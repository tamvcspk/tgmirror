"""The Telethon side of tgmirror: client setup, login, and the ``TelegramGateway`` implementation.

This is the only module (with its tests) that imports Telethon. Telethon exceptions are mapped to
``core.errors`` at this boundary, so nothing above it ever sees an ``RPCError``
(docs/01-kien-truc.md, "Xử lý lỗi"). Phase 1 covers login, listing and creating channels; phase 2
adds reading and copying messages. The limiter is wired in at phase 4 (docs/06-lo-trinh.md).
"""

import asyncio
import contextlib
import copy
import itertools
import logging
import math
import os
import re
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from telethon import TelegramClient, errors, helpers, types, utils
from telethon.errors.common import InvalidBufferError
from telethon.extensions import html as tl_html
from telethon.network import MTProtoSender
from telethon.tl import custom
from telethon.tl.functions.channels import CreateChannelRequest, ToggleForumRequest
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    ForwardMessagesRequest,
    GetForumTopicsRequest,
    SearchRequest,
    SendMultiMediaRequest,
    UploadMediaRequest,
)
from telethon.tl.functions.upload import GetFileRequest, SaveBigFilePartRequest

from tgmirror.core.auth import AccountInfo
from tgmirror.core.config import Config, Limits
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
    TransportPressure,
)
from tgmirror.core.gateway import (
    ALBUM_MARGIN,
    NO_FILTER,
    CaptionMode,
    CaptionPolicy,
    ChannelInfo,
    ChatKind,
    ExportedMedia,
    ExportedMessage,
    MediaKind,
    OnTransfer,
    Prepared,
    ServerFilter,
    SrcMessage,
    TopicInfo,
    TransferPhase,
    Unit,
)
from tgmirror.core.paths import Paths
from tgmirror.core.pool import RequestBudget, run_parts

# The Telethon version this module was last checked against. ``pyproject.toml`` pins the same one
# and ``tests/unit/test_telethon_pin.py`` fails on any other, because the request pool and the
# reupload reach into Telethon internals with no compatibility promise. Bump it only after the
# checklist in docs/06-lo-trinh.md ("Nâng cấp Telethon").
TELETHON_CHECKED = "1.45.0"


def _quiet_hachoir() -> None:
    """Telethon reads a video's details with hachoir, which prints ``[warn] [<NdsFile>] ...``
    whenever one of its many parsers fails on a big file. It is noise: the details we need are
    passed explicitly."""
    try:
        from hachoir.core import config
    except ImportError:
        return
    config.quiet = True


_quiet_hachoir()

log = logging.getLogger(__name__)  # ``tgmirror.core.telethon_gateway``: shown by the CLI

READ_WAIT = 1.0  # seconds between history pages: reads are rate-limited too (docs/05)

DOWN_PART = 1024 * 1024  # a download request: at most 1 MiB, never across a 1 MiB boundary
UP_PART = 512 * 1024  # an upload part: at most 512 KiB
BIG_FILE = 10 * 1024 * 1024  # from here Telegram wants the big-file upload
MAX_UP_PARTS = 4000  # parts of a big file (2 GB; more only for Premium: not pooled)
START_REQUESTS = 2  # requests in flight a budget starts with


def _budget(maximum: int) -> RequestBudget | None:
    """A download or upload's own ``RequestBudget``, or ``None`` (that direction's pool off,
    ``download_requests``/``upload_requests`` = 0) — download and upload no longer share one
    (docs/06-lo-trinh.md, 2026-09-23: a real run found 8 downloads in flight broke on repeated
    transport 429s and dead connections, while 8 uploads did not; they need to be tunable apart)."""
    if maximum <= 0:
        return None
    return RequestBudget(start=min(START_REQUESTS, maximum), maximum=maximum)


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
        case (
            errors.MediaCaptionTooLongError() | errors.MessageTooLongError()
        ):  # ``append`` can do it
            return PerMessage(_rpc_name(exc))
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


def _text_of(value: object) -> str:
    """Poll questions are ``TextWithEntities`` in newer layers and plain strings in older ones."""
    return str(getattr(value, "text", value))


def _title_and_quiz(message: custom.Message) -> tuple[str | None, bool]:
    """A title to name the message by (game, invoice, poll question) and whether it is a quiz whose
    right answer this account cannot see (Telethon cannot rebuild such a quiz)."""
    media = message.media
    if isinstance(media, types.MessageMediaPoll):
        seen = bool(media.results and media.results.results)
        return _text_of(media.poll.question), bool(media.poll.quiz) and not seen
    if isinstance(media, types.MessageMediaGame):
        return media.game.title, False
    if isinstance(media, types.MessageMediaInvoice):
        return media.title, False
    return None, False


def _topic_of(message: custom.Message) -> int | None:
    """The forum topic a message belongs to (1 = General), or ``None`` outside a forum.

    Phase 8, unverified on a real account (docs/06-lo-trinh.md, open question 9): a reply within a
    topic carries ``reply_to_top_id``; the message that *defines* a topic (its first message) has
    ``forum_topic`` set but no ``reply_to_top_id``, so it is its own topic and
    ``reply_to_msg_id`` is the topic id instead.
    """
    reply_to = message.reply_to
    if reply_to is None or not getattr(reply_to, "forum_topic", False):
        return None
    return reply_to.reply_to_top_id or reply_to.reply_to_msg_id


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
    title, quiz_unanswered = _title_and_quiz(message)
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
        quiz_unanswered=quiz_unanswered,
        title=title,
        topic_id=_topic_of(message),
        from_user_id=message.sender_id,
    )


# ---- messages -> ExportedMessage (phase 11, backup) ------------------------------------------


def _poll_media(media: types.MessageMediaPoll) -> ExportedMedia:
    options = tuple(_text_of(a.text) for a in media.poll.answers)
    correct: int | None = None
    if media.results and media.results.results:
        by_option = {a.option: i for i, a in enumerate(media.poll.answers)}
        for r in media.results.results:
            if r.correct and r.option in by_option:
                correct = by_option[r.option]
                break
    return ExportedMedia(
        kind=MediaKind.POLL,
        poll_question=_text_of(media.poll.question),
        poll_options=options,
        poll_quiz=bool(media.poll.quiz),
        poll_correct_option=correct,
    )


def _geo_media(media: object) -> ExportedMedia:
    geo = getattr(media, "geo", None)
    venue_title = media.title if isinstance(media, types.MessageMediaVenue) else None
    return ExportedMedia(
        kind=MediaKind.GEO,
        geo_lat=getattr(geo, "lat", None),
        geo_lon=getattr(geo, "long", None),
        venue_title=venue_title,
    )


def _contact_media(media: types.MessageMediaContact) -> ExportedMedia:
    return ExportedMedia(
        kind=MediaKind.CONTACT,
        contact_phone=media.phone_number or None,
        contact_first_name=media.first_name or None,
        contact_last_name=media.last_name or None,
    )


def _self_contained_media(media: object) -> ExportedMedia | None:
    """The export of media with no file (docs/06-lo-trinh.md, phase 11): everything a backup can
    save of a poll/geo/contact/game/invoice, or ``None`` for plain text/a link preview (the text
    itself already carries what matters)."""
    if isinstance(media, types.MessageMediaPoll):
        return _poll_media(media)
    if isinstance(
        media, types.MessageMediaGeo | types.MessageMediaGeoLive | types.MessageMediaVenue
    ):
        return _geo_media(media)
    if isinstance(media, types.MessageMediaContact):
        return _contact_media(media)
    if isinstance(media, types.MessageMediaGame):
        return ExportedMedia(kind=MediaKind.GAME, title=media.game.title)
    if isinstance(media, types.MessageMediaInvoice):
        return ExportedMedia(kind=MediaKind.INVOICE, title=media.title)
    return None


def _forward_result_ids(req: ForwardMessagesRequest, result: object) -> list[int | None]:
    """Map a raw ``ForwardMessagesRequest`` result back to ``req.random_id`` order.

    Mirrors the mapping Telethon's own ``forward_messages`` does internally (``random_id`` ->
    ``UpdateMessageID.id``), without reaching into its private ``_get_response_message`` helper.
    """
    if isinstance(result, types.UpdateShort):
        updates: Sequence[object] = [result.update]
    elif isinstance(result, types.Updates | types.UpdatesCombined):
        updates = result.updates
    else:
        updates = ()
    random_to_id = {u.random_id: u.id for u in updates if isinstance(u, types.UpdateMessageID)}
    return [random_to_id.get(rnd) for rnd in req.random_id]


# ---- strategy B: captions and files ---------------------------------------------------------

_SELF_CONTAINED = (
    types.MessageMediaPoll,
    types.MessageMediaGeo,
    types.MessageMediaGeoLive,
    types.MessageMediaVenue,
    types.MessageMediaContact,
    types.MessageMediaDice,
)  # media with no file: Telethon rebuilds it from the message's own media object


def _units16(text: str) -> bytes:
    return text.encode("utf-16-le")


def _slice16(text: str, offset: int, length: int) -> str:
    """Telegram counts entity offsets in UTF-16 code units."""
    return _units16(text)[2 * offset : 2 * (offset + length)].decode("utf-16-le", "ignore")


def _points_at_source(reference: str, names: Collection[str]) -> bool:
    """A ``t.me`` link or an ``@mention`` of one of the source's names (lower-case)."""
    ref = reference.strip().casefold()
    if ref.startswith("@"):
        return ref[1:] in names
    found = re.match(r"(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?:c/)?([\w]+)", ref)
    return found is not None and found.group(1) in names


def strip_source_links(
    text: str, entities: Sequence[object], names: Collection[str]
) -> tuple[str, list[object]]:
    """Drop what points back at the source: a visible link or mention is deleted with its text, a
    hyperlink keeps its words and loses the link. Everything else stays, offsets moved to fit."""
    cuts: list[tuple[int, int]] = []
    kept: list[object] = []
    for entity in entities:
        if isinstance(entity, types.MessageEntityUrl | types.MessageEntityMention):
            visible = _slice16(text, entity.offset, entity.length)
            if _points_at_source(visible, names):
                cuts.append((entity.offset, entity.length))
                continue
        elif isinstance(entity, types.MessageEntityTextUrl) and _points_at_source(
            entity.url, names
        ):
            continue
        kept.append(entity)
    if not cuts:
        return text, kept

    def moved(position: int) -> int:
        return position - sum(min(length, max(0, position - at)) for at, length in cuts)

    units = bytearray(_units16(text))
    for at, length in sorted(cuts, reverse=True):
        del units[2 * at : 2 * (at + length)]
    new_text = units.decode("utf-16-le")
    trimmed = new_text.rstrip()
    limit = len(_units16(trimmed)) // 2
    result: list[object] = []
    for entity in kept:
        start = moved(entity.offset)
        end = min(moved(entity.offset + entity.length), limit)
        if end > start:
            clone = copy.copy(entity)
            clone.offset, clone.length = start, end - start
            result.append(clone)
    return trimmed, result


def rewrite_caption(
    text: str, entities: Sequence[object], policy: CaptionPolicy, names: Collection[str]
) -> tuple[str, list[object]]:
    """The caption and its entities after ``policy`` (docs/02-cli-ux.md, "Caption handling")."""
    match policy.mode:
        case CaptionMode.NONE:
            text, kept = "", []
        case CaptionMode.APPEND:
            text, kept = (f"{text}\n\n{policy.text}" if text else text), list(entities)
        case CaptionMode.STRIP_LINKS:
            text, kept = strip_source_links(text, entities, names)
        case _:
            text, kept = text, list(entities)
    if policy.hashtag:  # phase 8: appended last, whatever the mode did; Telegram parses it itself
        text = f"{text}\n{policy.hashtag}" if text else policy.hashtag
    return text, kept


def _album_captions(
    messages: Sequence[custom.Message], policy: CaptionPolicy
) -> list[tuple[str, list[object]]]:
    """``rewrite_caption`` for each message of a unit, with the topic hashtag (phase 8) added
    once: to the first caption left, or the first message when none is. Telegram shows an album's
    only caption as the album's; a hashtag on every item would bury the real one."""
    plain = replace(policy, hashtag=None)
    out = [
        rewrite_caption(m.message or "", list(m.entities or []), plain, _source_names(m))
        for m in messages
    ]
    if policy.hashtag and out:
        at = next((i for i, (text, _) in enumerate(out) if text), 0)
        text, entities = out[at]
        out[at] = (f"{text}\n{policy.hashtag}" if text else policy.hashtag, entities)
    return out


def _source_names(message: custom.Message) -> set[str]:
    """What links to the message's chat look like: its usernames and, for a private channel, the
    number in ``t.me/c/<number>/...``."""
    names: set[str] = set()
    chat = message.chat
    for name in [getattr(chat, "username", None)] + [
        u.username for u in getattr(chat, "usernames", None) or [] if u.active
    ]:
        if name:
            names.add(name.casefold())
    if message.chat_id is not None:
        names.add(str(message.chat_id).removeprefix("-100").removeprefix("-"))
    return names


@dataclass(frozen=True, slots=True)
class _Item:
    """One message of a prepared unit: the message as Telegram gave it and the files downloaded."""

    message: custom.Message
    path: Path | None = None  # its media, when it has a file
    thumb: Path | None = None
    uploaded: Any = None  # the ``InputFileBig`` its bytes were uploaded as, ahead of the post


@dataclass(frozen=True, slots=True)
class _Fetched:
    items: tuple[_Item, ...]


def _has_file(message: custom.Message) -> bool:
    return isinstance(message.media, types.MessageMediaPhoto | types.MessageMediaDocument)


@contextmanager
def _media_reusable() -> Iterator[None]:
    """Telegram would not send this media again by its id: the caller sends it the long way."""
    try:
        yield
    except (
        errors.FileReferenceEmptyError,
        errors.FileReferenceInvalidError,
        errors.FileIdInvalidError,
        errors.MediaEmptyError,
        errors.MediaInvalidError,
        errors.GroupedMediaInvalidError,
    ) as exc:
        raise FileRefExpired(_rpc_name(exc)) from exc


def _reporting(
    on_transfer: OnTransfer | None, phase: TransferPhase, msg_id: int
) -> Callable[[float, float], None] | None:
    """Telethon's ``progress_callback(done, total)`` as a call to ``on_transfer``. A callback that
    fails must never fail the transfer."""
    if on_transfer is None:
        return None

    def callback(done: float, total: float) -> None:
        if not total:
            return
        with contextlib.suppress(Exception):
            on_transfer(phase, msg_id, int(done), int(total))

    return callback


def _total_of(result: Any) -> int:
    """The total of a ``messages.search`` answer (a plain ``Messages`` has no ``count``)."""
    total = getattr(result, "count", None)
    return int(total) if total is not None else len(result.messages)


async def _position(ask: Callable[[int], Awaitable[Any]], offset_id: int, total: int) -> int | None:
    """How many matching messages have an id ``>= offset_id``, or ``None`` if Telegram did not say.

    Nothing older than ``offset_id`` in the answer means every match is at or above it."""
    result = await ask(offset_id)
    if (position := getattr(result, "offset_id_offset", None)) is not None:
        return int(position)
    return None if result.messages else total


def _album_share(items: Sequence[_Item]) -> int:
    """Bytes of an album's files, as the messages say (``0``: nothing to scale a report to)."""
    return sum(i.message.file.size or 0 for i in items if i.message.file is not None)


class _FileAt:
    """A file read or written at offsets from several tasks: one handle, one lock, and the
    seek and the transfer of each part done under it (the calls run in threads)."""

    def __init__(self, path: Path, size: int, *, mode: str = "new") -> None:
        """``mode``: ``new`` makes the whole file at once, ``resume`` reopens one that a cut-off
        transfer left, ``read`` opens one for reading."""
        self._path = path
        self._size = size
        self._mode = mode
        self._lock = threading.Lock()
        self._file: Any = None

    def open(self) -> None:
        if self._mode == "new":  # the whole file is made at once so every part has its place
            with open(self._path, "wb") as fresh:
                fresh.truncate(self._size)
        self._file = open(self._path, "rb" if self._mode == "read" else "r+b")  # noqa: SIM115

    def write(self, offset: int, data: bytes) -> None:
        with self._lock:
            self._file.seek(offset)
            self._file.write(data)

    def read(self, offset: int, length: int) -> bytes:
        with self._lock:
            self._file.seek(offset)
            return bytes(self._file.read(length))

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def _make_room(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)


def _publish(part: str, final: Path) -> None:
    os.replace(part, final)


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


@dataclass(frozen=True, slots=True)
class TransferSettings:
    """How files of strategy B move (``[limits]`` ``download_requests``, ``upload_requests``,
    ``upload_connections``, ``pool_min_mb``): a separate request budget for each direction, so one
    can be tuned without the other. ``0`` (the default here, so tests and one-shot commands are
    unaffected) leaves that direction to Telethon: one request at a time."""

    download_requests: int = 0
    upload_requests: int = 0
    upload_connections: int = 2
    min_bytes: int = BIG_FILE
    request_timeout: float = 30.0  # a request that gets no answer counts as pushback

    @classmethod
    def of(cls, limits: Limits) -> "TransferSettings":
        return cls(
            limits.download_requests,
            limits.upload_requests,
            limits.upload_connections,
            limits.pool_min_mb * 1024 * 1024,
        )


class _NoPool(Exception):
    """This file cannot go through the pool (a CDN redirect, ...): Telethon's way instead."""


class TelethonGateway:
    """``TelegramGateway`` on a connected Telethon client."""

    def __init__(
        self,
        client: TelegramClient,
        transfer: TransferSettings | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._transfer = transfer or TransferSettings()
        self._sleep = sleep
        self._download_budget = _budget(self._transfer.download_requests)
        self._upload_budget = _budget(self._transfer.upload_requests)
        # Bulk data never shares the main connection with the calls that read and post: the first
        # real run drew a 429 with downloads and uploads on it. Uploads spread over extra
        # connections, a download over one of its own (both to this account's data centre).
        self._extra_senders: list[MTProtoSender] | None = None
        self._down_sender: Any = None
        self._owned: list[MTProtoSender] = []  # what ``aclose`` must disconnect
        # a download cut off by a flood or a lost connection keeps what it has:
        # path -> (size, the parts already written)
        self._partial: dict[Path, tuple[int, set[int]]] = {}
        self._rotation = itertools.count()

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

    async def create_channel(
        self, title: str, about: str = "", kind: ChatKind = ChatKind.BROADCAST
    ) -> ChannelInfo:
        with mapped_errors():
            result = await self._client(
                CreateChannelRequest(title=title, about=about, broadcast=kind is ChatKind.BROADCAST)
                if kind is ChatKind.BROADCAST
                else CreateChannelRequest(title=title, about=about, megagroup=True)
            )
            chat = result.chats[0]
            if kind is ChatKind.FORUM:
                await self._client(ToggleForumRequest(chat, enabled=True, tabs=False))
                chat = await self._client.get_entity(utils.get_peer_id(chat))
        info = channel_info(chat)
        if info is None:  # cannot happen for a fresh channel; fail loudly if it does
            raise GatewayError("Telegram returned an unexpected result for the new channel")
        return info

    async def list_topics(self, src: int) -> list[TopicInfo]:
        peer = await self._peer(src)
        topics: dict[int, TopicInfo] = {}
        seen = 0  # entries Telegram returned, deleted topics included: they count in ``count``
        offset_date, offset_id, offset_topic = None, 0, 0
        with mapped_errors():
            while True:
                result = await self._client(
                    GetForumTopicsRequest(
                        peer=peer,
                        offset_date=offset_date,
                        offset_id=offset_id,
                        offset_topic=offset_topic,
                        limit=100,
                    )
                )
                seen += len(result.topics)
                page = [t for t in result.topics if isinstance(t, types.ForumTopic)]
                for topic in page:
                    topics[topic.id] = TopicInfo(topic.id, topic.title, closed=bool(topic.closed))
                if not page or seen >= result.count:
                    break
                # topics come newest activity first: the next page starts after the last one's
                # top message (its date, not the topic's creation date, orders the list)
                last = page[-1]
                dates = {m.id: m.date for m in result.messages if hasattr(m, "date")}
                cursor = (dates.get(last.top_message, last.date), last.top_message, last.id)
                if cursor == (offset_date, offset_id, offset_topic):
                    break  # no progress: never loop forever on an odd answer
                offset_date, offset_id, offset_topic = cursor
        return list(topics.values())

    async def create_topic(self, dst: int, title: str) -> int:
        peer = await self._peer(dst)
        with mapped_errors():
            result = await self._client(CreateForumTopicRequest(peer=peer, title=title))
        for update in result.updates:
            if isinstance(update, types.UpdateMessageID):
                return update.id
        raise GatewayError("Telegram returned no id for the new topic")

    async def last_message_id(self, chat: int) -> int:
        peer = await self._peer(chat)
        with mapped_errors():
            latest = await self._client.get_messages(peer, limit=1)
        return latest[0].id if latest else 0

    async def _bounds(
        self, peer: object, min_id: int, filters: ServerFilter, *, before: int, after: int
    ) -> tuple[int, int | None] | None:
        """The id range ``filters`` selects: messages with ``id > low`` and ``id <= high`` (``high``
        ``None``: no upper bound), or ``None`` when nothing that new exists.

        ``before``/``after`` widen the range where a date bound falls (reading keeps an album on the
        boundary whole with ``ALBUM_MARGIN``; counting wants the range as it is).
        """
        low, high = min_id, filters.max_id
        if filters.since is not None:
            # one message at (or just after) the date
            first = await self._first_id_after(peer, filters.since - timedelta(seconds=1))
            if first is None:
                return None
            low = max(low, first - 1 - before)
        if filters.until is not None and (past := await self._first_id_after(peer, filters.until)):
            high = min(past + after, high) if high is not None else past + after
        return low, high

    async def count(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int:
        peer = await self._peer(src)
        with mapped_errors():
            bounds = await self._bounds(peer, min_id, filters, before=0, after=-1)
            if bounds is None or (bounds[1] is not None and bounds[1] <= bounds[0]):
                return 0
            low, high = bounds
            kind = (
                types.InputMessagesFilterEmpty()
                if filters.media is None
                else _MEDIA_FILTERS[filters.media]()
            )

            async def ask(offset_id: int) -> Any:
                # one message and the totals: ``limit=1`` is all that is needed. ``min_id`` and
                # ``max_id`` are left out on purpose: Telegram ignores them when it counts (checked
                # on a real channel by ``scripts/spike_count.py``, docs/06 spike 10).
                return await self._client(
                    SearchRequest(
                        peer=peer,
                        q=filters.search or "",
                        filter=kind,
                        min_date=None,
                        max_date=None,
                        offset_id=offset_id,
                        add_offset=0,
                        limit=1,
                        max_id=0,
                        min_id=0,
                        hash=0,
                    )
                )

            total = _total_of(await ask(0))
            if low <= 0 and high is None:
                return total
            # What Telegram does say is where the first message older than ``offset_id`` stands in
            # the whole list (``offset_id_offset``: how many matching messages have an id at or
            # above ``offset_id``). Two such positions bound the range exactly.
            from_low = await _position(ask, low + 1, total) if low > 0 else total
            above_high = await _position(ask, high + 1, total) if high is not None else 0
        if from_low is None or above_high is None:
            return total  # Telegram did not say: the whole list is still an upper bound
        return max(from_low - above_high, 0)

    async def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        peer = await self._peer(src)
        with mapped_errors():
            # the margin keeps an album that straddles a date boundary whole
            bounds = await self._bounds(
                peer, min_id, filters, before=ALBUM_MARGIN, after=ALBUM_MARGIN
            )
            if bounds is None:
                return  # nothing that new exists
            low, high = bounds  # ``high`` is included
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

    async def copy_messages(
        self, src: int, dst: int, ids: list[int], *, topic: int | None = None
    ) -> list[int | None]:
        from_peer, to_peer = await self._peer(src), await self._peer(dst)
        if topic is None:  # the well-verified path (docs/06-lo-trinh.md, spike 2)
            with mapped_errors():
                sent = await self._client.forward_messages(
                    to_peer, ids, from_peer=from_peer, drop_author=True
                )
            return [None if m is None else m.id for m in sent]
        # ``forward_messages`` has no topic parameter (docs/01-kien-truc.md, "Ánh xạ topic");
        # phase 8, unverified on a real account.
        req = ForwardMessagesRequest(
            from_peer=from_peer, id=ids, to_peer=to_peer, drop_author=True, top_msg_id=topic
        )
        with mapped_errors():
            result = await self._client(req)
        return _forward_result_ids(req, result)

    async def _peer(self, ref: int) -> object:
        """The input entity for ``ref``; chats we never saw in a dialog list are not accessible."""
        with mapped_errors():
            try:
                return await self._client.get_input_entity(ref)
            except ValueError:  # not in the session cache
                raise NoPermission(f"channel {ref} is not accessible") from None

    # ---- strategy B ------------------------------------------------------------------------

    async def _read_unit(self, src: int, unit: Unit) -> list[custom.Message]:
        """The unit's messages read again (fresh file references); ``PerMessage`` if one is
        gone."""
        peer = await self._peer(src)
        with mapped_errors():
            found = await self._client.get_messages(peer, ids=unit.ids)
        messages = [
            m
            for m in found
            if isinstance(m, custom.Message) and not isinstance(m, types.MessageEmpty)
        ]
        if len(messages) != len(unit.ids):  # deleted since it was read
            raise PerMessage("gone_from_source")
        return messages

    async def fetch(self, src: int, unit: Unit) -> Prepared:
        messages = await self._read_unit(src, unit)
        return Prepared(unit, (), _Fetched(tuple(_Item(m) for m in messages)))

    async def send_by_reference(
        self, dst: int, prepared: Prepared, caption: CaptionPolicy, *, topic: int | None = None
    ) -> list[int]:
        fetched = prepared.handle
        assert isinstance(fetched, _Fetched)
        peer = await self._peer(dst)
        captions = _album_captions([item.message for item in fetched.items], caption)
        texts = [text for text, _ in captions]
        entity_lists = [entities for _, entities in captions]
        media = [item.message.media for item in fetched.items]
        with mapped_errors(), _media_reusable():
            if len(media) > 1:
                sent = await self._client.send_file(
                    peer,
                    media,
                    caption=texts,
                    formatting_entities=entity_lists,
                    parse_mode=None,
                    reply_to=topic,
                )
                return [int(m.id) for m in sent]
            one = await self._client.send_file(
                peer,
                media[0],
                caption=texts[0],
                formatting_entities=entity_lists[0] or None,
                parse_mode=None,
                reply_to=topic,
            )
            return [int(one.id)]

    async def prepare(
        self, src: int, unit: Unit, tmp: Path, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        messages = await self._read_unit(src, unit)
        items: list[_Item] = []
        with mapped_errors():
            for message in messages:
                path = thumb = None
                if _has_file(message):
                    path = await self._download(message, tmp, on_transfer)
                    thumb = await self._thumbnail(message, tmp)
                items.append(_Item(message, path, thumb))
        files = tuple(f for item in items for f in (item.path, item.thumb) if f is not None)
        return Prepared(unit, files, _Fetched(tuple(items)))

    async def export_unit(
        self, src: int, unit: Unit, media_dir: Path, on_transfer: OnTransfer | None = None
    ) -> list[ExportedMessage]:
        messages = await self._read_unit(src, unit)
        out: list[ExportedMessage] = []
        with mapped_errors():
            for message in messages:
                media: ExportedMedia | None = None
                if _has_file(message):
                    path = await self._download(message, media_dir, on_transfer)
                    f = message.file
                    media = ExportedMedia(
                        kind=media_kind(message),
                        filename=path.name,
                        mime=f.mime_type,
                        size=f.size,
                        duration=f.duration,
                    )
                elif message.media is not None:
                    media = _self_contained_media(message.media)
                out.append(
                    ExportedMessage(
                        id=message.id,
                        date=message.date,
                        grouped_id=message.grouped_id,
                        topic_id=_topic_of(message),
                        from_user_id=message.sender_id,
                        text_html=tl_html.unparse(message.message or "", message.entities or []),
                        views=message.views,
                        media=media,
                    )
                )
        return out

    # ---- file transfers through the request budget ---------------------------------------

    def _pooled(self, message: custom.Message) -> bool:
        """A document big enough to be worth many requests in flight (download side)."""
        return (
            self._download_budget is not None
            and message.document is not None
            and (message.file.size or 0) >= self._transfer.min_bytes
        )

    async def _request(self, sender: object, request: object) -> Any:
        """One request of a transfer, its transport errors as ``TransportPressure``."""
        try:
            with mapped_errors():
                return await asyncio.wait_for(
                    self._client._call(sender, request),  # noqa: SLF001
                    self._transfer.request_timeout,
                )
        except InvalidBufferError as exc:  # code 429: Telegram's flood at the transport level
            raise TransportPressure(f"transport error {exc.code}", flood=exc.code == 429) from exc
        except Transient as exc:  # closed connection, no answer in time
            raise TransportPressure(str(exc)) from exc

    async def _download_parallel(
        self, message: custom.Message, part: Path, on_transfer: OnTransfer | None
    ) -> str:
        """The document of ``message`` into ``part``, many 1 MiB requests in flight on the
        connection of the file's data centre (one connection is enough: spike 12). Each part
        is written where it belongs, so the order they arrive in does not matter. Raises
        ``_NoPool`` for a file this cannot fetch."""
        assert self._download_budget is not None
        size = int(message.file.size)
        dc_id, location = utils.get_input_location(message.media)
        home = self._client.session.dc_id
        borrowed = dc_id is not None and dc_id != home
        sender = (
            await self._client._borrow_exported_sender(dc_id)  # noqa: SLF001
            if borrowed
            else await self._download_sender()
        )
        report = _reporting(on_transfer, TransferPhase.DOWNLOAD, message.id)
        known = self._partial.get(part)
        resuming = known is not None and known[0] == size and await asyncio.to_thread(part.exists)
        have: set[int] = known[1] if resuming and known is not None else set()
        self._partial[part] = (size, have)
        done = sum(min(DOWN_PART, size - i * DOWN_PART) for i in have)
        handle = _FileAt(part, size, mode="resume" if resuming else "new")
        try:

            async def work(index: int) -> None:
                nonlocal done
                if index in have:  # fetched before the transfer was cut off
                    return
                offset = index * DOWN_PART
                got = await self._request(
                    sender, GetFileRequest(location, offset=offset, limit=DOWN_PART)
                )
                if isinstance(got, types.upload.FileCdnRedirect):
                    raise _NoPool
                if len(got.bytes) != min(DOWN_PART, size - offset):
                    raise TransportPressure("a part came back with the wrong length")
                await asyncio.to_thread(handle.write, offset, got.bytes)
                have.add(index)
                done += len(got.bytes)
                if report is not None:
                    report(done, size)

            await asyncio.to_thread(handle.open)
            if report is not None:
                report(done, size)
            parts = math.ceil(size / DOWN_PART)
            await run_parts(parts, work, self._download_budget, sleep=self._sleep)
            self._partial.pop(part, None)
        finally:
            await asyncio.to_thread(handle.close)
            if borrowed:
                await self._client._return_exported_sender(sender)  # noqa: SLF001
        return str(part)

    async def _download_sender(self) -> Any:
        """The connection downloads use: one of their own, made once and kept. If it cannot be
        made the main one is used, which works but shares it with everything else (said aloud:
        a transfer that runs on fewer connections than asked for is otherwise a slow mystery)."""
        if self._down_sender is None:
            try:
                self._down_sender = await self._new_sender()
                self._owned.append(self._down_sender)
            except Exception as exc:  # noqa: BLE001 - the main connection is the fallback
                log.warning(
                    "download connection could not be made (%s: %s); downloads share the main "
                    "connection, which is slower",
                    type(exc).__name__,
                    exc,
                )
                self._down_sender = self._client._sender  # noqa: SLF001
        return self._down_sender

    async def _upload_senders(self) -> list[object]:
        """The connections an upload is spread over: ``upload_connections`` of their own to the
        account's data centre (they share its auth key), made once and kept for the next file.
        One that cannot be made is left out; with none, the main connection is used, which works
        but shares it with everything else. Either way it is said aloud, once: one connection
        uploads at ~3 MB/s and two or more at 18-28 (docs/06, spike 2026-09-21), so silently
        making fewer than asked for is the first suspect when an upload is slow."""
        if self._extra_senders is None:
            self._extra_senders = []
            wanted = self._transfer.upload_connections
            failure: Exception | None = None
            for _ in range(wanted):
                try:
                    sender = await self._new_sender()
                except Exception as exc:  # noqa: BLE001 - fewer connections is the fallback
                    failure = exc
                    break
                self._extra_senders.append(sender)
                self._owned.append(sender)
            if failure is not None:
                made = len(self._extra_senders)
                log.warning(
                    "upload connections: only %d of %d could be made (%s: %s); %s",
                    made,
                    wanted,
                    type(failure).__name__,
                    failure,
                    "uploads are slower than they should be"
                    if made
                    else "uploads share the main connection, which is much slower",
                )
        return list(self._extra_senders) or [self._client._sender]  # noqa: SLF001

    async def _new_sender(self) -> MTProtoSender:
        """A connection of its own to this account's data centre, on the same auth key."""
        client = self._client
        dc = await client._get_dc(client.session.dc_id)  # noqa: SLF001
        sender = MTProtoSender(client.session.auth_key, loggers=client._log)  # noqa: SLF001
        await sender.connect(
            client._connection(  # noqa: SLF001
                dc.ip_address,
                dc.port,
                dc.id,
                loggers=client._log,  # noqa: SLF001
                proxy=client._proxy,  # noqa: SLF001
                local_addr=client._local_addr,  # noqa: SLF001
            )
        )
        return sender

    async def aclose(self) -> None:
        """Close the connections of file transfers (the client itself is the caller's)."""
        for sender in self._owned:
            with contextlib.suppress(Exception):
                await sender.disconnect()
        self._owned = []
        self._extra_senders = None
        self._down_sender = None
        self._partial.clear()

    async def _upload_parallel(
        self, path: Path, msg_id: int, on_transfer: OnTransfer | None
    ) -> types.InputFileBig:
        """``path`` as a big file uploaded in 512 KiB parts, many in flight at once over several
        connections; the result is what ``send_file`` takes instead of the path. Parts may
        arrive in any order, and a file that is uploaded but never posted is discarded by
        Telegram, so a failure part-way leaves nothing behind."""
        assert self._upload_budget is not None
        size = (await asyncio.to_thread(path.stat)).st_size
        parts = math.ceil(size / UP_PART)
        senders = await self._upload_senders()
        file_id = helpers.generate_random_long()
        report = _reporting(on_transfer, TransferPhase.UPLOAD, msg_id)
        done = 0
        handle = _FileAt(path, size, mode="read")
        try:

            async def work(index: int) -> None:
                nonlocal done
                data = await asyncio.to_thread(handle.read, index * UP_PART, UP_PART)
                sender = senders[next(self._rotation) % len(senders)]  # a retry may change
                sent = await self._request(
                    sender, SaveBigFilePartRequest(file_id, index, parts, data)
                )
                if not sent:
                    raise PerMessage("upload_failed")
                done += len(data)
                if report is not None:
                    report(done, size)

            await asyncio.to_thread(handle.open)
            if report is not None:
                report(0, size)
            await run_parts(parts, work, self._upload_budget, sleep=self._sleep)
        finally:
            await asyncio.to_thread(handle.close)
        return types.InputFileBig(id=file_id, parts=parts, name=path.name)

    async def _download(
        self, message: custom.Message, tmp: Path, on_transfer: OnTransfer | None = None
    ) -> Path:
        """The media of ``message`` in ``tmp``. A finished file is kept, so the same call after a
        FloodWait does not download it again; an interrupted one is only ever a ``.part``."""
        final = tmp / f"{message.id}{message.file.ext or ''}"
        if await asyncio.to_thread(final.exists):
            return final
        part = tmp / f"{message.id}.part"
        if part not in self._partial:  # what a cut-off transfer left is picked up again
            await asyncio.to_thread(_make_room, part)
        got: str | None = None
        if self._pooled(message):
            try:
                got = await self._download_parallel(message, part, on_transfer)
            except _NoPool:
                self._partial.pop(part, None)
                await asyncio.to_thread(_make_room, part)
            except (FloodWait, Transient):
                raise  # the guard repeats the call: keep the file and the parts it has
            except BaseException:  # never leave a big half-made file on the disk
                self._partial.pop(part, None)
                await asyncio.to_thread(_make_room, part)
                raise
        if got is None:
            progress = _reporting(on_transfer, TransferPhase.DOWNLOAD, message.id)
            got = await self._client.download_media(
                message,
                file=str(part),
                **({"progress_callback": progress} if progress else {}),
            )
        if got is None:
            raise PerMessage("download_failed")
        await asyncio.to_thread(_publish, got, final)
        return final

    async def _thumbnail(self, message: custom.Message, tmp: Path) -> Path | None:
        """The cover of a video, when Telegram has one (a stripped preview is too small to use)."""
        if not (message.video or message.gif or message.video_note):
            return None
        sizes = [
            s
            for s in message.document.thumbs or []
            if isinstance(s, types.PhotoSize | types.PhotoCachedSize)
        ]
        if not sizes:
            return None
        final = tmp / f"{message.id}.thumb.jpg"
        if await asyncio.to_thread(final.exists):
            return final
        part = tmp / f"{message.id}.thumb.part"
        await asyncio.to_thread(_make_room, part)
        best = max(sizes, key=lambda s: s.w * s.h)
        got = await self._client.download_media(message, file=str(part), thumb=best)
        if got is None:
            return None  # a missing cover is not worth failing the video for
        await asyncio.to_thread(_publish, got, final)
        return final

    async def _pooled_upload(self, item: _Item) -> bool:
        """A single document big enough for the big-file API and the request pool."""
        if self._upload_budget is None or item.path is None or item.message.document is None:
            return False
        size = (await asyncio.to_thread(item.path.stat)).st_size
        return (
            size >= BIG_FILE
            and size >= self._transfer.min_bytes
            and math.ceil(size / UP_PART) <= MAX_UP_PARTS
        )

    async def upload_prepared(
        self, prepared: Prepared, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        fetched = prepared.handle
        assert isinstance(fetched, _Fetched)
        if prepared.uploaded or len(fetched.items) != 1:
            return prepared  # an album is uploaded by the post itself
        item = fetched.items[0]
        if item.path is None or not await self._pooled_upload(item):
            return prepared
        with mapped_errors():
            handle = await self._upload_parallel(item.path, item.message.id, on_transfer)
        return Prepared(
            prepared.unit,
            prepared.files,
            _Fetched((replace(item, uploaded=handle),)),
            uploaded=True,
        )

    async def send_prepared(
        self,
        dst: int,
        prepared: Prepared,
        caption: CaptionPolicy,
        on_transfer: OnTransfer | None = None,
        *,
        topic: int | None = None,
    ) -> list[int]:
        fetched = prepared.handle
        assert isinstance(fetched, _Fetched)
        peer = await self._peer(dst)
        with mapped_errors():
            if len(fetched.items) > 1:
                return await self._send_album(peer, fetched.items, caption, on_transfer, topic)
            return [await self._send_one(peer, fetched.items[0], caption, on_transfer, topic)]

    async def _send_one(
        self,
        peer: object,
        item: _Item,
        caption: CaptionPolicy,
        on_transfer: OnTransfer | None = None,
        topic: int | None = None,
    ) -> int:
        message = item.message
        text, entities = message.message or "", list(message.entities or [])
        media = message.media
        if item.path is not None:
            text, entities = rewrite_caption(text, entities, caption, _source_names(message))
            extra: dict[str, object] = {}
            if (doc := message.document) is not None:
                kind = media_kind(message)
                # The attributes are what make it a video, a voice note, a sticker, ... at the
                # destination. ``force_document`` must NOT be set for those: Telethon turns it into
                # ``force_file`` and Telegram then shows the upload as a plain file whatever the
                # attributes say. Only what already was a plain file at the source is forced (it
                # also keeps a .jpg document from being re-sent as a photo).
                extra = {
                    "attributes": list(doc.attributes),
                    "mime_type": doc.mime_type,
                    "force_document": kind is MediaKind.DOCUMENT,
                    "supports_streaming": bool(message.video),
                }
                if kind is MediaKind.VIDEO:
                    # without it Telegram turns a video with no sound track into a GIF
                    extra["nosound_video"] = True
            source: Any = str(item.path)
            if item.uploaded is not None:  # the bytes went up before: only post them
                source = item.uploaded
            elif await self._pooled_upload(item):
                # the bytes go up in parallel, then the message is posted with what was uploaded
                source = await self._upload_parallel(item.path, message.id, on_transfer)
            elif progress := _reporting(on_transfer, TransferPhase.UPLOAD, message.id):
                extra["progress_callback"] = progress
            sent = await self._client.send_file(
                peer,
                source,
                caption=text,
                formatting_entities=entities or None,
                parse_mode=None,  # the entities are the formatting: nothing to parse as markdown
                thumb=None if item.thumb is None else str(item.thumb),
                reply_to=topic,
                **extra,
            )
        elif media is None or isinstance(media, types.MessageMediaWebPage):
            # ``CaptionPolicy.mode`` never touches plain text (it is the content itself), but a
            # topic hashtag (phase 8) still applies: it is tgmirror's own addition, not part of
            # the original message.
            if caption.hashtag:
                text = f"{text}\n{caption.hashtag}" if text else caption.hashtag
            sent = await self._client.send_message(
                peer,
                text,
                formatting_entities=entities or None,
                parse_mode=None,
                link_preview=media is not None,
                reply_to=topic,
            )
        elif isinstance(media, _SELF_CONTAINED):
            sent = await self._client.send_message(
                peer,
                text,
                file=media,
                formatting_entities=entities or None,
                parse_mode=None,
                reply_to=topic,
            )
        else:
            raise PerMessage(f"unsupported_media:{type(media).__name__}")
        return int(sent.id)

    async def _upload_one(
        self, path: Path, on_transfer: OnTransfer | None
    ) -> types.InputFile | types.InputFileBig:
        """A file too small to be worth the pool (or a thumbnail, always): Telethon's own
        single-connection upload. ``on_transfer``, when given, is ``OnTransfer``-shaped already
        (a caller wrapping a per-item slice of a bigger transfer), not the raw ``(done, total)``
        Telethon calls its own ``progress_callback`` with."""
        callback = None
        if on_transfer is not None:

            def callback(done: float, total: float) -> None:
                on_transfer(TransferPhase.UPLOAD, 0, int(done), int(total))

        return await self._client.upload_file(str(path), progress_callback=callback)

    def _album_progress(
        self, on_transfer: OnTransfer | None, msg_id: int, total: int, base: int
    ) -> OnTransfer | None:
        """One album item's upload progress folded into the album's running byte total, reported
        under the album's own first message id (``TransferTracker``'s contract for an album)."""
        if on_transfer is None or not total:
            return None

        def callback(phase: TransferPhase, _msg_id: int, done: int, _total: int) -> None:
            with contextlib.suppress(Exception):
                on_transfer(phase, msg_id, base + done, total)

        return callback

    async def _album_media(
        self, peer: object, item: _Item, on_transfer: OnTransfer | None
    ) -> types.TypeInputMedia:
        """One album member's bytes, uploaded (through the pool when big enough) and turned into
        media ``SendMultiMediaRequest`` will actually accept: it refuses a bare
        ``InputMediaUploadedDocument``/``Photo`` (``MediaInvalidError``), so ``UploadMediaRequest``
        converts it first, same as Telethon's own album path does."""
        message = item.message
        assert item.path is not None
        kind = media_kind(message)
        fm: types.TypeInputMedia
        if kind is MediaKind.PHOTO:
            handle = await self._upload_one(item.path, on_transfer)
            fm = types.InputMediaUploadedPhoto(file=handle)
        else:
            doc = message.document
            assert doc is not None
            if await self._pooled_upload(item):
                handle = await self._upload_parallel(item.path, message.id, on_transfer)
            else:
                handle = await self._upload_one(item.path, on_transfer)
            thumb = None if item.thumb is None else await self._upload_one(item.thumb, None)
            fm = types.InputMediaUploadedDocument(
                file=handle,
                mime_type=doc.mime_type,
                attributes=list(doc.attributes),
                thumb=thumb,
                force_file=kind is MediaKind.DOCUMENT,
                nosound_video=True if kind is MediaKind.VIDEO else None,
            )
        uploaded = await self._client(UploadMediaRequest(peer, media=fm))
        got = uploaded.photo if kind is MediaKind.PHOTO else uploaded.document
        return utils.get_input_media(got)

    async def _send_album(
        self,
        peer: object,
        items: Sequence[_Item],
        caption: CaptionPolicy,
        on_transfer: OnTransfer | None = None,
        topic: int | None = None,
    ) -> list[int]:
        total = _album_share(items)
        album_id = items[0].message.id
        done = 0
        media: list[types.InputSingleMedia] = []
        captions = _album_captions([item.message for item in items], caption)
        for item, (text, entities) in zip(items, captions, strict=True):
            progress = self._album_progress(on_transfer, album_id, total, done)
            fm = await self._album_media(peer, item, progress)
            done += _album_share((item,))
            media.append(types.InputSingleMedia(fm, message=text, entities=entities or None))
        reply_to = (
            None
            if topic is None
            else types.InputReplyToMessage(reply_to_msg_id=topic, top_msg_id=topic)
        )
        result = await self._client(
            SendMultiMediaRequest(peer, multi_media=media, reply_to=reply_to)
        )
        has_updates = types.Updates | types.UpdatesCombined
        updates = result.updates if isinstance(result, has_updates) else []
        id_map = {u.random_id: u.id for u in updates if isinstance(u, types.UpdateMessageID)}
        return [id_map[m.random_id] for m in media]

    async def send_text(self, dst: int, text: str, *, topic: int | None = None) -> int:
        peer = await self._peer(dst)
        with mapped_errors():
            sent = await self._client.send_message(
                peer, text, parse_mode=None, link_preview=False, reply_to=topic
            )
        return int(sent.id)


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
    gateway = TelethonGateway(client, TransferSettings.of(config.limits))
    try:
        yield TelethonAuth(client), gateway
    finally:
        await gateway.aclose()
        await client.disconnect()
