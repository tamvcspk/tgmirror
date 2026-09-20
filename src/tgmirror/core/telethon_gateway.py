"""The Telethon side of tgmirror: client setup, login, and the ``TelegramGateway`` implementation.

This is the only module (with its tests) that imports Telethon. Telethon exceptions are mapped to
``core.errors`` at this boundary, so nothing above it ever sees an ``RPCError``
(docs/01-kien-truc.md, "Xử lý lỗi"). Phase 1 covers login, listing and creating channels; phase 2
adds reading and copying messages. The limiter is wired in at phase 4 (docs/06-lo-trinh.md).
"""

import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telethon import TelegramClient, errors, types, utils
from telethon.tl import custom
from telethon.tl.functions.channels import CreateChannelRequest

from tgmirror.core.auth import AccountInfo
from tgmirror.core.config import Config
from tgmirror.core.errors import (
    BadApiCredentials,
    CodeExpired,
    FileRefExpired,
    FloodWait,
    ForwardsRestricted,
    GatewayError,
    InvalidCode,
    InvalidPassword,
    InvalidPhone,
    MissingCredentials,
    NoPermission,
    NotLoggedIn,
    PasswordRequired,
    PeerFlood,
    PerMessage,
    SessionBusy,
    TooManyChannels,
    Transient,
)
from tgmirror.core.gateway import (
    ALBUM_MARGIN,
    NO_FILTER,
    ChannelInfo,
    ChatKind,
    MediaKind,
    ServerFilter,
    SrcMessage,
    Unit,
)
from tgmirror.core.paths import Paths

READ_WAIT = 1.0  # seconds between history pages: reads are rate-limited too (docs/05)

# ``media`` pushdown (docs/03-filters.md): only kinds whose Telegram filter is a superset of ours.
# ``filters.pushdown.PUSHABLE_MEDIA`` lists the same kinds; a test keeps the two in step.
_MEDIA_FILTERS: dict[MediaKind, type] = {
    MediaKind.PHOTO: types.InputMessagesFilterPhotos,
    MediaKind.VIDEO: types.InputMessagesFilterVideo,
    MediaKind.AUDIO: types.InputMessagesFilterMusic,
    MediaKind.VOICE: types.InputMessagesFilterVoice,
    MediaKind.GIF: types.InputMessagesFilterGif,
    MediaKind.VIDEO_NOTE: types.InputMessagesFilterRoundVideo,
}

# ---- error mapping --------------------------------------------------------------------------


def _rpc_name(exc: errors.RPCError) -> str:
    """A short, stable name for an RPC error.

    Telethon's ``message`` attribute is only the generic class label ("BAD_REQUEST") for the
    generated subclasses; those are identified by class name. Errors it has no class for arrive
    as a plain ``RPCError`` whose ``message`` is Telegram's own error string.
    """
    return exc.message if type(exc) is errors.RPCError else type(exc).__name__


def map_exception(exc: BaseException) -> GatewayError | None:
    """Translate a Telethon/network exception; ``None`` if it is not one we know how to map."""
    match exc:
        case errors.FloodWaitError():
            return FloodWait(int(exc.seconds))
        case errors.SlowModeWaitError():  # handled like a FloodWait (docs/05), logged apart
            return FloodWait(int(exc.seconds), slow_mode=True)
        case errors.PeerFloodError():
            return PeerFlood("PEER_FLOOD")
        case errors.PasswordHashInvalidError():
            return InvalidPassword("wrong two-step verification password")
        case errors.SessionPasswordNeededError():
            return PasswordRequired("two-step verification password required")
        case errors.PhoneCodeInvalidError() | errors.PhoneCodeEmptyError():
            return InvalidCode("wrong login code")
        case errors.PhoneCodeExpiredError():
            return CodeExpired("login code expired")
        case errors.PhoneNumberInvalidError() | errors.PhoneNumberBannedError():
            return InvalidPhone("phone number not accepted")
        case errors.ApiIdInvalidError():
            return BadApiCredentials("api_id/api_hash rejected by Telegram")
        case (
            errors.AuthKeyUnregisteredError()
            | errors.SessionRevokedError()
            | errors.SessionExpiredError()
            | errors.UserDeactivatedError()
            | errors.UserDeactivatedBanError()
        ):
            return NotLoggedIn("session is no longer valid")
        case (
            errors.ChatWriteForbiddenError()
            | errors.ChatAdminRequiredError()
            | errors.ChannelPrivateError()
            | errors.ChannelInvalidError()
            | errors.UserBannedInChannelError()
        ):
            return NoPermission(_rpc_name(exc))
        case errors.ChatForwardsRestrictedError():
            return ForwardsRestricted("CHAT_FORWARDS_RESTRICTED")
        case errors.FileReferenceExpiredError():
            return FileRefExpired("FILE_REFERENCE_EXPIRED")
        case errors.MessageIdInvalidError():  # every id of a forward is gone; the request failed
            return PerMessage("MESSAGE_ID_INVALID")
        case errors.ChannelsTooMuchError() | errors.UserChannelsTooMuchError():
            return TooManyChannels(_rpc_name(exc))
        case ConnectionError() | TimeoutError() | errors.TimedOutError() | errors.ServerError():
            return Transient(f"connection problem: {type(exc).__name__}")
        case errors.RPCError():
            return GatewayError(f"Telegram error: {_rpc_name(exc)}")
    return None


@contextmanager
def mapped_errors() -> Iterator[None]:
    """Re-raise known Telethon exceptions as ``GatewayError``s (the original is the cause)."""
    try:
        yield
    except Exception as exc:
        if (mapped := map_exception(exc)) is None:
            raise
        raise mapped from exc


# ---- entities -> ChannelInfo ----------------------------------------------------------------


def _restricts_sending(rights: types.ChatBannedRights | None) -> bool:
    """True when ``send_messages`` is banned and the ban has not expired."""
    if rights is None or not rights.send_messages:
        return False
    return rights.until_date is None or rights.until_date > datetime.now(UTC)


def _username(entity: types.Channel) -> str | None:
    if entity.username:
        return entity.username
    return next((u.username for u in entity.usernames or [] if u.active), None)


def channel_info(entity: object) -> ChannelInfo | None:
    """Map a Telethon entity to ``ChannelInfo``; ``None`` for anything that is not a chat we clone.

    Rights come from the entity itself instead of ``get_permissions`` so listing costs no extra
    request per dialog. Whether this matches what Telegram enforces for every kind of chat is still
    to be checked on a real account (docs/06-lo-trinh.md, spike 4).
    """
    if isinstance(entity, types.Channel):
        if entity.left:
            return None
        if entity.broadcast:
            kind = ChatKind.BROADCAST
        elif entity.megagroup:
            kind = ChatKind.FORUM if entity.forum else ChatKind.SUPERGROUP
        else:
            return None
        is_admin = bool(entity.creator or entity.admin_rights)
        if kind is ChatKind.BROADCAST:
            can_post = bool(
                entity.creator or (entity.admin_rights and entity.admin_rights.post_messages)
            )
        else:
            can_post = is_admin or not (
                _restricts_sending(entity.banned_rights)
                or _restricts_sending(entity.default_banned_rights)
            )
        return ChannelInfo(
            id=utils.get_peer_id(entity),
            title=entity.title,
            kind=kind,
            username=_username(entity),
            participants=entity.participants_count,
            noforwards=bool(entity.noforwards),
            is_admin=is_admin,
            can_post=can_post,
        )
    if isinstance(entity, types.Chat):
        if entity.left or entity.deactivated:  # deactivated: migrated to a supergroup
            return None
        is_admin = bool(entity.creator or entity.admin_rights)
        return ChannelInfo(
            id=utils.get_peer_id(entity),
            title=entity.title,
            kind=ChatKind.GROUP,
            participants=entity.participants_count,
            noforwards=bool(entity.noforwards),
            is_admin=is_admin,
            can_post=is_admin or not _restricts_sending(entity.default_banned_rights),
        )
    return None


# ---- messages -> SrcMessage -----------------------------------------------------------------


def media_kind(message: custom.Message) -> MediaKind:
    """The ``media`` filter value of a message (docs/03-filters.md)."""
    media = message.media
    if media is None:
        return MediaKind.TEXT
    if isinstance(media, types.MessageMediaPhoto):
        return MediaKind.PHOTO
    if isinstance(media, types.MessageMediaDocument):
        # order matters: a GIF is also a video, and a video note is a round video
        for kind, is_kind in (
            (MediaKind.STICKER, message.sticker),
            (MediaKind.GIF, message.gif),
            (MediaKind.VIDEO_NOTE, message.video_note),
            (MediaKind.VIDEO, message.video),
            (MediaKind.VOICE, message.voice),
            (MediaKind.AUDIO, message.audio),
        ):
            if is_kind:
                return kind
        return MediaKind.DOCUMENT
    if isinstance(media, types.MessageMediaPoll):
        return MediaKind.POLL
    if isinstance(media, types.MessageMediaWebPage):
        return MediaKind.WEBPAGE
    if isinstance(
        media, types.MessageMediaGeo | types.MessageMediaGeoLive | types.MessageMediaVenue
    ):
        return MediaKind.GEO
    if isinstance(media, types.MessageMediaContact):
        return MediaKind.CONTACT
    if isinstance(media, types.MessageMediaGame):
        return MediaKind.GAME
    if isinstance(media, types.MessageMediaInvoice):
        return MediaKind.INVOICE
    return MediaKind.DOCUMENT  # dice, stories, giveaways...: attachments we have no filter name for


def _hashtags(message: custom.Message) -> tuple[str, ...]:
    """Hashtags from the message entities, lower-cased with ``#`` (not from a text search)."""
    found = message.get_entities_text(types.MessageEntityHashtag)
    return tuple(text.split("@", 1)[0].casefold() for _, text in found)  # groups: #tag@channel


def src_message(message: object) -> SrcMessage | None:
    """Reduce a Telethon message to ``SrcMessage``; ``None`` for anything that is not a message."""
    # Telethon's patched MessageEmpty is a custom.Message too, so it has to be excluded by name
    if not isinstance(message, custom.Message) or isinstance(message, types.MessageEmpty):
        return None
    size = duration = mime = None
    # only real attachments: ``message.file`` would also describe a link preview's picture
    attachment = isinstance(message.media, types.MessageMediaPhoto | types.MessageMediaDocument)
    if attachment and (file := message.file) is not None:
        size, duration, mime = file.size, file.duration, file.mime_type
    return SrcMessage(
        id=message.id,
        date=message.date,
        text=message.message or "",
        media=media_kind(message),
        grouped_id=message.grouped_id,
        is_service=message.action is not None,
        hashtags=_hashtags(message),
        size=size,
        duration=duration,
        mime=mime,
        views=message.views,
    )


# ---- client, auth, gateway ------------------------------------------------------------------


def make_client(session_path: Path, api_id: int, api_hash: str) -> TelegramClient:
    """Telethon client as decided in D6: FloodWait is never slept silently."""
    return TelegramClient(
        str(session_path),
        api_id,
        api_hash,
        flood_sleep_threshold=0,
        request_retries=5,
        connection_retries=5,
        retry_delay=1,
        auto_reconnect=True,
    )


class TelethonAuth:
    """``TelegramAuth`` on a connected Telethon client."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    async def account(self) -> AccountInfo | None:
        with mapped_errors():
            if not await self._client.is_user_authorized():
                return None
            me = await self._client.get_me()
        name = " ".join(p for p in (me.first_name, me.last_name) if p) or (me.username or "")
        return AccountInfo(id=me.id, name=name, username=me.username)

    async def request_code(self, phone: str) -> None:
        with mapped_errors():
            await self._client.send_code_request(phone)

    async def sign_in_code(self, phone: str, code: str) -> None:
        with mapped_errors():
            await self._client.sign_in(phone, code)

    async def sign_in_password(self, password: str) -> None:
        with mapped_errors():
            await self._client.sign_in(password=password)

    async def log_out(self) -> None:
        with mapped_errors():
            await self._client.log_out()


class TelethonGateway:
    """``TelegramGateway`` on a connected Telethon client (reupload arrives in phase 6)."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    async def list_channels(self) -> list[ChannelInfo]:
        channels: list[ChannelInfo] = []
        with mapped_errors():
            async for dialog in self._client.iter_dialogs():
                if (info := channel_info(dialog.entity)) is not None:
                    channels.append(info)
        return channels

    async def get_channel(self, ref: int) -> ChannelInfo:
        with mapped_errors():
            try:
                entity = await self._client.get_entity(ref)
            except ValueError:  # not in the session cache: we never saw it in a dialog list
                raise NoPermission(f"channel {ref} is not accessible") from None
        if (info := channel_info(entity)) is None:
            raise NoPermission(f"{ref} is not a channel or group you can use")
        return info

    async def create_channel(self, title: str, about: str = "") -> ChannelInfo:
        with mapped_errors():
            result = await self._client(
                CreateChannelRequest(title=title, about=about, broadcast=True)
            )
        info = channel_info(result.chats[0])
        if info is None:  # cannot happen for a fresh broadcast channel; fail loudly if it does
            raise GatewayError("Telegram returned an unexpected result for the new channel")
        return info

    async def last_message_id(self, chat: int) -> int:
        peer = await self._peer(chat)
        with mapped_errors():
            latest = await self._client.get_messages(peer, limit=1)
        return latest[0].id if latest else 0

    async def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        peer = await self._peer(src)
        with mapped_errors():
            low, high = min_id, filters.max_id  # ``high`` is included
            if filters.since is not None:
                # one message at (or just after) the date; the margin keeps a boundary album whole
                first = await self._first_id_after(peer, filters.since - timedelta(seconds=1))
                if first is None:
                    return  # nothing that new exists
                low = max(low, first - 1 - ALBUM_MARGIN)
            if filters.until is not None and (
                past := await self._first_id_after(peer, filters.until)
            ):
                high = min(past + ALBUM_MARGIN, high) if high is not None else past + ALBUM_MARGIN
            # reverse=True: oldest first (D4); Telethon starts after ``min_id`` and excludes
            # ``max_id`` itself (spike 3, docs/06). ``filter``/``search`` become a search request.
            options: dict[str, object] = {"min_id": low, "reverse": True, "wait_time": READ_WAIT}
            if high is not None:
                options["max_id"] = high + 1
            if filters.media is not None:
                options["filter"] = _MEDIA_FILTERS[filters.media]
            if filters.search is not None:
                options["search"] = filters.search
            async for message in self._client.iter_messages(peer, **options):
                if (reduced := src_message(message)) is not None:
                    yield reduced

    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]:
        peer = await self._peer(src)
        with mapped_errors():
            found = await self._client.get_messages(peer, ids=list(ids))
        # Telethon answers ``None`` (or an empty message) for an id that no longer exists
        reduced = (src_message(message) for message in found)
        return sorted((m for m in reduced if m is not None), key=lambda m: m.id)

    async def _first_id_after(self, peer: object, moment: datetime) -> int | None:
        """Id of the oldest message dated after ``moment`` (``None`` when there is none)."""
        found = await self._client.get_messages(peer, limit=1, offset_date=moment, reverse=True)
        return found[0].id if found else None

    async def copy_messages(self, src: int, dst: int, ids: list[int]) -> list[int | None]:
        from_peer, to_peer = await self._peer(src), await self._peer(dst)
        with mapped_errors():
            sent = await self._client.forward_messages(
                to_peer, ids, from_peer=from_peer, drop_author=True
            )
        return [None if m is None else m.id for m in sent]

    async def _peer(self, ref: int) -> object:
        """The input entity for ``ref``; chats we never saw in a dialog list are not accessible."""
        with mapped_errors():
            try:
                return await self._client.get_input_entity(ref)
            except ValueError:  # not in the session cache
                raise NoPermission(f"channel {ref} is not accessible") from None

    async def reupload(self, src: int, dst: int, unit: Unit, tmp: Path) -> list[int]:
        raise NotImplementedError("reupload arrives in phase 6")


@asynccontextmanager
async def telethon_session(
    paths: Paths, config: Config, name: str = "default"
) -> AsyncIterator[tuple[TelethonAuth, TelethonGateway]]:
    """Connect with the saved session (creating it on first use) and disconnect on exit."""
    if config.api_id is None or config.api_hash is None:
        raise MissingCredentials("api_id/api_hash are not configured")
    paths.ensure()
    try:  # the session file is opened when the client is built, so a lock shows up here
        client = make_client(
            paths.session_path(name), config.api_id, config.api_hash.get_secret_value()
        )
        with mapped_errors():
            await client.connect()
    except sqlite3.OperationalError as exc:
        raise SessionBusy("session file is locked by another process") from exc
    try:
        yield TelethonAuth(client), TelethonGateway(client)
    finally:
        await client.disconnect()
