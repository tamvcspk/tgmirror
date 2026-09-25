"""``engine/backupdir.py``: the backup directory format, no Telegram/network involved."""

from datetime import UTC, datetime

from tgmirror.core.gateway import ChatKind, ExportedMedia, ExportedMessage, MediaKind
from tgmirror.engine import backupdir

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_manifest_round_trips(tmp_path) -> None:
    manifest = backupdir.BackupManifest(
        format_version=1,
        tgmirror_version="0.1.0",
        src_id=-1001,
        src_title="My channel",
        src_kind=ChatKind.FORUM,
        src_about="about",
        src_noforwards=True,
        protected_ack=True,
        filters_json="{}",
        topics=(backupdir.BackupTopic(1, "General"), backupdir.BackupTopic(2, "News")),
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
    )
    backupdir.write_manifest(tmp_path, manifest)
    back = backupdir.read_manifest(tmp_path)
    assert back == manifest


def test_read_manifest_missing_is_none(tmp_path) -> None:
    assert backupdir.read_manifest(tmp_path) is None


def test_messages_jsonl_round_trips(tmp_path) -> None:
    text_msg = ExportedMessage(
        id=1, date=NOW, grouped_id=None, topic_id=None, from_user_id=7, text_html="hi", views=3
    )
    photo_msg = ExportedMessage(
        id=2,
        date=NOW,
        grouped_id=100,
        topic_id=5,
        from_user_id=None,
        text_html="<b>caption</b>",
        views=None,
        media=ExportedMedia(kind=MediaKind.PHOTO, filename="2.jpg", mime="image/jpeg", size=1234),
    )
    poll_msg = ExportedMessage(
        id=3,
        date=NOW,
        grouped_id=None,
        topic_id=None,
        from_user_id=None,
        text_html="",
        views=None,
        media=ExportedMedia(
            kind=MediaKind.POLL,
            poll_question="Favourite colour?",
            poll_options=("Red", "Blue"),
            poll_quiz=True,
            poll_correct_option=1,
        ),
    )
    backupdir.append_records(tmp_path, [text_msg, photo_msg])
    backupdir.append_records(tmp_path, [poll_msg])

    records = backupdir.iter_records(tmp_path)
    assert records == [text_msg, photo_msg, poll_msg]
    assert backupdir.last_id(tmp_path) == 3


def test_iter_records_no_file_is_empty(tmp_path) -> None:
    assert backupdir.iter_records(tmp_path) == []
    assert backupdir.last_id(tmp_path) == 0


def test_iter_records_drops_incomplete_last_line(tmp_path) -> None:
    """A crash mid-``write`` leaves at most the last line broken; the reader must not trust it
    (or anything doubtfully placed after it), and a resume simply fetches that message again."""
    msg = ExportedMessage(
        id=1,
        date=NOW,
        grouped_id=None,
        topic_id=None,
        from_user_id=None,
        text_html="ok",
        views=None,
    )
    backupdir.append_records(tmp_path, [msg])
    with backupdir.messages_path(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write('{"id": 2, "date": "2026-0')  # cut off mid-write, no trailing newline

    records = backupdir.iter_records(tmp_path)
    assert records == [msg]
    assert backupdir.last_id(tmp_path) == 1


def test_media_dict_omits_defaults_but_keeps_kind(tmp_path) -> None:
    msg = ExportedMessage(
        id=1,
        date=NOW,
        grouped_id=None,
        topic_id=None,
        from_user_id=None,
        text_html="",
        views=None,
        media=ExportedMedia(kind=MediaKind.CONTACT, contact_phone="+1", contact_first_name="A"),
    )
    backupdir.append_records(tmp_path, [msg])
    line = backupdir.messages_path(tmp_path).read_text(encoding="utf-8").splitlines()[0]
    assert '"kind": "contact"' in line
    assert "poll_options" not in line  # empty tuple: not written
    assert "geo_lat" not in line  # None: not written

    back = backupdir.iter_records(tmp_path)[0]
    assert back.media == msg.media


def test_manifest_path_helpers(tmp_path) -> None:
    assert backupdir.manifest_path(tmp_path) == tmp_path / "backup.json"
    assert backupdir.messages_path(tmp_path) == tmp_path / "messages.jsonl"
    assert backupdir.media_dir(tmp_path) == tmp_path / "media"
