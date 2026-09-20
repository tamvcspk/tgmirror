from datetime import UTC, datetime

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.errors import FloodWait, ForwardsRestricted, NoPermission, PeerFlood
from tgmirror.core.gateway import ALBUM_MARGIN, MediaKind, ServerFilter, TelegramGateway, Unit


async def collect(gateway: FakeGateway, src: int, **kwargs):
    return [m async for m in gateway.iter_messages(src, **kwargs)]


def test_fake_satisfies_protocol(gateway: FakeGateway) -> None:
    assert isinstance(gateway, TelegramGateway)


async def test_iter_messages_ascending_after_min_id(gateway: FakeGateway) -> None:
    src = gateway.add_channel("src")
    for i in range(5):
        gateway.add_message(src.id, f"m{i}")

    assert [m.id for m in await collect(gateway, src.id)] == [1, 2, 3, 4, 5]
    assert [m.id for m in await collect(gateway, src.id, min_id=3)] == [4, 5]


async def test_iter_messages_server_filter(gateway: FakeGateway) -> None:
    src = gateway.add_channel("src")
    gateway.add_message(
        src.id, "hello #news", media=MediaKind.PHOTO, date=datetime(2023, 5, 1, tzinfo=UTC)
    )
    gateway.add_message(
        src.id, "video #news", media=MediaKind.VIDEO, date=datetime(2024, 5, 1, tzinfo=UTC)
    )
    gateway.add_message(src.id, "plain", date=datetime(2024, 6, 1, tzinfo=UTC))

    only_video = await collect(gateway, src.id, filters=ServerFilter(media=MediaKind.VIDEO))
    assert [m.id for m in only_video] == [2]

    searched = await collect(gateway, src.id, filters=ServerFilter(search="#NEWS"))
    assert [m.id for m in searched] == [1, 2]

    bounded = await collect(gateway, src.id, filters=ServerFilter(max_id=2))
    assert [m.id for m in bounded] == [1, 2]


async def test_date_bounds_are_positions_with_an_album_margin(gateway: FakeGateway) -> None:
    """Like the real gateway: the server may return up to ALBUM_MARGIN ids more, never fewer."""
    src = gateway.add_channel("src")
    for day in range(1, 31):  # ids 1..30, one per day of June 2024
        gateway.add_message(src.id, f"d{day}", date=datetime(2024, 6, day, tzinfo=UTC))
    june_15, june_20 = datetime(2024, 6, 15, tzinfo=UTC), datetime(2024, 6, 20, tzinfo=UTC)

    window = await collect(gateway, src.id, filters=ServerFilter(since=june_15, until=june_20))
    ids = [m.id for m in window]

    assert set(range(15, 20)) <= set(ids)  # everything in [15th, 20th)
    assert ids[0] == 15 - ALBUM_MARGIN and ids[-1] == 20 + ALBUM_MARGIN
    assert (
        await collect(gateway, src.id, filters=ServerFilter(since=datetime(2025, 1, 1, tzinfo=UTC)))
        == []
    )


async def test_album_messages_share_grouped_id(gateway: FakeGateway) -> None:
    src = gateway.add_channel("src")
    album = gateway.add_album(src.id, [MediaKind.PHOTO, MediaKind.PHOTO, MediaKind.VIDEO], "cap")

    assert len({m.grouped_id for m in album}) == 1
    assert album[0].grouped_id is not None
    assert [m.text for m in album] == ["cap", "", ""]
    assert Unit(tuple(album)).is_album


async def test_copy_messages_result_aligned_and_albums_stay_grouped(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src"), gateway.add_channel("dst")
    single = gateway.add_message(src.id, "single")
    album = gateway.add_album(src.id, [MediaKind.PHOTO, MediaKind.PHOTO], "album")
    ids = [single.id, *(m.id for m in album)]

    result = await gateway.copy_messages(src.id, dst.id, ids)

    assert all(isinstance(i, int) for i in result)
    copied = await collect(gateway, dst.id)
    assert [m.id for m in copied] == result
    assert [m.text for m in copied] == ["single", "album", ""]
    assert copied[0].grouped_id is None
    assert copied[1].grouped_id is not None
    assert copied[1].grouped_id == copied[2].grouped_id
    assert copied[1].grouped_id != album[0].grouped_id


async def test_copy_messages_missing_source_id_is_none(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src"), gateway.add_channel("dst")
    msg = gateway.add_message(src.id, "x")

    assert await gateway.copy_messages(src.id, dst.id, [msg.id, 999]) == [1, None]


async def test_copy_messages_rejects_bad_batch_sizes(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src"), gateway.add_channel("dst")

    with pytest.raises(ValueError):
        await gateway.copy_messages(src.id, dst.id, [])
    with pytest.raises(ValueError):
        await gateway.copy_messages(src.id, dst.id, list(range(101)))


async def test_copy_from_noforwards_source_raises(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src", noforwards=True), gateway.add_channel("dst")
    msg = gateway.add_message(src.id, "x")

    with pytest.raises(ForwardsRestricted):
        await gateway.copy_messages(src.id, dst.id, [msg.id])
    assert gateway.messages[dst.id] == []


async def test_copy_to_read_only_destination_raises(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src"), gateway.add_channel("dst", can_post=False)
    msg = gateway.add_message(src.id, "x")

    with pytest.raises(NoPermission):
        await gateway.copy_messages(src.id, dst.id, [msg.id])


async def test_scripted_failures_fire_once_then_clear(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src"), gateway.add_channel("dst")
    msg = gateway.add_message(src.id, "x")
    gateway.fail_next("copy_messages", FloodWait(30))

    with pytest.raises(FloodWait) as exc:
        await gateway.copy_messages(src.id, dst.id, [msg.id])
    assert exc.value.seconds == 30
    assert gateway.messages[dst.id] == []  # failed before any side effect

    assert await gateway.copy_messages(src.id, dst.id, [msg.id]) == [1]
    assert len(gateway.calls_to("copy_messages")) == 2


async def test_scripted_peer_flood(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src"), gateway.add_channel("dst")
    gateway.fail_next("copy_messages", PeerFlood(), times=2)

    for _ in range(2):
        with pytest.raises(PeerFlood):
            await gateway.copy_messages(src.id, dst.id, [1])


async def test_create_and_list_channels(gateway: FakeGateway) -> None:
    gateway.add_channel("joined", can_post=False, is_admin=False)
    created = await gateway.create_channel("clone", about="copy")

    assert created.is_admin and created.can_post
    assert await gateway.get_channel(created.id) == created
    assert [c.title for c in await gateway.list_channels()] == ["joined", "clone"]


async def test_unknown_channel_is_no_permission(gateway: FakeGateway) -> None:
    with pytest.raises(NoPermission):
        await gateway.get_channel(-1)
