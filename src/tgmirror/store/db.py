"""``Store``: the one SQLite file behind runs, mirrors, msg_map, flood_log and limiter_state
(docs/04-state-checkpoint.md).

The engine and the CLI use the intent-level methods below (``start_run``, ``begin_batch``,
``commit_batch``, ``finish``, ``set_control``, ...), never SQL. Each write method is one
``BEGIN IMMEDIATE`` transaction, and none is ever held across a Telegram call (skill
``checkpoint-state``).

A *run* is one execution (the log); a *mirror* is the checkpoint of a source/destination pair. The
batch methods take the id of the run doing the work and find its mirror themselves.

One connection is shared by the tasks of a process (the runner and its heartbeat), so every
method takes ``_lock``: without it a second task could run statements inside the first one's
open transaction and commit it half-way.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Self

import aiosqlite

from tgmirror.core.errors import RunBusy, SchemaTooNew, StoreError
from tgmirror.core.gateway import Unit
from tgmirror.core.limiter import LimiterState
from tgmirror.store import floodlog, limiterstate, msgmap
from tgmirror.store.msgmap import MessageResult, PendingRow
from tgmirror.store.runs import (
    RUN_SELECT,
    Control,
    FailedMessage,
    FilterChange,
    FloodEvent,
    Mirror,
    Run,
    RunOptions,
    RunSpec,
    RunStatus,
    StartedRun,
    mirror_from_row,
    run_from_row,
)

HEARTBEAT_TIMEOUT = timedelta(minutes=2)  # a runner silent for this long is presumed dead
_LIVE = (RunStatus.RUNNING, RunStatus.PAUSED)  # states of a run that holds its clone

Clock = Callable[[], datetime]


def _sql(name: str) -> str:
    return resources.files("tgmirror.store").joinpath(name).read_text(encoding="utf-8")


def default_migrations() -> tuple[str, ...]:
    """SQL scripts by version: ``migrations[i]`` upgrades ``user_version`` ``i`` to ``i + 1``.

    Version 1 is ``schema.sql``. A schema change appends a script here (never edits an earlier
    one) and gets a test that upgrades a database made by the previous version.
    """
    return (_sql("schema.sql"),)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, conn: aiosqlite.Connection, clock: Clock = utc_now) -> None:
        self._conn = conn
        self._clock = clock
        self._lock = asyncio.Lock()

    # ---- lifecycle ------------------------------------------------------------------------

    @classmethod
    async def open(
        cls, path: Path, *, clock: Clock = utc_now, migrations: Sequence[str] | None = None
    ) -> Self:
        path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: autocommit, so the transactions below are exactly our own.
        conn = await aiosqlite.connect(path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA busy_timeout = 5000")  # a `pause` from another process waits
        store = cls(conn, clock)
        try:
            await store._migrate(
                tuple(migrations) if migrations is not None else default_migrations()
            )
        except BaseException:
            await conn.close()
            raise
        return store

    async def close(self) -> None:
        await self._conn.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _migrate(self, migrations: tuple[str, ...]) -> None:
        async with self._lock:
            cur = await self._conn.execute("PRAGMA user_version")
            row = await cur.fetchone()
            assert row is not None
            version = int(row[0])
            if version > len(migrations):
                raise SchemaTooNew(
                    f"database schema is version {version}, this tgmirror knows {len(migrations)}"
                )
            for target in range(version + 1, len(migrations) + 1):
                script = (
                    f"BEGIN IMMEDIATE;\n{migrations[target - 1]}\n"
                    f"PRAGMA user_version = {target};\nCOMMIT;"
                )
                try:
                    await self._conn.executescript(script)
                except BaseException:
                    if self._conn.in_transaction:
                        await self._conn.execute("ROLLBACK")
                    raise

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                await self._conn.execute("ROLLBACK")
                raise
            await self._conn.execute("COMMIT")

    def _now(self) -> datetime:
        return self._clock()

    def _ts(self) -> str:
        return self._now().isoformat()

    # ---- runs and mirrors -----------------------------------------------------------------

    async def find_mirror(self, src_id: int, dst_id: int) -> Mirror | None:
        async with self._lock:
            return await self._mirror_of_pair(self._conn, src_id, dst_id)

    async def count_copied(self, src_id: int, dst_id: int) -> int:
        """Messages the pair has copied so far (0 for a pair never cloned)."""
        async with self._lock:
            mirror = await self._mirror_of_pair(self._conn, src_id, dst_id)
            return 0 if mirror is None else await msgmap.count_done(self._conn, mirror.id)

    async def start_run(
        self, spec: RunSpec, *, force: bool = False, fresh: bool = False
    ) -> StartedRun:
        """Begin a run of ``spec``'s pair: get or create its mirror, settle the filter, insert.

        One transaction. Refuses (``RunBusy``) when another process holds the pair with a fresh
        heartbeat, unless ``force``; a run whose heartbeat is stale is closed as ``failed`` with
        the reason ``interrupted`` (it died), so the log stays truthful. A filter that differs from
        the remembered one restarts the read from the start: this is the one place the cursor moves
        backwards (rule 3 forbids it everywhere else). Messages already ``done`` are skipped
        through ``msg_map`` when the source is read again.

        ``fresh`` is the other place the cursor goes back: the pair forgets its progress
        (every ``msg_map`` row, the cursor) and records the destination's current newest
        message as the new ``dst_base_id``, so the whole source is copied again. It is
        checked after the busy test, so a pair someone else runs loses nothing.
        """
        now = self._now()
        ts = now.isoformat()
        async with self._tx() as db:
            mirror = await self._mirror_of_pair(db, spec.src.id, spec.dst.id)
            forgot: int | None = None
            if mirror is None:
                filters = spec.filters_json if spec.filters_json is not None else "{}"
                cur = await db.execute(
                    "INSERT INTO mirrors(account, src_id, src_title, src_kind, dst_id, dst_title, "
                    "mode, filters_json, options_json, cursor_src_id, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (
                        spec.account,
                        spec.src.id,
                        spec.src.title,
                        spec.src.kind,
                        spec.dst.id,
                        spec.dst.title,
                        spec.mode,
                        filters,
                        spec.options.for_pair(spec.options.dst_base_id).to_json(),
                        ts,
                        ts,
                    ),
                )
                assert cur.lastrowid is not None
                mirror_id, cursor, base = cur.lastrowid, 0, spec.options.dst_base_id
                change = FilterChange.NEW
            else:
                await self._close_dead_runs(db, mirror.id, now, force)
                mirror_id, base = mirror.id, mirror.options.dst_base_id
                filters, cursor, change = (
                    mirror.filters_json,
                    mirror.cursor_src_id,
                    FilterChange.SAME,
                )
                if fresh:
                    forgot = await msgmap.count_done(db, mirror_id)
                    await msgmap.delete_all(db, mirror_id)
                    base, cursor = spec.options.dst_base_id, 0
                    await db.execute(
                        "UPDATE mirrors SET options_json = ?, cursor_src_id = 0, updated_at = ? "
                        "WHERE id = ?",
                        (spec.options.for_pair(base).to_json(), ts, mirror_id),
                    )
                if spec.filters_json is not None and spec.filters_json != mirror.filters_json:
                    filters, cursor, change = spec.filters_json, 0, FilterChange.CHANGED
                    await db.execute(
                        "UPDATE mirrors SET filters_json = ?, cursor_src_id = 0, updated_at = ? "
                        "WHERE id = ?",
                        (filters, ts, mirror_id),
                    )
            options = replace(spec.options, dst_base_id=base)
            cur = await db.execute(
                "INSERT INTO runs(mirror_id, mode, filters_json, options_json, status, control, "
                "cursor_from, cursor_to, started_at, updated_at) "
                "VALUES(?, ?, ?, ?, 'running', 'none', ?, ?, ?, ?)",
                (mirror_id, spec.mode, filters, options.to_json(), cursor, cursor, ts, ts),
            )
            run_id = cur.lastrowid
        assert run_id is not None
        return StartedRun(await self._require(run_id), change, forgot)

    async def _close_dead_runs(
        self, db: aiosqlite.Connection, mirror_id: int, now: datetime, force: bool
    ) -> None:
        cur = await db.execute(
            "SELECT id, updated_at FROM runs "
            "WHERE mirror_id = ? AND status IN ('running', 'paused')",
            (mirror_id,),
        )
        for row in await cur.fetchall():
            fresh = now - datetime.fromisoformat(row["updated_at"]) < HEARTBEAT_TIMEOUT
            if fresh and not force:
                raise RunBusy(row["id"])
            # a dead run ended when it last gave a sign of life; a taken-over one ends now
            reason, ended = ("taken_over", now.isoformat()) if fresh else ("interrupted", row[1])
            await db.execute(
                "UPDATE runs SET status = 'failed', control = 'none', fail_reason = ?, "
                "ended_at = ? WHERE id = ?",
                (reason, ended, row["id"]),
            )

    async def get_run(self, run_id: int) -> Run | None:
        async with self._lock:
            cur = await self._conn.execute(RUN_SELECT + " WHERE r.id = ?", (run_id,))
            row = await cur.fetchone()
        return run_from_row(row) if row is not None else None

    async def _require(self, run_id: int) -> Run:
        run = await self.get_run(run_id)
        if run is None:
            raise StoreError(f"run {run_id} does not exist")
        return run

    async def latest_run(self, src_id: int | None = None, dst_id: int | None = None) -> Run | None:
        """The newest run, of one pair when both ids are given."""
        sql = RUN_SELECT
        params: tuple[int, ...] = ()
        if src_id is not None and dst_id is not None:
            sql += " WHERE m.src_id = ? AND m.dst_id = ?"
            params = (src_id, dst_id)
        async with self._lock:
            cur = await self._conn.execute(sql + " ORDER BY r.id DESC LIMIT 1", params)
            row = await cur.fetchone()
        return run_from_row(row) if row is not None else None

    async def list_runs(self, limit: int = 20) -> list[Run]:
        """The newest runs first."""
        async with self._lock:
            cur = await self._conn.execute(RUN_SELECT + " ORDER BY r.id DESC LIMIT ?", (limit,))
            return [run_from_row(r) for r in await cur.fetchall()]

    async def list_pairs(self, limit: int = 10) -> list[Run]:
        """The latest run of each distinct pair (mirror), most recently active first.

        For ``run``'s wizard step: which pair to continue when none was named and more than one
        exists.
        """
        async with self._lock:
            cur = await self._conn.execute(
                RUN_SELECT + " WHERE r.id IN (SELECT MAX(id) FROM runs GROUP BY mirror_id) "
                "ORDER BY r.id DESC LIMIT ?",
                (limit,),
            )
            return [run_from_row(r) for r in await cur.fetchall()]

    async def active_run(self) -> Run | None:
        """The run some process holds right now (``running``/``paused`` with a fresh heartbeat)."""
        now = self._now()
        async with self._lock:
            cur = await self._conn.execute(
                RUN_SELECT + " WHERE r.status IN ('running', 'paused') ORDER BY r.id DESC"
            )
            rows = await cur.fetchall()
        for row in rows:
            run = run_from_row(row)
            if now - run.updated_at < HEARTBEAT_TIMEOUT:
                return run
        return None

    async def run_failures(self, run_id: int, limit: int | None = None) -> list[FailedMessage]:
        async with self._lock:
            return await msgmap.failed_of_run(self._conn, run_id, limit)

    async def count_failed(self, run_id: int) -> int:
        """Messages that are ``failed`` and were last written by this run: what ``retry`` sends."""
        async with self._lock:
            return await msgmap.count_failed_of_run(self._conn, run_id)

    async def flood_events(self, run_id: int) -> list[FloodEvent]:
        async with self._lock:
            return await floodlog.events_of_run(self._conn, run_id)

    async def flood_count_since(self, since: datetime) -> int:
        async with self._lock:
            return await floodlog.count_since(self._conn, since)

    # ---- ownership, control, status -------------------------------------------------------

    async def heartbeat(self, run_id: int) -> None:
        async with self._tx() as db:
            await db.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (self._ts(), run_id))

    async def read_control(self, run_id: int) -> Control:
        async with self._lock:
            cur = await self._conn.execute("SELECT control FROM runs WHERE id = ?", (run_id,))
            row = await cur.fetchone()
        return Control(row[0]) if row is not None else Control.NONE

    async def set_control(self, run_id: int, control: Control) -> bool:
        """Ask a live run to pause, stop or carry on; ``False`` when it is not live."""
        async with self._tx() as db:
            cur = await db.execute(
                "UPDATE runs SET control = ? WHERE id = ? AND status IN ('running', 'paused')",
                (control, run_id),
            )
            return cur.rowcount > 0

    async def set_total(self, run_id: int, total: int) -> Run:
        """Record how many messages the run has to look at (its analysis, see ``RunOptions``)."""
        async with self._tx() as db:
            cur = await db.execute("SELECT options_json FROM runs WHERE id = ?", (run_id,))
            row = await cur.fetchone()
            if row is None:
                raise StoreError(f"run {run_id} does not exist")
            options = replace(RunOptions.from_json(row[0]), total_items=total)
            await db.execute(
                "UPDATE runs SET options_json = ?, updated_at = ? WHERE id = ?",
                (options.to_json(), self._ts(), run_id),
            )
        return await self._require(run_id)

    async def set_status(self, run_id: int, status: RunStatus) -> None:
        """``running`` <-> ``paused`` while the process lives; a finished run is left alone."""
        assert status in _LIVE
        async with self._tx() as db:
            await db.execute(
                "UPDATE runs SET status = ?, updated_at = ? "
                "WHERE id = ? AND status IN ('running', 'paused')",
                (status, self._ts(), run_id),
            )

    async def finish(
        self,
        run_id: int,
        status: RunStatus,
        *,
        fail_reason: str | None = None,
        resume_at: datetime | None = None,
    ) -> None:
        """Leave the live states: set the final status, clear the control flag, close the log."""
        ts = self._ts()
        async with self._tx() as db:
            await db.execute(
                "UPDATE runs SET status = ?, control = 'none', fail_reason = ?, resume_at = ?, "
                "ended_at = ?, updated_at = ? WHERE id = ?",
                (status, fail_reason, resume_at.isoformat() if resume_at else None, ts, ts, run_id),
            )

    # ---- batches --------------------------------------------------------------------------

    async def begin_batch(self, run_id: int, units: Sequence[Unit]) -> int:
        """Write-ahead: record the batch as ``pending`` before Telegram is called."""
        async with self._tx() as db:
            mirror_id = await self._mirror_id(db, run_id)
            batch_id = await msgmap.next_batch_id(db, mirror_id)
            await msgmap.insert_pending(db, mirror_id, run_id, units, batch_id, self._ts())
        return batch_id

    async def commit_batch(
        self,
        run_id: int,
        batch_id: int,
        results: Sequence[MessageResult],
        cursor: int,
        *,
        extra_stats: Mapping[str, int] | None = None,
        limiter: LimiterState | None = None,
    ) -> Run:
        """One transaction: rows to ``done``/``failed``, cursor forward, stats, heartbeat, and the
        limiter's state (``sent_today`` moves with the messages it counts)."""
        ts = self._ts()
        async with self._tx() as db:
            mirror_id = await self._mirror_id(db, run_id)
            done, failed, skipped = await msgmap.finish_batch(
                db, mirror_id, run_id, batch_id, results, ts
            )
            if await msgmap.pending_rows(db, mirror_id):
                # The cursor may only pass a batch with nothing pending (rule 3).
                raise StoreError("another batch is still pending; refusing to move the cursor")
            await self._bump(
                db,
                run_id,
                mirror_id,
                {"done": done, "failed": failed}
                | ({"skipped_unsupported": skipped} if skipped else {})
                | dict(extra_stats or {}),
                cursor,
                ts,
            )
            if limiter is not None:
                await limiterstate.save(db, await self._account(db, mirror_id), limiter, ts)
        return await self._require(run_id)

    async def discard_batch(self, run_id: int, batch_id: int) -> None:
        """Forget a batch Telegram provably refused (nothing was created)."""
        async with self._tx() as db:
            await msgmap.delete_pending(db, await self._mirror_id(db, run_id), batch_id)

    async def pending_rows(self, run_id: int) -> list[PendingRow]:
        async with self._lock:
            return await msgmap.pending_rows(self._conn, await self._mirror_id(self._conn, run_id))

    async def discard_pending(self, run_id: int) -> None:
        """Reconcile found nothing in the destination: the batch will be sent again."""
        async with self._tx() as db:
            await msgmap.delete_pending(db, await self._mirror_id(db, run_id))

    async def confirm_pending(self, run_id: int, mapping: dict[int, int]) -> None:
        """Reconcile found the copies: pending rows become ``done`` and the cursor passes them."""
        ts = self._ts()
        async with self._tx() as db:
            mirror_id = await self._mirror_id(db, run_id)
            await msgmap.confirm_pending(db, mirror_id, run_id, mapping, ts)
            await self._bump(db, run_id, mirror_id, {"done": len(mapping)}, max(mapping), ts)

    async def advance_cursor(
        self, run_id: int, cursor: int, *, extra_stats: Mapping[str, int] | None = None
    ) -> Run:
        """Move the cursor past messages that need no work (already ``done``, or filtered out)."""
        async with self._tx() as db:
            mirror_id = await self._mirror_id(db, run_id)
            await self._bump(db, run_id, mirror_id, extra_stats or {}, cursor, self._ts())
        return await self._require(run_id)

    async def mark_gone(self, run_id: int, ids: Sequence[int]) -> None:
        """A retry found these ``failed`` messages deleted at the source: they become ``skipped``
        (with a reason), so they stop showing as failures nobody can fix; counted in ``gone``."""
        async with self._tx() as db:
            mirror_id = await self._mirror_id(db, run_id)
            ts = self._ts()
            count = await msgmap.mark_gone(db, mirror_id, run_id, ids, ts)
            if count:
                await self._bump(db, run_id, mirror_id, {"gone": count}, 0, ts)

    async def done_ids(self, run_id: int, ids: Sequence[int]) -> set[int]:
        async with self._lock:
            mirror_id = await self._mirror_id(self._conn, run_id)
            return await msgmap.done_ids(self._conn, mirror_id, ids)

    async def last_done_dst_id(self, run_id: int) -> int:
        async with self._lock:
            return await msgmap.last_done_dst_id(
                self._conn, await self._mirror_id(self._conn, run_id)
            )

    async def log_flood(
        self,
        run_id: int,
        *,
        kind: str,
        seconds: int | None,
        method: str,
        delay_ms: int | None,
        batch_size: int | None,
    ) -> None:
        async with self._tx() as db:
            await floodlog.log_flood(
                db,
                run_id=run_id,
                ts=self._ts(),
                kind=kind,
                seconds=seconds,
                method=method,
                delay_ms=delay_ms,
                batch_size=batch_size,
            )

    async def load_limiter_state(self, account: str) -> LimiterState | None:
        async with self._lock:
            return await limiterstate.load(self._conn, account)

    async def save_limiter_state(self, account: str, state: LimiterState) -> None:
        """Keep what the limiter learned (after a flood); a batch commit saves it with the batch."""
        async with self._tx() as db:
            await limiterstate.save(db, account, state, self._ts())

    # ---- internals ------------------------------------------------------------------------

    async def _mirror_of_pair(
        self, db: aiosqlite.Connection, src_id: int, dst_id: int
    ) -> Mirror | None:
        cur = await db.execute(
            "SELECT * FROM mirrors WHERE src_id = ? AND dst_id = ?", (src_id, dst_id)
        )
        row = await cur.fetchone()
        return mirror_from_row(row) if row is not None else None

    async def _mirror_id(self, db: aiosqlite.Connection, run_id: int) -> int:
        cur = await db.execute("SELECT mirror_id FROM runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        if row is None:
            raise StoreError(f"run {run_id} does not exist")
        return int(row[0])

    async def _account(self, db: aiosqlite.Connection, mirror_id: int) -> str:
        cur = await db.execute("SELECT account FROM mirrors WHERE id = ?", (mirror_id,))
        row = await cur.fetchone()
        assert row is not None
        return str(row[0])

    async def _bump(
        self,
        db: aiosqlite.Connection,
        run_id: int,
        mirror_id: int,
        stats: Mapping[str, int],
        cursor: int,
        ts: str,
    ) -> None:
        cur = await db.execute("SELECT stats_json FROM runs WHERE id = ?", (run_id,))
        row = await cur.fetchone()
        assert row is not None
        merged: dict[str, int] = json.loads(row[0])
        for key, amount in stats.items():
            merged[key] = merged.get(key, 0) + amount
        # MAX(): the cursor never moves backwards (skill checkpoint-state, rule 3)
        await db.execute(
            "UPDATE runs SET cursor_to = MAX(cursor_to, ?), stats_json = ?, updated_at = ? "
            "WHERE id = ?",
            (cursor, json.dumps(merged, sort_keys=True), ts, run_id),
        )
        await db.execute(
            "UPDATE mirrors SET cursor_src_id = MAX(cursor_src_id, ?), updated_at = ? WHERE id = ?",
            (cursor, ts, mirror_id),
        )
