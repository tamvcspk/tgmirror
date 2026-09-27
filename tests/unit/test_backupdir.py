"""``engine/backupdir.py``: the backup directory format, no Telegram/network involved."""

from datetime import UTC, datetime

import pytest

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


def test_iter_records_survives_unicode_line_separators_in_text(tmp_path) -> None:
    """N2, Phase 15b: a message's own text may legally contain U+2028/U+2029/U+0085 (Telegram
    text, not something tgmirror controls). ``json.dumps(ensure_ascii=False)`` does not escape
    them, so they land raw inside the JSON string; ``str.splitlines()`` (the old reader) treats
    them as line breaks too and cuts that message's JSON in half. Only ``\\n`` may end a line."""
    msgs = [
        ExportedMessage(
            id=1,
            date=NOW,
            grouped_id=None,
            topic_id=None,
            from_user_id=None,
            text_html="line one line two",
            views=None,
        ),
        ExportedMessage(
            id=2,
            date=NOW,
            grouped_id=None,
            topic_id=None,
            from_user_id=None,
            text_html="a b\u0085c",
            views=None,
        ),
        ExportedMessage(
            id=3,
            date=NOW,
            grouped_id=None,
            topic_id=None,
            from_user_id=None,
            text_html="ok",
            views=None,
        ),
    ]
    backupdir.append_records(tmp_path, msgs)

    records = backupdir.iter_records(tmp_path)

    assert records == msgs
    assert backupdir.last_id(tmp_path) == 3


def test_iter_records_raises_on_a_corrupt_line_in_the_middle(tmp_path) -> None:
    """Unlike a killed-mid-write *last* line (silently dropped, refetched), a broken line earlier
    in the file cannot mean an ordinary crash — continuing past it would silently lose everything
    written after it, so it raises instead (N3, Phase 15b)."""
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
    path = backupdir.messages_path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write('{"id": 3, "date": "2026-01-01T00:00:00+00:00", "text_html": "ok"}\n')

    with pytest.raises(backupdir.CorruptMessagesFile) as exc_info:
        backupdir.iter_records(tmp_path)
    assert exc_info.value.line_number == 2
    with pytest.raises(backupdir.CorruptMessagesFile):
        backupdir.last_id(tmp_path)


def test_repair_trailing_line_cuts_off_a_killed_mid_write(tmp_path) -> None:
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
    path = backupdir.messages_path(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": 2, "date": "2026-0')  # cut off mid-write, no trailing newline

    backupdir.repair_trailing_line(tmp_path)

    assert path.read_text(encoding="utf-8").count("\n") == 1
    assert backupdir.iter_records(tmp_path) == [msg]
    assert backupdir.last_id(tmp_path) == 1

    # the next append lands cleanly on its own line, not fused onto the removed fragment
    msg2 = ExportedMessage(
        id=2,
        date=NOW,
        grouped_id=None,
        topic_id=None,
        from_user_id=None,
        text_html="ok2",
        views=None,
    )
    backupdir.append_records(tmp_path, [msg2])
    assert backupdir.iter_records(tmp_path) == [msg, msg2]


def test_repair_trailing_line_is_a_noop_when_last_line_is_already_complete(tmp_path) -> None:
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
    before = backupdir.messages_path(tmp_path).read_text(encoding="utf-8")

    backupdir.repair_trailing_line(tmp_path)

    after = backupdir.messages_path(tmp_path).read_text(encoding="utf-8")
    assert after == before


def test_repair_trailing_line_is_a_noop_when_file_is_missing(tmp_path) -> None:
    backupdir.repair_trailing_line(tmp_path)  # must not raise
    assert not backupdir.messages_path(tmp_path).exists()


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


def test_original_filename_and_audio_tags_round_trip(tmp_path) -> None:
    """C2, Phase 15b: the new fields survive a write+read cycle like any other, via the same
    generic ``asdict``/``fields()`` machinery — no special-casing needed."""
    msg = ExportedMessage(
        id=1,
        date=NOW,
        grouped_id=None,
        topic_id=None,
        from_user_id=None,
        text_html="",
        views=None,
        media=ExportedMedia(
            kind=MediaKind.AUDIO,
            filename="1.mp3",
            mime="audio/mpeg",
            duration=180,
            original_filename="Track 05.mp3",
            audio_title="Song Title",
            audio_performer="Some Artist",
        ),
    )
    backupdir.append_records(tmp_path, [msg])

    back = backupdir.iter_records(tmp_path)[0]

    assert back.media == msg.media
    assert back.media is not None
    assert back.media.original_filename == "Track 05.mp3"
    assert (back.media.audio_title, back.media.audio_performer) == ("Song Title", "Some Artist")


def test_a_backup_without_the_new_fields_still_reads_fine(tmp_path) -> None:
    """A line written before C2 (Phase 15b) simply has no ``original_filename``/``audio_title``/
    ``audio_performer`` keys — reading it back must not choke, and the new fields default to
    ``None`` (``ExportedMedia``'s own default), not an error."""
    path = backupdir.messages_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        '{"id": 1, "date": "2026-01-01T00:00:00+00:00", "text_html": "", '
        '"media": {"kind": "audio", "filename": "1.mp3", "mime": "audio/mpeg"}}'
    )
    path.write_text(line + "\n", encoding="utf-8")

    back = backupdir.iter_records(tmp_path)[0]

    assert back.media is not None
    assert back.media.original_filename is None
    assert (back.media.audio_title, back.media.audio_performer) == (None, None)


def test_manifest_path_helpers(tmp_path) -> None:
    assert backupdir.manifest_path(tmp_path) == tmp_path / "backup.json"
    assert backupdir.messages_path(tmp_path) == tmp_path / "messages.jsonl"
    assert backupdir.media_dir(tmp_path) == tmp_path / "media"
