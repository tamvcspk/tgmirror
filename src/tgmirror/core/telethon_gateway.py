"""The Telethon side of tgmirror: client setup, login, and the ``TelegramGateway`` implementation.

This is the only module (with its tests) that imports Telethon. Telethon exceptions are mapped to
``core.errors`` at this boundary, so nothing above it ever sees an ``RPCError``
(docs/01-kien-truc.md, "Xử lý lỗi"). Phase 1 covers login, listing and creating channels;
reading and copying messages arrive with phase 2, and the limiter is wired in at phase 4
(docs/06-lo-trinh.md).
"""

import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from telethon import TelegramClient, errors, types, utils
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
    SessionBusy,
    TooManyChannels,
    Transient,
)
from tgmirror.core.gateway import (
    NO_FILTER,
    ChannelInfo,
    ChatKind,
    ServerFilter,
    SrcMessage,
    Unit,
)
from tgmirror.core.paths import Paths

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
        case errors.FloodWaitError() | errors.SlowModeWaitError():  # same handling (docs/05)
            return FloodWait(int(exc.seconds))
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
    """``TelegramGateway`` on a connected Telethon client (phase 1: channels only)."""

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

    def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        raise NotImplementedError("reading messages arrives in phase 2")

    async def copy_messages(self, src: int, dst: int, ids: list[int]) -> list[int | None]:
        raise NotImplementedError("copying messages arrives in phase 2")

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
