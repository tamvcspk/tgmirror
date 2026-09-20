"""Telethon boundary: reducing messages, reading history (with server filters), forwarding.

No network: a stub client records what the gateway asks of it.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from telethon import errors, types
from telethon.tl import custom

from tgmirror.core.errors import (
    FloodWait,
    ForwardsRestricted,
    NoPermission,
    PeerFlood,
    PerMessage,
)
from tgmirror.core.gateway import ALBUM_MARGIN, MediaKind, ServerFilter, SrcMessage
from tgmirror.core.telethon_gateway import (
    _MEDIA_FILTERS,
    READ_WAIT,
    TelethonGateway,
    map_exception,
    media_kind,
    src_message,
)
from tgmirror.filters.pushdown import PUSHABLE_MEDIA

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def bare(cls: type) -> Any:
    """An instance for ``isinstance`` checks only: media kinds whose fields we never read."""
    return cls.__new__(cls)


def document(*attributes: Any) -> types.MessageMediaDocument:
    doc = types.Document(
        id=1,
        access_hash=1,
        file_reference=b"",
        date=NOW,
        mime_type="application/octet-stream",
        size=1,
        dc_id=1,
        attributes=list(attributes),
    )
    return types.MessageMediaDocument(document=doc)


def message(msg_id: int = 1, *, media: Any = None, action: Any = None, **kw: Any) -> Any:
    return custom.Message(
        id=msg_id, peer_id=types.PeerChannel(1), date=NOW, media=media, action=action, **kw
    )


VIDEO = types.DocumentAttributeVideo(duration=1, w=1, h=1)
ROUND = types.DocumentAttributeVideo(duration=1, w=1, h=1, round_message=True)
PHOTO = types.MessageMediaPhoto(
    photo=types.Photo(id=1, access_hash=1, file_reference=b"", date=NOW, sizes=[], dc_id=1)
)
STICKER = types.DocumentAttributeSticker(alt="x", stickerset=types.InputStickerSetEmpty())


@pytest.mark.parametrize(
    ("media", "kind"),
    [
        (None, MediaKind.TEXT),
        (PHOTO, MediaKind.PHOTO),
        (document(VIDEO), MediaKind.VIDEO),
        (document(VIDEO, types.DocumentAttributeAnimated()), MediaKind.GIF),  # a GIF is a video too
        (document(ROUND), MediaKind.VIDEO_NOTE),
        (document(types.DocumentAttributeAudio(duration=1, voice=True)), MediaKind.VOICE),
        (document(types.DocumentAttributeAudio(duration=1)), MediaKind.AUDIO),
        (document(STICKER), MediaKind.STICKER),
        (document(types.DocumentAttributeFilename("a.zip")), MediaKind.DOCUMENT),
        (bare(types.MessageMediaPoll), MediaKind.POLL),
        (bare(types.MessageMediaWebPage), MediaKind.WEBPAGE),
        (bare(types.MessageMediaGeo), MediaKind.GEO),
        (bare(types.MessageMediaVenue), MediaKind.GEO),
        (bare(types.MessageMediaContact), MediaKind.CONTACT),
        (bare(types.MessageMediaGame), MediaKind.GAME),
        (bare(types.MessageMediaInvoice), MediaKind.INVOICE),
        (bare(types.MessageMediaDice), MediaKind.DOCUMENT),  # no filter name: a generic attachment
    ],
)
def test_media_kind(media: Any, kind: MediaKind) -> None:
    assert media_kind(message(media=media)) is kind


def test_src_message_reduces_a_telethon_message() -> None:
    reduced = src_message(message(7, message="hello", grouped_id=99))

    assert reduced == SrcMessage(
        id=7, date=NOW, text="hello", media=MediaKind.TEXT, grouped_id=99, is_service=False
    )


def test_service_messages_are_flagged_and_non_messages_dropped() -> None:
    pinned = src_message(message(3, action=types.MessageActionPinMessage()))

    assert pinned is not None and pinned.is_service and pinned.text == ""
    assert src_message(types.MessageEmpty(id=4, peer_id=types.PeerChannel(1))) is None
    assert src_message(None) is None


def test_message_id_invalid_maps_to_per_message() -> None:
    mapped = map_exception(errors.MessageIdInvalidError(None))

    assert isinstance(mapped, PerMessage) and mapped.reason == "MESSAGE_ID_INVALID"


class HistoryClient:
    """The reading and forwarding calls of ``TelegramClient``, recorded."""

    def __init__(self, history: list[Any] | None = None) -> None:
        self.history = history or []
        self.iter_calls: list[dict[str, Any]] = []
        self.forwards: list[tuple[Any, list[int], dict[str, Any]]] = []
        self.forward_result: list[Any] = []
        self.raises: BaseException | None = None
        self.unknown: set[int] = set()
        self.after: list[list[Any]] = []  # answers to "first message after <date>", in order
        self.lookups: list[dict[str, Any]] = []

    async def get_input_entity(self, ref: int) -> str:
        if ref in self.unknown:
            raise ValueError("Could not find the input entity")
        return f"peer:{ref}"

    async def iter_messages(self, peer: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self.iter_calls.append({"peer": peer, **kwargs})
        for m in self.history:
            yield m
        if self.raises:
            raise self.raises

    async def forward_messages(self, to_peer: Any, ids: Any, **kwargs: Any) -> list[Any]:
        self.forwards.append((to_peer, list(ids), kwargs))
        if self.raises:
            raise self.raises
        return self.forward_result

    async def get_messages(self, peer: Any, limit: int, **kwargs: Any) -> list[Any]:
        if "offset_date" in kwargs:
            self.lookups.append({"limit": limit, **kwargs})
            return self.after.pop(0)
        return self.history[-limit:]


def gateway_on(client: HistoryClient) -> TelethonGateway:
    return TelethonGateway(client)  # type: ignore[arg-type]


async def test_iter_messages_reads_oldest_first_after_min_id() -> None:
    client = HistoryClient(
        [
            message(5, message="a"),
            message(6, action=types.MessageActionPinMessage()),
            types.MessageEmpty(id=7, peer_id=types.PeerChannel(1)),
        ]
    )

    got = [m async for m in gateway_on(client).iter_messages(-1001, min_id=4)]

    assert [(m.id, m.is_service) for m in got] == [(5, False), (6, True)]  # empty ones dropped
    (call,) = client.iter_calls
    assert call["peer"] == "peer:-1001"
    assert (call["min_id"], call["reverse"], call["wait_time"]) == (4, True, READ_WAIT)


async def test_iter_messages_maps_errors_and_unknown_chats() -> None:
    client = HistoryClient([message(5)])
    client.raises = errors.FloodWaitError(None, capture=12)
    with pytest.raises(FloodWait):
        _ = [m async for m in gateway_on(client).iter_messages(-1001)]

    client.unknown.add(-1002)
    with pytest.raises(NoPermission):
        _ = [m async for m in gateway_on(client).iter_messages(-1002)]


def call_of(client: HistoryClient) -> dict[str, Any]:
    (call,) = client.iter_calls
    return call


async def read(client: HistoryClient, min_id: int = 0, **filters: Any) -> list[SrcMessage]:
    stream = gateway_on(client).iter_messages(-1001, min_id=min_id, filters=ServerFilter(**filters))
    return [m async for m in stream]


async def test_a_media_filter_and_a_search_become_telethon_arguments() -> None:
    client = HistoryClient()

    await read(client, media=MediaKind.PHOTO, search="#news")

    call = call_of(client)
    assert call["filter"] is types.InputMessagesFilterPhotos and call["search"] == "#news"
    assert "max_id" not in call and call["reverse"] is True


async def test_max_id_is_included_although_telethon_excludes_its_max_id() -> None:
    client = HistoryClient()

    await read(client, max_id=200)

    assert call_of(client)["max_id"] == 201


async def test_since_becomes_a_position_with_an_album_margin() -> None:
    client = HistoryClient([message(90)])
    client.after = [[message(80)]]  # the first message dated after (since - 1 second)
    since = datetime(2024, 1, 1, tzinfo=UTC)

    await read(client, since=since)

    (lookup,) = client.lookups
    assert lookup == {"limit": 1, "offset_date": since - timedelta(seconds=1), "reverse": True}
    assert call_of(client)["min_id"] == 80 - 1 - ALBUM_MARGIN


async def test_since_never_lowers_the_cursor() -> None:
    client = HistoryClient()
    client.after = [[message(80)]]

    await read(client, min_id=500, since=NOW)

    assert call_of(client)["min_id"] == 500


async def test_nothing_after_since_means_nothing_to_read() -> None:
    client = HistoryClient([message(5)])
    client.after = [[]]

    assert await read(client, since=NOW) == []
    assert client.iter_calls == []  # not even a history request


async def test_until_becomes_a_bound_with_a_margin_and_combines_with_max_id() -> None:
    client = HistoryClient()
    client.after = [[message(300)]]  # the first message dated after `until`

    await read(client, until=NOW)

    assert call_of(client)["max_id"] == 300 + ALBUM_MARGIN + 1
    assert client.lookups[0]["offset_date"] == NOW

    tighter = HistoryClient()
    tighter.after = [[message(300)]]
    await read(tighter, until=NOW, max_id=120)
    assert call_of(tighter)["max_id"] == 121  # the explicit bound is tighter


async def test_until_past_the_end_of_the_channel_adds_no_bound() -> None:
    client = HistoryClient()
    client.after = [[]]

    await read(client, until=NOW)

    assert "max_id" not in call_of(client)


async def test_errors_while_looking_up_a_date_are_mapped() -> None:
    client = HistoryClient()

    async def flood(*args: Any, **kwargs: Any) -> list[Any]:
        raise errors.FloodWaitError(None, capture=7)

    client.get_messages = flood  # type: ignore[method-assign]

    with pytest.raises(FloodWait):
        await read(client, since=NOW)


def test_every_pushable_media_kind_has_a_telegram_filter_and_only_those() -> None:
    """``filters.pushdown`` decides what is safe, the gateway knows how: they must agree."""
    assert set(_MEDIA_FILTERS) == set(PUSHABLE_MEDIA)


# ---- the fields the filters need ------------------------------------------------------------


def with_entities(text: str, *spans: tuple[int, int]) -> Any:
    entities = [types.MessageEntityHashtag(offset=o, length=n) for o, n in spans]
    return message(1, message=text, entities=entities)


def test_hashtags_come_from_entities_lower_cased() -> None:
    reduced = src_message(with_entities("Look #News and #Sport now", (5, 5), (15, 6)))

    assert reduced is not None and reduced.hashtags == ("#news", "#sport")


def test_a_hash_in_the_text_without_an_entity_is_not_a_hashtag() -> None:
    reduced = src_message(message(1, message="see #news"))

    assert reduced is not None and reduced.hashtags == ()


def test_a_hashtag_suffixed_with_its_channel_keeps_only_the_tag() -> None:
    reduced = src_message(with_entities("#tag@mychannel", (0, 14)))

    assert reduced is not None and reduced.hashtags == ("#tag",)


def test_hashtag_offsets_count_utf16_units_like_telegram_does() -> None:
    reduced = src_message(with_entities("😀 #tag", (3, 4)))  # the emoji is two UTF-16 units

    assert reduced is not None and reduced.hashtags == ("#tag",)


def test_file_details_come_from_the_document() -> None:
    doc = types.Document(
        id=1,
        access_hash=1,
        file_reference=b"",
        date=NOW,
        mime_type="video/mp4",
        size=5_000_000,
        dc_id=1,
        attributes=[types.DocumentAttributeVideo(duration=95, w=1, h=1)],
    )

    reduced = src_message(message(1, media=types.MessageMediaDocument(document=doc), views=1234))

    assert reduced is not None
    assert (reduced.size, reduced.duration, reduced.mime, reduced.views) == (
        5_000_000,
        95,
        "video/mp4",
        1234,
    )


def test_a_text_message_has_no_file_details() -> None:
    reduced = src_message(message(1, message="hi"))

    assert reduced is not None
    assert (reduced.size, reduced.duration, reduced.mime, reduced.views) == (None, None, None, None)


def photo_of(*sizes: int) -> types.Photo:
    return types.Photo(
        id=1,
        access_hash=1,
        file_reference=b"",
        date=NOW,
        sizes=[types.PhotoSize(type="x", w=1, h=1, size=n) for n in sizes],
        dc_id=1,
    )


def test_a_link_previews_picture_is_not_the_messages_file() -> None:
    page = types.WebPage(id=1, url="https://e.x", display_url="e.x", hash=0, photo=photo_of(999))

    reduced = src_message(message(1, media=types.MessageMediaWebPage(webpage=page)))

    assert reduced is not None and reduced.media is MediaKind.WEBPAGE
    assert reduced.size is None and reduced.mime is None


def test_a_photo_reports_its_largest_size() -> None:
    reduced = src_message(message(1, media=types.MessageMediaPhoto(photo=photo_of(100, 900))))

    assert reduced is not None and reduced.size == 900 and reduced.mime == "image/jpeg"


async def test_copy_messages_forwards_without_the_author_and_aligns_results() -> None:
    client = HistoryClient()
    client.forward_result = [message(100), None, message(102)]  # id 2 no longer exists

    result = await gateway_on(client).copy_messages(-1001, -1002, [1, 2, 3])

    assert result == [100, None, 102]
    ((to_peer, ids, kwargs),) = client.forwards
    assert (to_peer, ids) == ("peer:-1002", [1, 2, 3])
    assert kwargs == {"from_peer": "peer:-1001", "drop_author": True}


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (errors.MessageIdInvalidError(None), PerMessage),  # every id was gone: nothing created
        (errors.ChatForwardsRestrictedError(None), ForwardsRestricted),
        (errors.FloodWaitError(None, capture=9), FloodWait),
        (errors.PeerFloodError(None), PeerFlood),
        (errors.ChatWriteForbiddenError(None), NoPermission),
    ],
)
async def test_copy_messages_maps_errors(raised: BaseException, expected: type[Exception]) -> None:
    client = HistoryClient()
    client.raises = raised

    with pytest.raises(expected):
        await gateway_on(client).copy_messages(-1001, -1002, [1])


async def test_copy_to_a_chat_we_never_saw_is_no_permission() -> None:
    client = HistoryClient()
    client.unknown.add(-1002)

    with pytest.raises(NoPermission):
        await gateway_on(client).copy_messages(-1001, -1002, [1])


async def test_last_message_id() -> None:
    assert await gateway_on(HistoryClient([message(3), message(9)])).last_message_id(-1001) == 9
    assert await gateway_on(HistoryClient()).last_message_id(-1001) == 0
