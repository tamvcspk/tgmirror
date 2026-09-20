"""The ``TelegramGateway`` protocol and the plain data types that cross it.

Engine code depends on this module only, never on Telethon (hard rule 8, decision D9). Every
Telegram network call is implemented behind this protocol and goes through the limiter (rule 1).
The types live here rather than in ``engine/`` because the protocol itself refers to them.
"""

from collections.abc import AsyncIterator, Callable, Sequence
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
    quiz_unanswered: bool = False  # a quiz whose right answer this account cannot see yet
    title: str | None = None  # game/invoice title or poll question, for a placeholder text


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


ALBUM_MARGIN = 10  # an album has at most 10 messages, with consecutive ids
MAX_IDS_PER_CALL = 100  # Telegram takes at most 100 ids in one forward or one read by id


@dataclass(frozen=True, slots=True)
class ServerFilter:
    """The part of a filter Telegram can evaluate for us (docs/03-filters.md, "Server pushdown").

    It may only *narrow safely*: implementations return a superset of the true matches, and the
    client matcher runs again on the result. ``None`` means "no restriction".

    Two kinds of narrowing need care because an album must stay whole (hard rule 4):

    - ``since``/``until`` are dates, so only the gateway can turn them into positions. It starts
      ``ALBUM_MARGIN`` ids before the first message at ``since`` and stops ``ALBUM_MARGIN`` ids
      after the first message at ``until``, so an album on either boundary is returned whole.
    - ``media``/``search`` drop the members of an album that do not match themselves. The caller
      (``engine/planner.py``) completes such albums with a second, unfiltered read.
    """

    media: MediaKind | None = None
    search: str | None = None
    since: datetime | None = None  # oldest date wanted (inclusive)
    until: datetime | None = None  # first date not wanted (exclusive)
    max_id: int | None = None  # highest id to include


NO_FILTER = ServerFilter()


class CaptionMode(StrEnum):
    """What strategy B does with the caption of a media message (``--caption``)."""

    KEEP = "keep"
    STRIP_LINKS = "strip-links"  # drop links and mentions that point back at the source
    APPEND = "append"  # add ``CaptionPolicy.text`` after the caption
    NONE = "none"  # send the media without its caption


@dataclass(frozen=True, slots=True)
class CaptionPolicy:
    """How captions are rewritten while re-sending. Only captions of media messages: the text of a
    message that has no media is the content itself and is always sent as it is."""

    mode: CaptionMode = CaptionMode.KEEP
    text: str = ""  # what ``APPEND`` adds


@dataclass(frozen=True, slots=True)
class Prepared:
    """A unit fetched for strategy B: its messages read again and its media downloaded.

    ``files`` are the temporary files the caller must delete once the unit is sent or given up on;
    ``handle`` is private to the gateway that made it.
    """

    unit: Unit
    files: tuple[Path, ...] = ()
    handle: object = None


class TransferPhase(StrEnum):
    DOWNLOAD = "download"
    UPLOAD = "upload"


# What a transfer reports while it runs: ``(phase, message id, bytes done, bytes in all)``. It is
# called from the event loop, often (once per part), so it must be cheap and never raise. An album
# reports under the id of its first message, for all its files together.
OnTransfer = Callable[[TransferPhase, int, int, int], None]


class MessageReader(Protocol):
    """The read side of the gateway: all that the planner, preview and reconcile need.

    ``FloodGuard`` (``engine/flood.py``) wraps one to pace and retry reads.
    """

    def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        """Messages with ``id > min_id``, ascending (decision D4), narrowed by ``filters``."""
        ...

    async def count(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int:
        """How many messages ``iter_messages`` would (roughly) return, from one cheap request.

        It is what progress is measured against, so an *upper bound* is the honest answer: service
        messages are counted, and whatever the client-side filter drops later is not subtracted.
        """
        ...

    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]:
        """The messages with these ids (one request: 1..``MAX_IDS_PER_CALL`` ids), ascending.

        An id whose message no longer exists is simply absent from the result. ``retry`` uses it to
        read what it must send again without scanning the source.
        """
        ...

    async def fetch(self, src: int, unit: Unit) -> Prepared:
        """Sending by reference, the read half: read the unit's messages again so their file
        references are fresh. Nothing is downloaded (``Prepared.files`` is empty). Raises
        ``PerMessage`` when a message is gone."""
        ...

    async def prepare(
        self, src: int, unit: Unit, tmp: Path, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        """Strategy B, the read half: read the unit's messages again and download their media
        into ``tmp`` (one request to find them, then the downloads), telling ``on_transfer`` how
        far each download is.

        A file already downloaded there is reused, so repeating the call after a FloodWait does
        not fetch it twice. Raises ``PerMessage`` when a message is gone or has nothing to send.
        """
        ...


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

    async def count(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int:
        """As ``MessageReader.count``."""
        ...

    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]:
        """As ``MessageReader.get_messages``."""
        ...

    async def last_message_id(self, chat: int) -> int:
        """Id of the newest message in ``chat`` (0 when it has none). One cheap read.

        The first run of a pair records it for the destination, so a later reconcile only reads what
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

    async def fetch(self, src: int, unit: Unit) -> Prepared:
        """As ``MessageReader.fetch``."""
        ...

    async def prepare(
        self, src: int, unit: Unit, tmp: Path, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        """As ``MessageReader.prepare``."""
        ...

    async def send_by_reference(
        self, dst: int, prepared: Prepared, caption: CaptionPolicy
    ) -> list[int]:
        """Sending by reference, the write half: post the unit as new messages by handing
        Telegram the id of each file it already stores, so nothing is downloaded or uploaded
        (an album stays one album). Returns the new ids aligned with ``prepared.unit``;
        ``caption`` rewrites the captions. Raises ``FileRefExpired`` when the media cannot be
        sent this way (the reference expired, or Telegram will not reuse it)."""
        ...

    async def send_prepared(
        self,
        dst: int,
        prepared: Prepared,
        caption: CaptionPolicy,
        on_transfer: OnTransfer | None = None,
    ) -> list[int]:
        """Strategy B, the write half: send the unit as new messages (one album for an album, text
        for text, the poll/location/contact itself for those) and return their ids in the
        destination, aligned with ``prepared.unit``. ``caption`` rewrites the captions of media;
        ``on_transfer`` hears how far the upload is."""
        ...

    async def send_text(self, dst: int, text: str) -> int:
        """Post a plain text message (the stub that stands for what cannot be copied)."""
        ...
