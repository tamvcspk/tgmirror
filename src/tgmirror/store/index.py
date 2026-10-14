"""The local metadata index of one source (docs/07-inspect-transform.md 3.4, phase 18b).

One SQLite file per source, ``<data_dir>/index/<src id>.db``, next to nothing else: it is big,
it can be rebuilt from Telegram at any time, and it must never travel with ``tgmirror.db`` (an
``appdata export``) or take its locks. A row per message holds the message as the engine sees it
(``engine/index.py`` turns a ``SrcMessage`` into the JSON text kept here — this module knows SQL
and nothing about messages), so any filter runs on the index through the same ``Matcher``.

The index is a *prefix* of the source in id order: a read that stops early leaves a shorter,
still valid index, and the next update goes on after ``last_id``. It is never the basis for a
write to Telegram.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from typing import Any, Self

import aiosqlite

INDEX_VERSION = 1  # ``PRAGMA user_version``; another value means "rebuild", not "migrate"
_PAGE = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seen (id INTEGER PRIMARY KEY);
"""


class MetadataIndex:
    """The messages of one source and a few facts about the index itself (``meta``)."""

    def __init__(self, conn: aiosqlite.Connection, path: Path) -> None:
        self._conn = conn
        self.path = path

    @classmethod
    async def open(cls, path: Path) -> Self:
        """Open (or create) the index at ``path``. A file made by another format version, or one
        that is not a database any more, is thrown away and started again: an index is a cache."""
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            conn = await aiosqlite.connect(path)
            try:
                async with conn.execute("PRAGMA user_version") as cursor:
                    row = await cursor.fetchone()
                version = row[0] if row else 0
                if version not in (0, INDEX_VERSION):
                    raise aiosqlite.DatabaseError("another index format")
                await conn.executescript(_SCHEMA)
                await conn.execute(f"PRAGMA user_version = {INDEX_VERSION}")
                await conn.commit()
                return cls(conn, path)
            except aiosqlite.DatabaseError:
                await conn.close()
                if attempt == 2:
                    raise
                await asyncio.to_thread(path.unlink, missing_ok=True)
        raise AssertionError("unreachable")  # pragma: no cover

    async def close(self) -> None:
        await self._conn.close()

    # ---- facts about the index ---------------------------------------------------------------

    async def meta(self) -> dict[str, Any]:
        async with self._conn.execute("SELECT key, value FROM meta") as cursor:
            return {key: json.loads(value) async for key, value in cursor}

    async def set_meta(self, **values: Any) -> None:
        await self._conn.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()],
        )
        await self._conn.commit()

    async def count(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) FROM messages") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def last_id(self) -> int:
        """The highest message id read so far (service messages included, they are not kept)."""
        return int((await self.meta()).get("last_id", 0))

    # ---- messages ----------------------------------------------------------------------------

    async def put(
        self, rows: Sequence[tuple[int, str]], *, last_id: int, refreshing: bool = False
    ) -> None:
        """Keep ``rows`` (id, JSON) and move ``last_id`` up to ``last_id``: one transaction, so
        the index never says it has read further than what it holds. While ``refreshing`` the ids
        are also noted as seen (``finish_refresh`` drops the rest)."""
        await self._conn.execute("BEGIN IMMEDIATE")
        try:
            await self._conn.executemany(
                "INSERT OR REPLACE INTO messages(id, data) VALUES (?, ?)", list(rows)
            )
            if refreshing:
                await self._conn.executemany(
                    "INSERT OR IGNORE INTO seen(id) VALUES (?)", [(i,) for i, _ in rows]
                )
            await self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('last_id', ?)",
                (json.dumps(max(last_id, await self.last_id())),),
            )
            await self._conn.commit()
        except BaseException:
            await self._conn.rollback()
            raise

    async def rows(self, min_id: int = 0) -> AsyncIterator[str]:
        """The JSON of every message with ``id > min_id``, ascending, a page at a time."""
        cursor_id = min_id
        while True:
            async with self._conn.execute(
                "SELECT id, data FROM messages WHERE id > ? ORDER BY id LIMIT ?",
                (cursor_id, _PAGE),
            ) as cursor:
                page = await cursor.fetchall()
            if not page:
                return
            for _, data in page:
                yield data
            cursor_id = page[-1][0]

    async def rows_of(self, ids: Iterable[int]) -> list[str]:
        wanted = sorted(set(ids))
        found: list[str] = []
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            marks = ",".join("?" * len(chunk))
            async with self._conn.execute(
                f"SELECT data FROM messages WHERE id IN ({marks}) ORDER BY id", chunk
            ) as cursor:
                found.extend([row[0] async for row in cursor])
        return found

    # ---- a full re-read ----------------------------------------------------------------------

    async def begin_refresh(self) -> None:
        """A full re-read starts: every message met from now on is noted as seen."""
        await self._conn.execute("DELETE FROM seen")
        await self._conn.commit()

    async def finish_refresh(self) -> int:
        """The re-read reached the end: what it did not meet is gone from the source. Returns
        how many messages that was."""
        async with self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE id NOT IN (SELECT id FROM seen)"
        ) as cursor:
            row = await cursor.fetchone()
        gone = int(row[0]) if row else 0
        await self._conn.execute("DELETE FROM messages WHERE id NOT IN (SELECT id FROM seen)")
        await self._conn.execute("DELETE FROM seen")
        await self._conn.commit()
        return gone
