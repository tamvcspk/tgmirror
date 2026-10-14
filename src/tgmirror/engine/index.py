"""The metadata index (docs/07-inspect-transform.md 3.4, phase 18b): read a source once, report on
it as often as you like.

``update`` brings the index of a source up to date through any ``MessageReader`` — the live gateway
behind ``FloodGuard`` (hard rule 1: a long read is paced like any other), so nothing here talks to
Telegram on its own. ``IndexReader`` is a ``MessageReader`` over the index, so ``engine/inspect.py``
scans it exactly as it scans a channel or a backup directory, and the filter is judged by the same
``Matcher``.

What an index cannot know: a message edited, deleted, or whose views/reactions moved after it was
read. ``update`` only adds what is newer than ``last_id``; ``refresh=True`` reads everything again
and drops what the source no longer has. Every output that comes from an index says when it was
last brought up to date.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tgmirror.core.gateway import (
    NO_FILTER,
    ChatKind,
    MediaCounters,
    MediaKind,
    MessageReader,
    Prepared,
    ServerFilter,
    SrcMessage,
    TopicInfo,
)
from tgmirror.store.index import MetadataIndex

BATCH = 200  # messages per transaction: a stop or a crash loses at most this many reads
_DATES = ("date", "edit_date")
_TUPLES = ("hashtags", "link_domains")


# ---- one message <-> the text kept in the index -----------------------------------------------


def encode(message: SrcMessage) -> str:
    data: dict[str, Any] = {}
    for f in fields(SrcMessage):
        value = getattr(message, f.name)
        if f.name in _DATES:
            value = value.isoformat() if value is not None else None
        elif f.name == "media":
            value = value.value
        elif f.name in _TUPLES:
            value = list(value)
        data[f.name] = value
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def decode(text: str) -> SrcMessage:
    data = json.loads(text)
    known = {f.name for f in fields(SrcMessage)}
    kept: dict[str, Any] = {k: v for k, v in data.items() if k in known}  # a field removed later
    for name in _DATES:
        if kept.get(name) is not None:
            kept[name] = datetime.fromisoformat(kept[name])
    kept["media"] = MediaKind(kept.get("media", MediaKind.TEXT.value))
    for name in _TUPLES:
        kept[name] = tuple(kept.get(name, ()))
    return SrcMessage(**kept)


# ---- what the index says about its source -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndexedSource:
    """What a report needs to know about the source without asking Telegram."""

    id: int
    title: str
    username: str | None
    kind: ChatKind
    noforwards: bool
    topics: dict[int, str]

    def to_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "username": self.username,
            "kind": self.kind.value,
            "noforwards": self.noforwards,
            "topics": {str(k): v for k, v in self.topics.items()},
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "IndexedSource":
        return cls(
            id=int(data["id"]),
            title=str(data.get("title") or ""),
            username=data.get("username"),
            kind=ChatKind(data.get("kind", ChatKind.BROADCAST.value)),
            noforwards=bool(data.get("noforwards", False)),
            topics={int(k): str(v) for k, v in (data.get("topics") or {}).items()},
        )


@dataclass(frozen=True, slots=True)
class IndexInfo:
    messages: int  # messages kept (service messages are not)
    last_id: int  # the highest id read
    updated_at: datetime | None
    complete: bool  # the last update reached the end of the source
    source: IndexedSource | None


async def info(index: MetadataIndex) -> IndexInfo:
    meta = await index.meta()
    updated = meta.get("updated_at")
    return IndexInfo(
        messages=await index.count(),
        last_id=int(meta.get("last_id", 0)),
        updated_at=datetime.fromisoformat(updated) if updated else None,
        complete=bool(meta.get("complete", False)),
        source=IndexedSource.from_data(meta["source"]) if meta.get("source") else None,
    )


def path_of(index_dir: Path, src_id: int) -> Path:
    return index_dir / f"{src_id}.db"


def _index_files(index_dir: Path) -> list[Path]:
    return sorted(index_dir.glob("*.db")) if index_dir.is_dir() else []


async def find(index_dir: Path, ref: str) -> tuple[MetadataIndex, IndexedSource] | None:
    """The index a chat reference names, without Telegram: ``@username``, a numeric id (with or
    without the ``-100`` prefix) or an exact title, among the indexes already built."""
    ref = ref.strip()
    files = await asyncio.to_thread(_index_files, index_dir)
    wanted: set[int] = set()
    if ref.lstrip("-").isdigit():
        number = int(ref)
        wanted = {number, -number, -1_000_000_000_000 - number} if number > 0 else {number}
    for path in files:
        opened = await MetadataIndex.open(path)
        meta = (await opened.meta()).get("source")
        if meta:
            source = IndexedSource.from_data(meta)
            word = ref[1:] if ref.startswith("@") else ref
            username = (source.username or "").casefold()
            if wanted:
                hit = source.id in wanted
            elif ref.startswith("@"):
                hit = username == word.casefold()
            else:  # a title, or a bare username (PowerShell swallows an unquoted @name)
                hit = source.title.casefold() == word.casefold() or username == word.casefold()
            if hit:
                return opened, source
        await opened.close()
    return None


# ---- bringing it up to date -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Updated:
    read: int  # messages read from the source this time (service ones included)
    complete: bool  # reached the end of the source (a stop or a FloodWait leaves it short)
    dropped: int  # messages a refresh found gone


async def update(
    index: MetadataIndex,
    reader: MessageReader,
    source: IndexedSource,
    *,
    refresh: bool = False,
    stop: Callable[[], bool] = lambda: False,
    on_progress: Callable[[int, int], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Updated:
    """Read what ``source`` has beyond the index's ``last_id`` (or everything, for ``refresh``).

    Whatever was read is kept even when this ends early — by ``stop`` or by an exception from
    the reader such as a FloodWait too long to sit out — because the index is a prefix and the
    next update goes on from where this one reached. ``on_progress(read, last_id)`` follows each
    message.
    """
    await index.set_meta(source=source.to_data(), complete=False)
    if refresh:
        await index.begin_refresh()
    start = 0 if refresh else await index.last_id()
    pending: list[tuple[int, str]] = []
    last = start
    read = 0

    async def flush() -> None:
        nonlocal pending
        if pending or last > start:
            await index.put(pending, last_id=last, refreshing=refresh)
        pending = []

    try:
        async with aclosing(reader.iter_messages(source.id, min_id=start)) as stream:
            async for message in stream:
                read += 1
                last = max(last, message.id)
                if not message.is_service:
                    pending.append((message.id, encode(message)))
                if len(pending) >= BATCH:
                    await flush()
                if on_progress is not None:
                    on_progress(read, last)
                if stop():
                    await flush()
                    return Updated(read, False, 0)
    except BaseException:
        await flush()
        raise
    await flush()
    dropped = await index.finish_refresh() if refresh else 0
    await index.set_meta(complete=True, updated_at=now().isoformat())
    return Updated(read, True, dropped)


# ---- reading from it --------------------------------------------------------------------------


class IndexReader:
    """A ``MessageReader`` over a ``MetadataIndex``: only the whole-source reads a report needs.

    The rest of the protocol (fetching file references, downloading) has no meaning for an index
    and raises, so a command that would write or download cannot be pointed at one by mistake.
    """

    def __init__(self, index: MetadataIndex, source: IndexedSource) -> None:
        self._index = index
        self._source = source

    async def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        if filters != NO_FILTER:
            raise ValueError("an index serves whole-source reads: a server filter has no meaning")
        async for data in self._index.rows(min_id):
            yield decode(data)

    async def count(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int:
        return await self._index.count()

    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]:
        return [decode(data) for data in await self._index.rows_of(ids)]

    async def list_topics(self, src: int) -> list[TopicInfo]:
        return [TopicInfo(id, title) for id, title in sorted(self._source.topics.items())]

    async def media_counters(self, *_: object, **__: object) -> MediaCounters:
        raise NotImplementedError("the counters come from Telegram, not from an index")

    async def fetch(self, *_: object, **__: object) -> Prepared:
        raise NotImplementedError("an index holds metadata, not files")

    async def prepare(self, *_: object, **__: object) -> Prepared:
        raise NotImplementedError("an index holds metadata, not files")

    async def export_unit(self, *_: object, **__: object) -> list[Any]:
        raise NotImplementedError("an index holds metadata, not files")
