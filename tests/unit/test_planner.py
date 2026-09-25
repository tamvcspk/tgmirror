"""Units and batches: albums are never split (hard rule 4)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.gateway import (
    ALBUM_MARGIN,
    MAX_IDS_PER_CALL,
    ChatKind,
    MediaKind,
    ServerFilter,
    SrcMessage,
    Unit,
)
from tgmirror.engine import planner
from tgmirror.engine.batcher import Batch, batches
from tgmirror.engine.planner import Skip
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec

PHOTO, VIDEO = MediaKind.PHOTO, MediaKind.VIDEO
DAY = datetime(2024, 1, 1, tzinfo=UTC)


async def collect_units(gw: FakeGateway, src: int, min_id: int = 0) -> list[list[int]]:
    return [u.ids async for u in planner.units(gw, src, min_id=min_id)]


async def test_singles_and_albums_become_units_in_order(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "a")  # 1
    gateway.add_album(src, [PHOTO, PHOTO, VIDEO])  # 2 3 4
    gateway.add_message(src, "b")  # 5
    gateway.add_album(src, [PHOTO, PHOTO])  # 6 7 (album is the last thing in the stream)

    assert await collect_units(gateway, src) == [[1], [2, 3, 4], [5], [6, 7]]


async def test_two_albums_in_a_row_stay_two_units(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_album(src, [PHOTO, PHOTO])
    gateway.add_album(src, [PHOTO, PHOTO])

    assert await collect_units(gateway, src) == [[1, 2], [3, 4]]


async def test_service_messages_are_never_cloned_and_do_not_split_nothing(
    gateway: FakeGateway,
) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "a")
    gateway.add_message(src, is_service=True)
    gateway.add_message(src, "b")

    assert await collect_units(gateway, src) == [[1], [3]]


async def test_min_id_is_exclusive(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    for text in "abcd":
        gateway.add_message(src, text)

    assert await collect_units(gateway, src, min_id=2) == [[3], [4]]


async def test_an_empty_source_yields_nothing(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id

    assert await collect_units(gateway, src) == []


# ---- batcher --------------------------------------------------------------------------------


async def stream(gw: FakeGateway, src: int) -> AsyncIterator[Unit]:
    async for u in planner.units(gw, src):
        yield u


async def sizes(gw: FakeGateway, src: int, batch_size: int) -> list[list[list[int]]]:
    return [[u.ids for u in b.units] async for b in batches(stream(gw, src), batch_size)]


async def test_batches_fill_up_to_the_batch_size(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    for _ in range(5):
        gateway.add_message(src, "x")

    assert await sizes(gateway, src, 2) == [[[1], [2]], [[3], [4]], [[5]]]


async def test_an_album_is_never_split_across_batches(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "a")  # 1
    gateway.add_message(src, "b")  # 2
    gateway.add_album(src, [PHOTO, PHOTO, PHOTO])  # 3 4 5: does not fit after two singles
    gateway.add_message(src, "c")  # 6

    result = await sizes(gateway, src, 4)

    assert result == [[[1], [2]], [[3, 4, 5], [6]]]


async def test_a_batch_cuts_when_the_source_topic_changes(gateway: FakeGateway) -> None:
    """Phase 8: one forward/send call targets one destination topic, so a batch never mixes
    source topics even when it would otherwise still have room (docs/01-kien-truc.md, "Ánh xạ
    topic")."""
    src = gateway.add_channel("S", kind=ChatKind.FORUM).id
    gateway.add_message(src, "a", topic_id=1)  # 1
    gateway.add_message(src, "b", topic_id=1)  # 2
    gateway.add_message(src, "c", topic_id=7)  # 3: a different topic ends the batch
    gateway.add_message(src, "d", topic_id=7)  # 4: joins the new batch

    result = await sizes(gateway, src, 10)

    assert result == [[[1], [2]], [[3], [4]]]
    batches_seen = [b async for b in batches(stream(gateway, src), 10)]
    assert [b.topic_id for b in batches_seen] == [1, 7]


async def test_a_non_forum_stream_never_cuts_on_topic(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    for _ in range(3):
        gateway.add_message(src, "x")

    (batch,) = [b async for b in batches(stream(gateway, src), 10)]

    assert batch.topic_id is None and len(batch.units) == 3


async def test_an_album_larger_than_the_batch_goes_out_whole(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "a")
    gateway.add_album(src, [PHOTO] * 5)
    gateway.add_message(src, "b")

    assert await sizes(gateway, src, 3) == [[[1]], [[2, 3, 4, 5, 6]], [[7]]]


async def test_batch_properties(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "a")
    gateway.add_album(src, [PHOTO, VIDEO])

    (batch,) = [b async for b in batches(stream(gateway, src), 10)]

    assert isinstance(batch, Batch)
    assert (batch.ids, batch.size, batch.last_id) == ([1, 2, 3], 3, 3)


@pytest.mark.parametrize("batch_size", [1, 2, 3, 7, 100])
async def test_batching_never_loses_or_reorders_messages(
    gateway: FakeGateway, batch_size: int
) -> None:
    src = gateway.add_channel("S").id
    for i in range(6):
        gateway.add_message(src, f"m{i}")
        if i % 2:
            gateway.add_album(src, [PHOTO, PHOTO])

    flat = [i for batch in await sizes(gateway, src, batch_size) for unit in batch for i in unit]

    assert flat == [m.id for m in gateway.messages[src]]


# ---- filters: skips and album completion ----------------------------------------------------


def matcher_for(data: dict) -> Matcher:
    return Matcher(FilterSpec.from_data(data))


async def items(gw: FakeGateway, src: int, data: dict, **kw: object) -> list[Unit | Skip]:
    return [i async for i in planner.units(gw, src, matcher=matcher_for(data), **kw)]  # type: ignore[arg-type]


def shape(found: list[Unit | Skip]) -> list[list[int] | tuple[int, int]]:
    return [i.ids if isinstance(i, Unit) else (i.count, i.last_id) for i in found]


async def test_units_that_fail_the_filter_become_skips_in_order(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "keep", hashtags=("#k",))  # 1
    gateway.add_album(src, [PHOTO, PHOTO, PHOTO], "album")  # 2 3 4: no hashtag, dropped whole
    gateway.add_message(src, is_service=True)  # 5: ignored, not even a skip
    gateway.add_message(src, "keep", hashtags=("#k",))  # 6
    gateway.add_message(src, "other")  # 7

    found = await items(gateway, src, {"include": [{"hashtag": ["#k"]}]})

    assert shape(found) == [[1], (3, 4), [6], (1, 7)]


async def test_without_a_matcher_nothing_is_ever_skipped(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "a")

    found = [i async for i in planner.units(gateway, src)]

    assert all(isinstance(i, Unit) for i in found) and len(found) == 1


async def test_a_server_filter_is_passed_to_the_gateway(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "x", media=VIDEO)
    gateway.add_message(src, "y", media=PHOTO)
    server = ServerFilter(media=VIDEO)

    found = await items(gateway, src, {"include": [{"media": ["video"]}]}, filters=server)

    assert shape(found) == [[1]]
    assert gateway.calls_to("iter_messages")[0].args[2] == server


async def test_albums_the_server_cut_are_completed_before_they_are_judged(
    gateway: FakeGateway,
) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "before")  # 1
    gateway.add_album(src, [PHOTO, VIDEO, PHOTO], "album")  # 2 3 4: the server keeps 2 and 4
    gateway.add_message(src, "after")  # 5
    data = {"include": [{"media": ["photo"]}]}

    found = await items(gateway, src, data, filters=ServerFilter(media=PHOTO), complete_albums=True)

    assert shape(found) == [[2, 3, 4]]  # the video member came back through the unfiltered read


async def test_without_completion_the_cut_album_would_be_split(gateway: FakeGateway) -> None:
    """The reason ``complete_albums`` exists: what the server returns is not a whole album."""
    src = gateway.add_channel("S").id
    gateway.add_album(src, [PHOTO, VIDEO, PHOTO])

    found = await items(gateway, src, {}, filters=ServerFilter(media=PHOTO))

    assert shape(found) == [[1, 3]]


async def test_completion_reads_only_a_small_window_around_the_album(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    for _ in range(50):
        gateway.add_message(src, "filler", media=VIDEO)
    gateway.add_album(src, [PHOTO, VIDEO], "album")  # 51 52

    await items(
        gateway,
        src,
        {},
        filters=ServerFilter(media=PHOTO),
        complete_albums=True,
    )

    window = gateway.calls_to("iter_messages")[1]  # the second read is the completion
    assert window.args[1] == 51 - 1 - ALBUM_MARGIN
    assert window.args[2] == ServerFilter(max_id=51 + ALBUM_MARGIN)


# ---- batcher: skips ride along with the next batch ------------------------------------------


def unit_of(*ids: int) -> Unit:
    gid = 900 + ids[0] if len(ids) > 1 else None
    return Unit(tuple(SrcMessage(id=i, date=DAY, grouped_id=gid) for i in ids))


async def feed(*entries: Unit | Skip) -> AsyncIterator[Unit | Skip]:
    for entry in entries:
        yield entry


async def batched(entries: list[Unit | Skip], size: int, **kw: int) -> list[Batch]:
    return [b async for b in batches(feed(*entries), size, **kw)]


async def test_skips_are_credited_to_the_batch_and_move_its_cursor() -> None:
    (batch,) = await batched([Skip(3, 3), unit_of(4), Skip(2, 6), unit_of(7)], 10)

    assert (batch.ids, batch.skipped, batch.upto, batch.last_id) == ([4, 7], 5, 6, 7)


async def test_skips_before_the_unit_that_did_not_fit_go_with_the_full_batch() -> None:
    first, second = await batched([unit_of(1), Skip(2, 3), unit_of(4)], 1)

    assert (first.ids, first.skipped, first.last_id) == ([1], 2, 3)  # the cursor may pass 2..3
    assert (second.ids, second.skipped, second.last_id) == ([4], 0, 4)


async def test_a_long_stretch_of_skips_flushes_a_batch_that_is_waiting() -> None:
    first, second = await batched(
        [unit_of(1), Skip(3, 4), Skip(3, 7), unit_of(8)], 10, flush_after=5
    )

    assert (first.ids, first.skipped, first.last_id) == ([1], 6, 7)
    assert second.ids == [8]


async def test_only_skips_make_a_progress_only_batch() -> None:
    flushed, tail = await batched([Skip(4, 4), Skip(3, 9), Skip(2, 12)], 10, flush_after=5)

    assert (flushed.units, flushed.skipped, flushed.last_id) == ((), 7, 9)
    assert (tail.units, tail.skipped, tail.last_id, tail.ids, tail.size) == ((), 2, 12, [], 0)


async def test_no_skips_and_no_units_make_no_batches() -> None:
    assert await batched([], 10) == []


# ---- retry: read the failed messages by id ---------------------------------------------------


async def failed_items(
    gw: FakeGateway, src: int, ids: list[int]
) -> list[list[int] | tuple[str, ...]]:
    """Units as id lists; ``Gone`` as ``("gone", ids...)`` so one list shows the order."""
    out: list[list[int] | tuple[str, ...]] = []
    async for item in planner.failed_units(gw, src, ids):
        out.append(("gone", *map(str, item.ids)) if isinstance(item, planner.Gone) else item.ids)
    return out


async def test_failed_units_are_read_by_id_and_albums_stay_whole(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_message(src, "a")  # 1
    gateway.add_album(src, [PHOTO, PHOTO, VIDEO])  # 2 3 4
    gateway.add_message(src, "b")  # 5
    gateway.add_album(src, [PHOTO, PHOTO])  # 6 7

    got = await failed_items(gateway, src, [1, 2, 3, 4, 6, 7])

    assert got == [[1], [2, 3, 4], [6, 7]]
    assert [c.method for c in gateway.calls] == ["get_messages"]  # no scan of the source


async def test_only_the_failed_members_of_an_album_make_the_unit(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_album(src, [PHOTO, PHOTO, PHOTO])  # 1 2 3, the middle one was copied

    assert await failed_items(gateway, src, [1, 3]) == [[1, 3]]


async def test_two_albums_in_a_row_stay_two_units_when_read_by_id(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    gateway.add_album(src, [PHOTO, PHOTO])
    gateway.add_album(src, [PHOTO, PHOTO])

    assert await failed_items(gateway, src, [1, 2, 3, 4]) == [[1, 2], [3, 4]]


async def test_an_album_across_two_reads_is_still_one_unit(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    for _ in range(MAX_IDS_PER_CALL - 1):
        gateway.add_message(src, "x")
    gateway.add_album(src, [PHOTO, PHOTO, PHOTO])  # ids 100 101 102: split by the 100-id read

    got = await failed_items(gateway, src, list(range(1, 103)))

    assert got[-1] == [100, 101, 102]
    assert len(got) == MAX_IDS_PER_CALL - 1 + 1
    assert len(gateway.calls_to("get_messages")) == 2


async def test_ids_the_source_no_longer_has_are_reported_as_gone(gateway: FakeGateway) -> None:
    src = gateway.add_channel("S").id
    for text in "abcd":
        gateway.add_message(src, text)
    gateway.delete_message(src, 2)

    assert await failed_items(gateway, src, [1, 2, 4]) == [("gone", "2"), [1], [4]]
