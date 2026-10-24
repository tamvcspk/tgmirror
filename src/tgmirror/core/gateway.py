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
    OTHER = "other"  # no file, no name of its own: checklist, story, paid media, giveaway
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


# A forum's General topic. Its messages carry no ``reply_to`` in Telethon, so their
# ``SrcMessage.topic_id`` is ``None`` (docs/06-lo-trinh.md, open question 9), not this id.
GENERAL_TOPIC_ID = 1


@dataclass(frozen=True, slots=True)
class TopicInfo:
    """A forum topic (docs/01-kien-truc.md, "Ánh xạ topic"). ``id`` 1 is always General."""

    id: int
    title: str
    closed: bool = False


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
    topic_id: int | None = None  # forum topic this message belongs to (1 = General); phase 8
    from_user_id: int | None = None  # sender of a group/forum message; phase 8
    # --- phase 18a: everything a report or a predicate looks at, all free in a live read ---
    is_forward: bool = False  # carries a "Forwarded from" header
    fwd_from_id: int | None = None  # marked peer id of the original channel/user, when visible
    fwd_from_name: str | None = None  # its title/name, or the hidden name a user chose to show
    reply_to_id: int | None = None  # a genuine reply (not the topic a forum message sits in)
    edit_date: datetime | None = None  # when it was last edited, ``None`` if never
    link_domains: tuple[str, ...] = ()  # distinct domains of the links in text/caption
    file_name: str | None = None  # the media file's own name, as the sender uploaded it
    file_key: str | None = None  # stable file id (``d<id>``/``p<id>``): same file, same key
    forwards: int | None = None  # how many times the post was forwarded
    reactions: int | None = None  # all reactions together
    post_author: str | None = None  # signature of a channel post
    via_bot_id: int | None = None  # the inline bot a message was sent through
    pinned: bool = False
    # --- phase 19c3: ids of the messages *of this same chat* its text links to (``links: rewrite``)
    own_links: tuple[int, ...] = ()


# the kinds ``messages.getSearchCounters`` answers for (spike 1, docs/07 section 9), in the order
# a report lists them; contacts, polls and pinned messages are not answered at all
COUNTER_KEYS = ("photo", "video", "document", "audio", "voice", "video_note", "gif", "link", "geo")


@dataclass(frozen=True, slots=True)
class MediaCounters:
    """How many messages of each kind *Telegram says* a chat (or one forum topic) holds, from one
    request. The numbers are Telegram's own classification, so they can differ a little from what
    a scan counts (a message the server hides behind a notice still counts as a video): a quick
    look, not a base for deciding what a filter selects. A kind the server did not answer for is
    absent from ``counts``."""

    counts: dict[str, int]
    topic_id: int | None = None


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

    @property
    def topic_id(self) -> int | None:
        return self.messages[0].topic_id

    @property
    def own_links(self) -> tuple[int, ...]:
        """The messages of the same chat that this unit's messages link to (phase 19c3)."""
        return tuple(sorted({i for m in self.messages for i in m.own_links}))

    @property
    def reply_to_id(self) -> int | None:
        """The source message this unit answers (phase 19c): every message of an album carries the
        same reply header, so the first one that has one says."""
        return next((m.reply_to_id for m in self.messages if m.reply_to_id is not None), None)


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


TRANSFORM_REGEX_TIMEOUT = 0.5  # seconds for one ``replace`` search; a transform may be shared


@dataclass(frozen=True, slots=True)
class StripLinks:
    """``strip_links``: drop what points back at the source (``"source"``, what ``--caption
    strip-links`` does) or every link (``"all"``); a hyperlink keeps its words."""

    scope: str = "source"  # "source" | "all"


@dataclass(frozen=True, slots=True)
class RemoveHashtags:
    """``remove_hashtags``: the given hashtags (lower-case, with ``#``), or every hashtag when
    ``tags`` is empty."""

    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RemoveMentions:
    """``remove_mentions``: ``@name`` mentions of the source (``"source"``) or all of them."""

    scope: str = "source"  # "source" | "all"


@dataclass(frozen=True, slots=True)
class Replace:
    """``replace``: every match of ``pattern`` (the ``regex`` library, a search with a timeout)
    becomes ``with_``, taken literally."""

    pattern: str
    with_: str = ""


@dataclass(frozen=True, slots=True)
class Append:
    """``append``: ``text`` after the caption (a caption that is empty stays empty)."""

    text: str


@dataclass(frozen=True, slots=True)
class Clear:
    """``clear``: send the media without its caption (``--caption none``)."""


CaptionOp = StripLinks | RemoveHashtags | RemoveMentions | Replace | Append | Clear


def ops_of_mode(mode: CaptionMode, text: str = "") -> tuple[CaptionOp, ...]:
    """The ``--caption`` flag as the list of operations it is a shorthand for."""
    match mode:
        case CaptionMode.STRIP_LINKS:
            return (StripLinks("source"), RemoveMentions("source"))
        case CaptionMode.APPEND:
            return (Append(text),)
        case CaptionMode.NONE:
            return (Clear(),)
        case _:
            return ()


@dataclass(frozen=True, slots=True)
class LinkRewrite:
    """``links: rewrite`` for one unit (phase 19c3): a link to a message of the source becomes a
    link to ``mapping[id]`` in the destination, whose links start ``prefix``. Built by the runner
    from ``msg_map``; a link whose message is not in ``mapping`` is left as it is."""

    mapping: tuple[tuple[int, int], ...]  # (source message id, destination message id)
    prefix: str  # ``t.me/<username>`` or ``t.me/c/<number>``


@dataclass(frozen=True, slots=True)
class CaptionPolicy:
    """How captions are rewritten while re-sending: ``ops`` (a transform's ``caption`` list, in
    order), then ``mode``/``text`` (the ``--caption`` shorthand, a flag layered on top). They
    only ever touch captions of media messages — the text of a message that has no media is the
    content itself and is sent as it is — unless ``text_too`` says the transform wants that text
    rewritten as well.

    ``hashtag`` (phase 8, ``RunOptions.topic_as_hashtag``) is different: it is appended last,
    after whatever the operations produced, to *any* text that ends up being sent (media caption
    or plain text alike) — except self-contained media (poll, location, contact, geo, dice)
    which have no caption slot to append to at all.
    """

    mode: CaptionMode = CaptionMode.KEEP
    text: str = ""  # what ``APPEND`` adds
    hashtag: str | None = None  # e.g. "#general", appended last regardless of ``mode``
    ops: tuple[CaptionOp, ...] = ()  # phase 19: the transform's caption operations
    text_too: bool = False  # phase 19: rewrite the text of a plain text message as well
    # phase 19c3: links to messages of the source, re-pointed at their copies. Runs *before* the
    # operations and reaches plain text too (a link is what a text message is made of).
    links: LinkRewrite | None = None

    @property
    def all_ops(self) -> tuple[CaptionOp, ...]:
        # a transform file is the base and ``--caption`` is a flag put on top of it: the file's
        # operations run first, the flag's after them (docs/02-cli-ux.md, "Cờ chồng lên file")
        return self.ops + ops_of_mode(self.mode, self.text)

    @property
    def rewrites(self) -> bool:
        return bool(self.all_ops)


@dataclass(frozen=True, slots=True)
class Rewrite:
    """A message's text now (``before``) and as a ``CaptionPolicy`` would leave it (``after``), for
    ``tgmirror edit`` (phase 20.3). Both are in the gateway's own markup (Telegram's formatting
    written as HTML, as a backup keeps it): the engine only compares them, shows them and hands
    one back to ``edit_message``. ``lossless`` is ``False`` when writing ``before`` back would not
    reproduce the message's formatting exactly (an entity the markup cannot carry): such a
    message is never edited, because ``undo`` could not give it back."""

    id: int
    before: str
    after: str
    lossless: bool = True


@dataclass(frozen=True, slots=True)
class Prepared:
    """A unit fetched for strategy B: its messages read again and its media downloaded.

    ``files`` are the temporary files the caller must delete once the unit is sent or given up on;
    ``handle`` is private to the gateway that made it.
    """

    unit: Unit
    files: tuple[Path, ...] = ()
    handle: object = None
    uploaded: bool = False  # the bytes are already up: ``send_prepared`` only posts


@dataclass(frozen=True, slots=True)
class ExportedMedia:
    """A message's media, self-contained enough to write to ``messages.jsonl`` (phase 11, backup):
    either a downloaded file (``filename``, relative to the backup's ``media/``) or, for media
    Telegram carries in the message itself, the payload text/HTML cannot hold.

    Only one of the groups below is ever set, matching ``kind``. Restore (phase 11b) rebuilds the
    message from whichever one is there (``core.telethon_gateway``'s ``_send_from_backup_*``).
    """

    kind: MediaKind
    filename: str | None = None  # photo/video/document/audio/voice/gif/sticker/video_note
    mime: str | None = None
    size: int | None = None
    duration: float | None = None
    # the name/tags Telegram itself carried, as opposed to ``filename`` above (this backup's own
    # storage name, by message id, never the source's) — optional (C2, Phase 15b): a backup made
    # before this field existed simply has none, and restore then falls back to what it always
    # did (Telegram/hachoir detecting them from the file itself).
    original_filename: str | None = None
    file_key: str | None = None  # ``SrcMessage.file_key`` (phase 18a): same file, same key
    audio_title: str | None = None
    audio_performer: str | None = None
    # poll/quiz
    poll_question: str | None = None
    poll_options: tuple[str, ...] = ()
    poll_quiz: bool = False
    poll_correct_option: int | None = None  # index into ``poll_options``, when this account sees it
    # geo/venue
    geo_lat: float | None = None
    geo_lon: float | None = None
    venue_title: str | None = None
    # contact
    contact_phone: str | None = None
    contact_first_name: str | None = None
    contact_last_name: str | None = None
    # game/invoice: only a title to stand in for it (docs/06-lo-trinh.md, "Không restore được")
    title: str | None = None


@dataclass(frozen=True, slots=True)
class ExportedMessage:
    """One message as ``tgmirror backup`` writes it to ``messages.jsonl`` (phase 11): the source
    text as Telethon's own HTML (so formatting/links survive), and its media self-contained rather
    than by Telegram file reference (a backup outlives the reference)."""

    id: int
    date: datetime
    grouped_id: int | None
    topic_id: int | None
    from_user_id: int | None
    text_html: str
    views: int | None
    media: ExportedMedia | None = None
    # --- phase 18a: the same optional metadata ``SrcMessage`` carries, so ``inspect`` and the new
    # predicates work on a backup. Absent in a backup made before; all default to "not there".
    is_forward: bool = False
    fwd_from_id: int | None = None
    fwd_from_name: str | None = None
    reply_to_id: int | None = None
    edit_date: datetime | None = None
    link_domains: tuple[str, ...] = ()
    forwards: int | None = None
    reactions: int | None = None
    post_author: str | None = None
    via_bot_id: int | None = None
    pinned: bool = False
    own_links: tuple[int, ...] = ()  # phase 19c3; a backup made before has none


@dataclass(frozen=True, slots=True)
class FromBackup:
    """``Prepared.handle`` for a unit read from a backup directory (phase 11b, restore): its
    messages, described the way ``ExportedMessage``/``ExportedMedia`` already describe them for
    ``messages.jsonl``, plus where their files live. Defined here rather than in
    ``core/telethon_gateway.py`` so ``engine/backup_reader.py`` can build one without importing
    Telethon (hard rule 8)."""

    messages: tuple[ExportedMessage, ...]
    media_dir: Path
    src_id: int  # the original channel's id (``BackupManifest.src_id``), for caption link-stripping
    src_username: str | None = None  # what its links looked like (``BackupManifest``, 19c3)


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

    async def list_topics(self, src: int) -> list[TopicInfo]:
        """A forum's topics (phase 8), one paced read request. ``engine/topics.py`` uses it to
        name a destination topic the first time it must create one."""
        ...

    async def media_counters(self, src: int, topic_id: int | None = None) -> MediaCounters:
        """``tgmirror inspect --quick`` (phase 18b): the number of photos, videos, ... Telegram
        reports for the chat, or for one forum topic (1 = General), in one request. There is no
        range: a count "after id N" has to be read."""
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

    async def export_unit(
        self, src: int, unit: Unit, media_dir: Path, on_transfer: OnTransfer | None = None
    ) -> list[ExportedMessage]:
        """``tgmirror backup`` (phase 11), the read half: read the unit's messages again and
        download their media into ``media_dir`` (kept, never deleted by the caller), returning one
        ``ExportedMessage`` per message, aligned with ``unit.messages``. Like ``prepare``, a file
        already there is reused, so repeating the call after a FloodWait does not fetch it twice.
        Raises ``PerMessage`` when a message is gone."""
        ...


@runtime_checkable
class TelegramGateway(Protocol):
    async def list_channels(self) -> list[ChannelInfo]:
        """Channels, supergroups, forums and groups the account has joined."""
        ...

    async def get_channel(self, ref: int) -> ChannelInfo: ...

    async def create_channel(
        self, title: str, about: str = "", kind: ChatKind = ChatKind.BROADCAST
    ) -> ChannelInfo:
        """Create a channel/supergroup/forum the account administers.

        ``kind`` is never ``ChatKind.GROUP``: basic groups cannot be created through this API
        (``engine/endpoints.py`` asks for ``SUPERGROUP`` instead when the source is a ``GROUP``).
        """
        ...

    async def list_topics(self, src: int) -> list[TopicInfo]:
        """As ``MessageReader.list_topics``."""
        ...

    async def create_topic(self, dst: int, title: str) -> int:
        """Create a topic in a forum destination and return its id (forum destinations only)."""
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

    async def slowmode_seconds(self, chat: int) -> int:
        """The slow-mode interval of a supergroup (0: none, or a chat that has no such thing).

        One request, part of a run's one-time setup like ``last_message_id``: the limiter never
        posts to ``chat`` faster than this (phase 19d1).
        """
        ...

    async def is_premium(self) -> bool:
        """Whether the signed-in account has Telegram Premium (one request, run setup). It only
        changes what a FLOOD_PREMIUM_WAIT means, and is logged next to every flood (19d1)."""
        ...

    async def last_message_id(self, chat: int) -> int:
        """Id of the newest message in ``chat`` (0 when it has none). One cheap read.

        The first run of a pair records it for the destination, so a later reconcile only reads what
        was posted after that point (docs/04-state-checkpoint.md, "Resume").
        """
        ...

    async def copy_messages(
        self,
        src: int,
        dst: int,
        ids: list[int],
        *,
        topic: int | None = None,
        keep_header: bool = False,
    ) -> list[int | None]:
        """Strategy A: server-side copy without author (``keep_header``, phase 19c2: with the
        "Forwarded from" header, which is what a plain forward does). Result is aligned with
        ``ids``.

        A call that returns normally is authoritative: ``None`` means Telegram created no message
        for that id (deleted at the source, not forwardable). Raises ``PerMessage`` when Telegram
        rejects the request because of the ids themselves (nothing was created), so the caller can
        retry the units one by one. If the call is cut off (``Transient``) the outcome is unknown
        and only reconcile can tell (docs/04-state-checkpoint.md).

        ``topic``, when given, is the destination topic id (phase 8): the messages land there
        instead of the forum's General topic.
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

    async def export_unit(
        self, src: int, unit: Unit, media_dir: Path, on_transfer: OnTransfer | None = None
    ) -> list[ExportedMessage]:
        """As ``MessageReader.export_unit``."""
        ...

    async def send_by_reference(
        self,
        dst: int,
        prepared: Prepared,
        caption: CaptionPolicy,
        *,
        topic: int | None = None,
        reply_to: int | None = None,
    ) -> list[int]:
        """Sending by reference, the write half: post the unit as new messages by handing
        Telegram the id of each file it already stores, so nothing is downloaded or uploaded
        (an album stays one album). Returns the new ids aligned with ``prepared.unit``;
        ``caption`` rewrites the captions. Raises ``FileRefExpired`` when the media cannot be
        sent this way (the reference expired, or Telegram will not reuse it). ``topic`` targets a
        destination topic (phase 8); ``reply_to`` (phase 19c) is the id, in ``dst``, of the message
        the new one answers."""
        ...

    async def upload_prepared(
        self, prepared: Prepared, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        """Strategy B, the bytes: upload what can be uploaded before the unit is posted, and
        return the unit marked ``uploaded``. No message is created, so it can be repeated or
        abandoned (Telegram discards an upload that is never posted). A unit that cannot be
        uploaded ahead of its post (an album, a small file, nothing to upload) is returned
        as it is, and ``send_prepared`` does everything."""
        ...

    async def send_prepared(
        self,
        dst: int,
        prepared: Prepared,
        caption: CaptionPolicy,
        on_transfer: OnTransfer | None = None,
        *,
        topic: int | None = None,
        reply_to: int | None = None,
        silent: bool = False,
    ) -> list[int]:
        """Strategy B, the write half: send the unit as new messages (one album for an album, text
        for text, the poll/location/contact itself for those) and return their ids in the
        destination, aligned with ``prepared.unit``. ``caption`` rewrites the captions of media;
        ``on_transfer`` hears how far the upload is. A unit already ``uploaded`` is only
        posted. ``topic`` targets a destination topic (phase 8); ``reply_to`` (phase 19c) is the id,
        in ``dst``, of the message the new one answers (an album answers as a whole).
        ``silent`` (phase 20.4, ``repost``) posts without notifying the chat's followers."""
        ...

    async def send_text(
        self, dst: int, text: str, *, topic: int | None = None, reply_to: int | None = None
    ) -> int:
        """Post a plain text message (the stub that stands for what cannot be copied). ``topic``
        targets a destination topic (phase 8), ``reply_to`` is as in ``send_prepared``."""
        ...

    async def rewrite_messages(
        self, chat: int, ids: Sequence[int], policy: CaptionPolicy
    ) -> list[Rewrite]:
        """Read messages of ``chat`` by id and say what ``policy`` would make of their text
        (phase 20.3). Reads only. An id that is gone is left out; a message with no text slot (a
        poll, a service message) comes back with ``after == before``. An empty policy returns each
        message's text as it is now, which is how an edit is checked and settled."""
        ...

    async def edit_message(self, chat: int, msg_id: int, text: str) -> bool:
        """Replace the text (caption) of one message of ``chat`` with ``text``, in the markup
        ``rewrite_messages`` returns (phase 20.3, decision D10). ``True``: it was changed;
        ``False``: Telegram said it already read that, so repeating a call after a cut connection
        is safe. Raises ``PerMessage`` when this message cannot be edited (not the account's own,
        too old in a group, gone) and ``NoPermission`` when nothing in the chat can."""
        ...

    async def delete_messages(self, chat: int, ids: list[int]) -> int:
        """Delete messages of ``chat`` by id (phase 20, decision D10): 1..``MAX_IDS_PER_CALL`` ids
        in one request, for everybody (a channel has no other kind of delete). Returns how many
        messages Telegram says it deleted; an id that is already gone is no error and counts for
        nothing, so repeating a call after a cut connection is safe. Raises ``NoPermission`` when
        the account may not delete there."""
        ...
