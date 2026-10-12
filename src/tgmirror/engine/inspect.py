"""``tgmirror inspect`` (docs/07-inspect-transform.md, phase 18a): what a source holds and what a
filter selects, without writing anything anywhere.

Pure on any ``MessageReader`` — the live gateway (behind ``FloodGuard``) or a ``BackupReader`` over
a backup directory — so one report serves a channel and a backup alike, and the filter is judged by
the same ``Matcher`` ``clone``/``backup``/``restore`` use: what inspect counts is what they copy.

Everything is counted per *message* (an album's members each count, like they do when copied);
``Totals.units`` says how many copy units that is. Tables are built over the selection (the whole
source when there is no filter). A table's rows carry keys, never user-facing words: ``ui/`` turns
the two sentinels (``NONE``) into text.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tgmirror.core.gateway import GENERAL_TOPIC_ID, MessageReader, SrcMessage, Unit
from tgmirror.engine import planner
from tgmirror.filters.matcher import Matcher

BY_CHOICES = (
    "media",
    "month",
    "year",
    "hashtag",
    "topic",
    "sender",
    "forward",
    "domain",
    "size",
    "duplicate",
)
DEFAULT_BY = ("media", "month", "hashtag")
DEFAULT_TOP = 10
NONE = "-"  # the key of "no sender" / "not a forward": the UI says it in words

_MB = 1024**2
_GB = 1024**3
# (label, exclusive upper bound); the label is also the key, and the order is the table's order
SIZE_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("<1MB", _MB),
    ("1-10MB", 10 * _MB),
    ("10-100MB", 100 * _MB),
    ("100MB-1GB", _GB),
    ("1-2GB", 2 * _GB),
    (">2GB", None),
)
# tables that read as a sequence, not a ranking: all their rows, in their own order, never "top N"
_SEQUENCE = frozenset({"month", "year", "size"})

LIST_COLUMNS = (
    "id",
    "date",
    "media",
    "size",
    "grouped_id",
    "topic_id",
    "from_user_id",
    "forwarded_from",
    "reply_to_id",
    "edited",
    "views",
    "forwards",
    "reactions",
    "domains",
    "file_name",
    "file_key",
    "text",
    "link",
)
TEXT_WIDTH = 200  # how much of a caption goes in a list row


@dataclass(frozen=True, slots=True)
class Totals:
    messages: int = 0
    units: int = 0
    albums: int = 0
    bytes: int = 0
    first: datetime | None = None  # date of the oldest message
    last: datetime | None = None  # date of the newest


@dataclass(frozen=True, slots=True)
class Row:
    key: str
    messages: int
    bytes: int
    name: str | None = None  # a readable name for the key, when the data had one


@dataclass(frozen=True, slots=True)
class Table:
    by: str
    rows: tuple[Row, ...]
    other: Row | None = None  # the rows ``top`` cut off, added up (``key`` holds how many)


@dataclass(frozen=True, slots=True)
class Report:
    scanned: Totals  # everything that was read
    selected: Totals  # the part the filter selects (== scanned without a filter)
    tables: tuple[Table, ...]
    last_id: int  # the highest message id read
    complete: bool  # False when it was stopped before the end of the source
    filtered: bool  # a filter was applied


# ---- grouping ---------------------------------------------------------------------------------


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _size_bucket(size: int) -> str:
    for label, below in SIZE_BUCKETS:
        if below is None or size < below:
            return label
    raise AssertionError("unreachable")  # pragma: no cover


def _keys(
    by: str, m: SrcMessage, *, forum: bool, topic_titles: Mapping[int, str]
) -> list[tuple[str, str | None]]:
    """The ``(key, name)`` pairs a message adds one to under ``by`` (several for hashtag/domain,
    none when the message has nothing to group by, e.g. no file for ``size``)."""
    match by:
        case "media":
            return [(m.media.value, None)]
        case "month":
            return [(_aware(m.date).astimezone(UTC).strftime("%Y-%m"), None)]
        case "year":
            return [(str(_aware(m.date).astimezone(UTC).year), None)]
        case "hashtag":
            return [(h, None) for h in dict.fromkeys(m.hashtags)]
        case "topic":
            if not forum:
                return []
            topic = m.topic_id or GENERAL_TOPIC_ID
            return [(str(topic), topic_titles.get(topic))]
        case "sender":
            return [(NONE if m.from_user_id is None else str(m.from_user_id), None)]
        case "forward":
            if not m.is_forward:
                return [(NONE, None)]
            if m.fwd_from_id is not None:
                return [(str(m.fwd_from_id), m.fwd_from_name)]
            return [(f"name:{m.fwd_from_name or '?'}", m.fwd_from_name)]
        case "domain":
            return [(d, None) for d in m.link_domains]
        case "size":
            return [] if m.size is None else [(_size_bucket(m.size), None)]
        case "duplicate":
            return [] if m.file_key is None else [(m.file_key, m.file_name)]
    raise ValueError(f"unknown grouping {by!r}")


@dataclass(slots=True)
class _Tally:
    messages: int = 0
    units: int = 0
    albums: int = 0
    bytes: int = 0
    first: datetime | None = None
    last: datetime | None = None

    def add(self, unit: Unit) -> None:
        self.units += 1
        self.albums += unit.is_album
        for m in unit.messages:
            self.messages += 1
            self.bytes += m.size or 0
            date = _aware(m.date)
            if self.first is None or date < self.first:
                self.first = date
            if self.last is None or date > self.last:
                self.last = date

    def totals(self) -> Totals:
        return Totals(self.messages, self.units, self.albums, self.bytes, self.first, self.last)


class Inspector:
    """Collects a stream of units into a ``Report``: feed it with ``add``, ask ``report``."""

    def __init__(
        self,
        matcher: Matcher | None = None,
        by: Sequence[str] = DEFAULT_BY,
        *,
        forum: bool = False,
        topic_titles: Mapping[int, str] | None = None,
        on_selected: Callable[[Unit], None] | None = None,
    ) -> None:
        unknown = [b for b in by if b not in BY_CHOICES]
        if unknown:
            raise ValueError(f"unknown grouping {unknown[0]!r}")
        self._matcher = matcher
        self._by = tuple(dict.fromkeys(by))
        self._forum = forum
        self._titles = topic_titles or {}
        self._on_selected = on_selected
        self._scanned = _Tally()
        self._selected = _Tally()
        self._last_id = 0
        self._counts: dict[str, Counter[str]] = {b: Counter() for b in self._by}
        self._bytes: dict[str, Counter[str]] = {b: Counter() for b in self._by}
        self._names: dict[str, dict[str, str]] = {b: {} for b in self._by}

    @property
    def scanned_messages(self) -> int:
        return self._scanned.messages

    @property
    def last_id(self) -> int:
        return self._last_id

    def add(self, unit: Unit) -> None:
        self._scanned.add(unit)
        self._last_id = max(self._last_id, unit.messages[-1].id)
        if self._matcher is not None and not self._matcher.matches(unit):
            return
        self._selected.add(unit)
        if self._on_selected is not None:
            self._on_selected(unit)
        for m in unit.messages:
            for by in self._by:
                for key, name in _keys(by, m, forum=self._forum, topic_titles=self._titles):
                    self._counts[by][key] += 1
                    self._bytes[by][key] += m.size or 0
                    if name and key not in self._names[by]:
                        self._names[by][key] = name

    def report(self, *, top: int = DEFAULT_TOP, complete: bool = True) -> Report:
        return Report(
            scanned=self._scanned.totals(),
            selected=self._selected.totals(),
            tables=tuple(self._table(by, top) for by in self._by),
            last_id=self._last_id,
            complete=complete,
            filtered=self._matcher is not None,
        )

    def _table(self, by: str, top: int) -> Table:
        counts = self._counts[by]
        if by == "duplicate":  # a file seen once is no duplicate
            counts = Counter({k: n for k, n in counts.items() if n >= 2})
        rows = [Row(k, n, self._bytes[by][k], self._names[by].get(k)) for k, n in counts.items()]
        if by in _SEQUENCE:
            order = [label for label, _ in SIZE_BUCKETS]
            rows.sort(key=lambda r: order.index(r.key) if by == "size" else r.key)
            return Table(by, tuple(rows))
        rows.sort(key=lambda r: (-r.messages, r.key))
        if top <= 0 or len(rows) <= top:
            return Table(by, tuple(rows))
        rest = rows[top:]
        other = Row(str(len(rest)), sum(r.messages for r in rest), sum(r.bytes for r in rest))
        return Table(by, tuple(rows[:top]), other)


async def scan(
    reader: MessageReader,
    src: int,
    inspector: Inspector,
    *,
    stop: Callable[[], bool] = lambda: False,
    on_progress: Callable[[Inspector], None] | None = None,
) -> bool:
    """Feed every unit of ``src`` to ``inspector``, oldest first; ``False`` if ``stop`` ended it.

    No filter is pushed to the server: the report has to describe the whole source (what it holds
    next to what the filter selects), so the whole source is read.
    """
    async with aclosing(planner.units(reader, src)) as stream:
        async for item in stream:
            assert isinstance(item, Unit)  # no matcher given: nothing is skipped
            inspector.add(item)
            if on_progress is not None:
                on_progress(inspector)
            if stop():
                return False
    return True


# ---- rows of messages (--list, --csv, --json) -------------------------------------------------


def message_link(src_id: int, username: str | None, msg_id: int) -> str | None:
    """``https://t.me/<username>/<id>``, or the ``t.me/c/`` form of a private channel; ``None``
    for a basic group (it has no link)."""
    if username:
        return f"https://t.me/{username}/{msg_id}"
    text = str(src_id)
    if text.startswith("-100"):
        return f"https://t.me/c/{text[4:]}/{msg_id}"
    return None


def describe_message(message: SrcMessage, *, link: str | None = None) -> dict[str, Any]:
    """One list/CSV row of ``LIST_COLUMNS``; an empty value is ``None`` (JSON null, empty cell)."""
    forwarded: str | None = None
    if message.is_forward:
        forwarded = str(message.fwd_from_id) if message.fwd_from_id is not None else None
        if forwarded is None:
            forwarded = message.fwd_from_name
    text = " ".join(message.text.split())
    return {
        "id": message.id,
        "date": _aware(message.date).astimezone(UTC).isoformat(timespec="seconds"),
        "media": message.media.value,
        "size": message.size,
        "grouped_id": message.grouped_id,
        "topic_id": message.topic_id,
        "from_user_id": message.from_user_id,
        "forwarded_from": forwarded,
        "reply_to_id": message.reply_to_id,
        "edited": message.edit_date is not None,
        "views": message.views,
        "forwards": message.forwards,
        "reactions": message.reactions,
        "domains": ";".join(message.link_domains) or None,
        "file_name": message.file_name,
        "file_key": message.file_key,
        "text": text[:TEXT_WIDTH] or None,
        "link": link,
    }


def report_to_dict(report: Report) -> dict[str, Any]:
    """The report as plain JSON-able data (``tgmirror inspect --json``)."""

    def totals(t: Totals) -> dict[str, Any]:
        return {
            "messages": t.messages,
            "units": t.units,
            "albums": t.albums,
            "bytes": t.bytes,
            "first": t.first.isoformat(timespec="seconds") if t.first else None,
            "last": t.last.isoformat(timespec="seconds") if t.last else None,
        }

    def row(r: Row) -> dict[str, Any]:
        return {"key": r.key, "name": r.name, "messages": r.messages, "bytes": r.bytes}

    return {
        "complete": report.complete,
        "filtered": report.filtered,
        "last_id": report.last_id,
        "scanned": totals(report.scanned),
        "selected": totals(report.selected),
        "tables": {
            t.by: {
                "rows": [row(r) for r in t.rows],
                "other": None
                if t.other is None
                else {
                    "count": int(t.other.key),
                    "messages": t.other.messages,
                    "bytes": t.other.bytes,
                },
            }
            for t in report.tables
        },
    }
