"""Telethon boundary, phase 2: reducing messages, reading history, forwarding. No network."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
from tgmirror.core.gateway import MediaKind, ServerFilter, SrcMessage
from tgmirror.core.telethon_gateway import (
    READ_WAIT,
    TelethonGateway,
    map_exception,
    media_kind,
    src_message,
)

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

    async def get_messages(self, peer: Any, limit: int) -> list[Any]:
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


def test_server_side_filters_are_not_implemented_before_phase_3() -> None:
    with pytest.raises(NotImplementedError):
        gateway_on(HistoryClient()).iter_messages(-1001, filters=ServerFilter(search="x"))


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
