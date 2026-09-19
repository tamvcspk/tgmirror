"""Units and batches: albums are never split (hard rule 4)."""

from collections.abc import AsyncIterator

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.gateway import MediaKind, Unit
from tgmirror.engine import planner
from tgmirror.engine.batcher import Batch, batches

PHOTO, VIDEO = MediaKind.PHOTO, MediaKind.VIDEO


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
