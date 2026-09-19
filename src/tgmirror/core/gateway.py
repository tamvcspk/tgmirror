"""The ``TelegramGateway`` protocol and the plain data types that cross it.

Engine code depends on this module only, never on Telethon (hard rule 8, decision D9). Every
Telegram network call is implemented behind this protocol and goes through the limiter (rule 1).
The types live here rather than in ``engine/`` because the protocol itself refers to them.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable


class MediaKind(StrEnum):
    """Values of the ``media`` filter predicate (docs/03-filters.md)."""

    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"
    GIF = "gif"
    STICKER = "sticker"
    VIDEO_NOTE = "video_note"
    POLL = "poll"
    GEO = "geo"  # location and venue
    CONTACT = "contact"
    GAME = "game"
    INVOICE = "invoice"
    WEBPAGE = "webpage"
    TEXT = "text"  # text only, no media


class ChatKind(StrEnum):
    """What a source or destination is (docs/01-kien-truc.md, "Loại nguồn")."""

    BROADCAST = "broadcast"
    SUPERGROUP = "supergroup"
    FORUM = "forum"  # supergroup with topics
    GROUP = "group"  # legacy basic group


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    id: int  # marked peer id, e.g. -100... for channels and supergroups
    title: str
    kind: ChatKind = ChatKind.BROADCAST
    username: str | None = None
    participants: int | None = None
    noforwards: bool = False  # "Restrict saving content" (decision D3)
    is_admin: bool = False  # creator or admin
    can_post: bool = False


@dataclass(frozen=True, slots=True)
class SrcMessage:
    """A source message reduced to what the engine and the filters need."""

    id: int
    date: datetime
    text: str = ""
    media: MediaKind = MediaKind.TEXT
    grouped_id: int | None = None  # albums share one grouped_id
    is_service: bool = False  # join / pin / rename ... never cloned
    hashtags: tuple[str, ...] = ()
    size: int | None = None  # bytes of the media file
    duration: float | None = None  # seconds
    mime: str | None = None
    views: int | None = None


@dataclass(frozen=True, slots=True)
class Unit:
    """One message, or a whole album. Never split (hard rule 4, docs/01-kien-truc.md)."""

    messages: tuple[SrcMessage, ...]

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("a Unit needs at least one message")
        ids = [m.id for m in self.messages]
        if ids != sorted(set(ids)):
            raise ValueError("Unit messages must have strictly ascending ids")
        if len(self.messages) > 1:
            gid = self.messages[0].grouped_id
            if gid is None or any(m.grouped_id != gid for m in self.messages):
                raise ValueError("a multi-message Unit must be a single album (one grouped_id)")

    @property
    def ids(self) -> list[int]:
        return [m.id for m in self.messages]

    @property
    def grouped_id(self) -> int | None:
        return self.messages[0].grouped_id

    @property
    def is_album(self) -> bool:
        return self.grouped_id is not None


@dataclass(frozen=True, slots=True)
class ServerFilter:
    """The part of a filter Telegram can evaluate for us (docs/03-filters.md, "Server pushdown").

    It may only *narrow safely*: implementations return a superset of the true matches, and the
    client matcher runs again on the result. ``None`` means "no restriction".
    """

    media: MediaKind | None = None
    search: str | None = None
    since: datetime | None = None  # oldest date to include (Telethon: offset_date + reverse)
    max_id: int | None = None  # highest id to include


NO_FILTER = ServerFilter()


@runtime_checkable
class TelegramGateway(Protocol):
    async def list_channels(self) -> list[ChannelInfo]:
        """Channels, supergroups, forums and groups the account has joined."""
        ...

    async def get_channel(self, ref: int) -> ChannelInfo: ...

    async def create_channel(self, title: str, about: str = "") -> ChannelInfo:
        """Create a broadcast channel the account administers (other kinds: phase 8)."""
        ...

    def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        """Messages with ``id > min_id``, ascending (decision D4), narrowed by ``filters``."""
        ...

    async def last_message_id(self, chat: int) -> int:
        """Id of the newest message in ``chat`` (0 when it has none). One cheap read.

        A job records it for the destination at creation, so a later reconcile only reads what
        was posted after that point (docs/04-state-checkpoint.md, "Resume").
        """
        ...

    async def copy_messages(self, src: int, dst: int, ids: list[int]) -> list[int | None]:
        """Strategy A: server-side copy without author. Result is aligned with ``ids``.

        A call that returns normally is authoritative: ``None`` means Telegram created no message
        for that id (deleted at the source, not forwardable). Raises ``PerMessage`` when Telegram
        rejects the request because of the ids themselves (nothing was created), so the caller can
        retry the units one by one. If the call is cut off (``Transient``) the outcome is unknown
        and only reconcile can tell (docs/04-state-checkpoint.md).
        """
        ...

    async def reupload(self, src: int, dst: int, unit: Unit, tmp: Path) -> list[int]:
        """Strategy B: download then send again; returns the new ids in the destination."""
        ...
