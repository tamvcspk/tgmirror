"""The metadata index (phase 18b): the store, the update and reading back from it."""

import sqlite3
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.gateway import ChatKind, MediaKind, ServerFilter, SrcMessage
from tgmirror.engine import index as indexing
from tgmirror.engine import inspect as engine
from tgmirror.engine.index import IndexedSource, IndexReader
from tgmirror.store.index import INDEX_VERSION, MetadataIndex

WHEN = datetime(2026, 3, 1, 12, 30, tzinfo=UTC)


def full(message_id: int = 7) -> SrcMessage:
    """A message with every field set to something other than its default."""
    return SrcMessage(
        id=message_id,
        date=WHEN,
        text="hello #a",
        media=MediaKind.VIDEO,
        grouped_id=99,
        is_service=True,
        hashtags=("#a", "#b"),
        size=12345,
        duration=61.5,
        mime="video/mp4",
        views=10,
        quiz_unanswered=True,
        title="t",
        topic_id=5,
        from_user_id=42,
        is_forward=True,
        fwd_from_id=-1007,
        fwd_from_name="Origin",
        reply_to_id=3,
        edit_date=WHEN + timedelta(days=1),
        link_domains=("example.com", "t.me"),
        file_name="clip.mp4",
        file_key="d1",
        forwards=2,
        reactions=9,
        post_author="Ann",
        via_bot_id=11,
        pinned=True,
    )


def test_a_message_survives_the_index_with_every_field() -> None:
    message = full()

    assert indexing.decode(indexing.encode(message)) == message
    # a message with nothing set is as good: defaults are not special
    bare = SrcMessage(id=1, date=WHEN)
    assert indexing.decode(indexing.encode(bare)) == bare
    # the test above is only as good as the field list: a new field must be set in ``full``
    defaults = SrcMessage(id=1, date=WHEN - timedelta(days=1))
    unset = [
        f.name for f in fields(SrcMessage) if getattr(message, f.name) == getattr(defaults, f.name)
    ]
    assert unset == [], f"set these in full(): {unset}"


def test_a_field_the_index_no_longer_knows_is_dropped_not_fatal() -> None:
    text = indexing.encode(full()).replace('"pinned"', '"gone_field":1,"pinned"', 1)

    assert indexing.decode(text) == full()


async def test_the_store_keeps_a_prefix_and_moves_last_id_with_what_it_holds(
    tmp_path: Path,
) -> None:
    index = await MetadataIndex.open(tmp_path / "index" / "1.db")
    try:
        await index.put([(1, "a"), (2, "b")], last_id=4)  # 3 and 4 were service messages
        await index.put([(5, "c")], last_id=5)

        assert await index.last_id() == 5 and await index.count() == 3
        assert [d async for d in index.rows()] == ["a", "b", "c"]
        assert [d async for d in index.rows(min_id=2)] == ["c"]
        assert await index.rows_of([5, 1, 99]) == ["a", "c"]
        await index.put([], last_id=3)  # never moves backwards
        assert await index.last_id() == 5
    finally:
        await index.close()


async def test_a_refresh_drops_what_it_did_not_meet_and_only_when_it_finished(
    tmp_path: Path,
) -> None:
    index = await MetadataIndex.open(tmp_path / "1.db")
    try:
        await index.put([(1, "a"), (2, "b"), (3, "c")], last_id=3)

        await index.begin_refresh()
        await index.put([(1, "a2"), (3, "c2")], last_id=3, refreshing=True)
        assert await index.finish_refresh() == 1

        assert [d async for d in index.rows()] == ["a2", "c2"]
    finally:
        await index.close()


async def test_an_index_of_another_format_or_not_a_database_is_started_again(
    tmp_path: Path,
) -> None:
    old = tmp_path / "1.db"
    first = await MetadataIndex.open(old)
    await first.put([(1, "a")], last_id=1)
    await first.close()
    conn = sqlite3.connect(old)
    conn.execute(f"PRAGMA user_version = {INDEX_VERSION + 1}")
    conn.commit()
    conn.close()
    broken = tmp_path / "2.db"
    broken.write_bytes(b"this is not a database at all, just text" * 10)

    for path in (old, broken):
        index = await MetadataIndex.open(path)
        try:
            assert await index.count() == 0
        finally:
            await index.close()


# ---- update and read back ---------------------------------------------------------------------


def channel(gateway: FakeGateway, count: int = 6) -> tuple[int, IndexedSource]:
    src = gateway.add_channel("Source", username="srcchan")
    for i in range(1, count + 1):
        gateway.add_message(src.id, f"m{i}", hashtags=("#x",) if i % 2 else ())
    known = IndexedSource(src.id, "Source", "srcchan", ChatKind.BROADCAST, False, {})
    return src.id, known


async def test_the_first_update_reads_everything_and_the_next_only_what_is_newer(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    src, known = channel(gateway)
    index = await MetadataIndex.open(tmp_path / "i.db")
    try:
        first = await indexing.update(index, gateway, known)
        assert (first.read, first.complete) == (6, True)

        gateway.add_message(src, "m7")
        gateway.add_message(src, "m8")
        second = await indexing.update(index, gateway, known)

        assert (second.read, second.complete) == (2, True)
        assert [c.args[1] for c in gateway.calls_to("iter_messages")] == [0, 6]  # min_id each time
        assert await index.count() == 8
        state = await indexing.info(index)
        assert state.complete and state.last_id == 8 and state.source == known
    finally:
        await index.close()


async def test_a_stopped_update_keeps_what_it_read_and_the_next_one_goes_on(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    src, known = channel(gateway, 10)
    index = await MetadataIndex.open(tmp_path / "i.db")
    try:
        seen = 0

        def stop() -> bool:
            nonlocal seen
            seen += 1
            return seen >= 4

        cut = await indexing.update(index, gateway, known, stop=stop)

        assert (cut.read, cut.complete) == (4, False)
        assert await index.count() == 4 and await index.last_id() == 4
        assert not (await indexing.info(index)).complete
        rest = await indexing.update(index, gateway, known)
        assert (rest.read, rest.complete) == (6, True) and await index.count() == 10
    finally:
        await index.close()


async def test_a_refresh_reads_it_all_again_and_forgets_deleted_messages(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    src, known = channel(gateway)
    index = await MetadataIndex.open(tmp_path / "i.db")
    try:
        await indexing.update(index, gateway, known)
        del gateway.messages[src][1]  # message 2 is deleted at the source
        plain = await indexing.update(index, gateway, known)
        assert plain.read == 0 and await index.count() == 6  # a delta cannot see it

        refreshed = await indexing.update(index, gateway, known, refresh=True)

        assert (refreshed.read, refreshed.dropped) == (5, 1)
        assert await index.count() == 5
    finally:
        await index.close()


async def test_service_messages_move_last_id_but_are_not_kept(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    src, known = channel(gateway, 2)
    gateway.add_message(src, "joined", is_service=True)  # id 3
    index = await MetadataIndex.open(tmp_path / "i.db")
    try:
        await indexing.update(index, gateway, known)

        assert await index.count() == 2 and await index.last_id() == 3
        await indexing.update(index, gateway, known)
        assert [c.args[1] for c in gateway.calls_to("iter_messages")] == [0, 3]
    finally:
        await index.close()


async def test_a_report_from_the_index_is_the_report_of_the_live_scan(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    src, known = channel(gateway, 8)
    gateway.add_message(src, "clip", media=MediaKind.VIDEO, size=5, grouped_id=7)
    gateway.add_message(src, "clip2", media=MediaKind.VIDEO, size=6, grouped_id=7)
    index = await MetadataIndex.open(tmp_path / "i.db")
    try:
        await indexing.update(index, gateway, known)

        async def report(reader: object) -> engine.Report:
            inspector = engine.Inspector(by=engine.BY_CHOICES)
            await engine.scan(reader, src, inspector)  # type: ignore[arg-type]
            return inspector.report(top=0)

        assert await report(IndexReader(index, known)) == await report(gateway)
    finally:
        await index.close()


async def test_an_index_has_no_use_for_a_server_filter_or_a_download(
    tmp_path: Path,
) -> None:
    index = await MetadataIndex.open(tmp_path / "i.db")
    try:
        reader = IndexReader(index, IndexedSource(1, "t", None, ChatKind.BROADCAST, False, {}))
        with pytest.raises(ValueError):
            [m async for m in reader.iter_messages(1, filters=ServerFilter(media=MediaKind.VIDEO))]
        with pytest.raises(NotImplementedError):
            await reader.prepare()
    finally:
        await index.close()
