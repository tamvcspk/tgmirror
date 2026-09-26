"""``engine/backup_reader.py``: ``BackupReader`` reads a backup directory as a ``MessageReader``,
no gateway or Telegram involved (phase 11b, restore)."""

from datetime import UTC, datetime

from tgmirror.core.gateway import (
    ChatKind,
    ExportedMedia,
    ExportedMessage,
    FromBackup,
    MediaKind,
    ServerFilter,
    Unit,
)
from tgmirror.engine import backupdir
from tgmirror.engine.backup_reader import BackupReader

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _manifest(*, topics=()) -> backupdir.BackupManifest:
    return backupdir.BackupManifest(
        format_version=1,
        tgmirror_version="0.1.0",
        src_id=-1001,
        src_title="My channel",
        src_kind=ChatKind.BROADCAST,
        src_about="",
        src_noforwards=False,
        protected_ack=False,
        filters_json="{}",
        topics=topics,
    )


def _seed(tmp_path, records: list[ExportedMessage]) -> None:
    backupdir.append_records(tmp_path, records)


async def test_iter_messages_ascending_and_excludes_min_id(tmp_path) -> None:
    records = [
        ExportedMessage(1, NOW, None, None, None, "hello #News", None),
        ExportedMessage(2, NOW, None, None, None, "world", None),
        ExportedMessage(3, NOW, None, None, None, "again", None),
    ]
    _seed(tmp_path, records)
    reader = BackupReader(tmp_path, _manifest())

    got = [m async for m in reader.iter_messages(-1001, min_id=1)]
    assert [m.id for m in got] == [2, 3]
    assert got[0].text == "world"


async def test_hashtag_is_approximated_from_plain_text(tmp_path) -> None:
    records = [ExportedMessage(1, NOW, None, None, None, "look at <b>#News</b> today", None)]
    _seed(tmp_path, records)
    reader = BackupReader(tmp_path, _manifest())

    got = [m async for m in reader.iter_messages(-1001)]
    assert got[0].text == "look at #News today"
    assert got[0].hashtags == ("#news",)


async def test_max_id_and_date_bounds_narrow(tmp_path) -> None:
    records = [
        ExportedMessage(1, datetime(2026, 1, 1, tzinfo=UTC), None, None, None, "a", None),
        ExportedMessage(2, datetime(2026, 1, 2, tzinfo=UTC), None, None, None, "b", None),
        ExportedMessage(3, datetime(2026, 1, 3, tzinfo=UTC), None, None, None, "c", None),
    ]
    _seed(tmp_path, records)
    reader = BackupReader(tmp_path, _manifest())

    async def ids(filters: ServerFilter) -> list[int]:
        return [m.id async for m in reader.iter_messages(-1001, filters=filters)]

    assert await ids(ServerFilter(max_id=2)) == [1, 2]
    assert await ids(ServerFilter(since=datetime(2026, 1, 2, tzinfo=UTC))) == [2, 3]
    assert await ids(ServerFilter(until=datetime(2026, 1, 2, tzinfo=UTC))) == [1]


async def test_count_matches_iter_messages(tmp_path) -> None:
    records = [ExportedMessage(i, NOW, None, None, None, str(i), None) for i in range(1, 6)]
    _seed(tmp_path, records)
    reader = BackupReader(tmp_path, _manifest())
    assert await reader.count(-1001, min_id=2) == 3


async def test_get_messages_returns_only_present_ids_ascending(tmp_path) -> None:
    records = [ExportedMessage(i, NOW, None, None, None, str(i), None) for i in (1, 2, 3)]
    _seed(tmp_path, records)
    reader = BackupReader(tmp_path, _manifest())
    got = await reader.get_messages(-1001, [3, 1, 99])
    assert [m.id for m in got] == [1, 3]


async def test_list_topics_from_manifest(tmp_path) -> None:
    topics = (backupdir.BackupTopic(1, "General"), backupdir.BackupTopic(2, "News"))
    reader = BackupReader(tmp_path, _manifest(topics=topics))
    got = await reader.list_topics(-1001)
    assert [(t.id, t.title) for t in got] == [(1, "General"), (2, "News")]


async def test_prepare_returns_no_files_and_a_from_backup_handle(tmp_path) -> None:
    record = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "caption",
        None,
        media=ExportedMedia(kind=MediaKind.PHOTO, filename="1.jpg", mime="image/jpeg", size=10),
    )
    _seed(tmp_path, [record])
    reader = BackupReader(tmp_path, _manifest())
    src_msg = (await reader.get_messages(-1001, [1]))[0]
    unit = Unit((src_msg,))

    prepared = await reader.prepare(-1001, unit, tmp_path / "tmp")
    assert prepared.files == ()  # nothing to delete: the media stays in the backup (decision 1)
    assert isinstance(prepared.handle, FromBackup)
    assert prepared.handle.messages == (record,)
    assert prepared.handle.media_dir == backupdir.media_dir(tmp_path)
    assert prepared.handle.src_id == -1001

    fetched = await reader.fetch(-1001, unit)
    assert fetched == prepared


async def test_quiz_with_unknown_correct_option_is_unanswered(tmp_path) -> None:
    quiz = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(
            kind=MediaKind.POLL, poll_question="2+2?", poll_options=("3", "4"), poll_quiz=True
        ),
    )
    answered = ExportedMessage(
        2,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(
            kind=MediaKind.POLL,
            poll_question="2+2?",
            poll_options=("3", "4"),
            poll_quiz=True,
            poll_correct_option=1,
        ),
    )
    plain_poll = ExportedMessage(
        3,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.POLL, poll_question="colour?", poll_options=("r", "b")),
    )
    _seed(tmp_path, [quiz, answered, plain_poll])
    reader = BackupReader(tmp_path, _manifest())
    got = {m.id: m for m in await reader.get_messages(-1001, [1, 2, 3])}
    assert got[1].quiz_unanswered is True
    assert got[2].quiz_unanswered is False
    assert got[3].quiz_unanswered is False  # a plain poll never needs a correct answer
