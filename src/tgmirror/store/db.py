"""``Store``: the one SQLite file behind jobs, msg_map, flood_log and limiter_state
(docs/04-state-checkpoint.md).

The engine and the CLI use the intent-level methods below (``begin_batch``, ``commit_batch``,
``finish``, ``set_control``, ...), never SQL. Each write method is one ``BEGIN IMMEDIATE``
transaction, and none is ever held across a Telegram call (skill ``checkpoint-state``).

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

from tgmirror.core.errors import JobBusy, SchemaTooNew, StoreError
from tgmirror.core.gateway import Unit
from tgmirror.core.limiter import LimiterState
from tgmirror.store import floodlog, limiterstate, msgmap
from tgmirror.store.jobs import Control, Job, JobSpec, JobStatus, job_from_row
from tgmirror.store.msgmap import MessageResult, PendingRow

HEARTBEAT_TIMEOUT = timedelta(minutes=2)  # a runner silent for this long is presumed dead

Clock = Callable[[], datetime]


def _schema_v1() -> str:
    return resources.files("tgmirror.store").joinpath("schema.sql").read_text(encoding="utf-8")


def default_migrations() -> tuple[str, ...]:
    """SQL scripts by version: ``migrations[i]`` upgrades ``user_version`` ``i`` to ``i + 1``.

    Version 1 is ``schema.sql``. A schema change appends a script here (never edits an earlier
    one) and gets a test that upgrades a database made by the previous version.
    """
    return (_schema_v1(),)


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

    # ---- jobs -----------------------------------------------------------------------------

    async def create_job(self, spec: JobSpec) -> Job:
        ts = self._ts()
        async with self._tx() as db:
            cur = await db.execute(
                "INSERT INTO jobs(name, account, src_id, src_title, src_kind, dst_id, dst_title, "
                "mode, filters_json, options_json, status, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.name,
                    spec.account,
                    spec.src.id,
                    spec.src.title,
                    spec.src.kind,
                    spec.dst.id,
                    spec.dst.title,
                    spec.mode,
                    spec.filters_json,
                    spec.options.to_json(),
                    JobStatus.CREATED,
                    ts,
                    ts,
                ),
            )
            job_id = cur.lastrowid
        assert job_id is not None
        return await self._require(job_id)

    async def get_job(self, job_id: int) -> Job | None:
        async with self._lock:
            cur = await self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
        return job_from_row(row) if row is not None else None

    async def _require(self, job_id: int) -> Job:
        job = await self.get_job(job_id)
        if job is None:
            raise StoreError(f"job {job_id} does not exist")
        return job

    async def list_jobs(self) -> list[Job]:
        async with self._lock:
            cur = await self._conn.execute("SELECT * FROM jobs ORDER BY id")
            return [job_from_row(r) for r in await cur.fetchall()]

    async def find_jobs_by_name(self, name: str) -> list[Job]:
        async with self._lock:
            cur = await self._conn.execute("SELECT * FROM jobs WHERE name = ? ORDER BY id", (name,))
            return [job_from_row(r) for r in await cur.fetchall()]

    async def find_job_for_pair(self, src_id: int, dst_id: int) -> Job | None:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT * FROM jobs WHERE src_id = ? AND dst_id = ? ORDER BY id LIMIT 1",
                (src_id, dst_id),
            )
            row = await cur.fetchone()
        return job_from_row(row) if row is not None else None

    # ---- ownership, control, status -------------------------------------------------------

    async def claim(self, job_id: int, *, force: bool = False) -> Job:
        """Take the job for this process: ``running`` + heartbeat. Refuses a fresh foreign lock."""
        now = self._now()
        async with self._tx() as db:
            cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
            if row is None:
                raise StoreError(f"job {job_id} does not exist")
            job = job_from_row(row)
            if (
                job.status is JobStatus.RUNNING
                and not force
                and now - job.updated_at < HEARTBEAT_TIMEOUT
            ):
                raise JobBusy(job_id)
            await db.execute(
                "UPDATE jobs SET status = 'running', control = 'none', resume_at = NULL, "
                "fail_reason = NULL, updated_at = ? WHERE id = ?",
                (now.isoformat(), job_id),
            )
        return replace(
            job,
            status=JobStatus.RUNNING,
            control=Control.NONE,
            resume_at=None,
            fail_reason=None,
            updated_at=now,
        )

    async def heartbeat(self, job_id: int) -> None:
        async with self._tx() as db:
            await db.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (self._ts(), job_id))

    async def read_control(self, job_id: int) -> Control:
        async with self._lock:
            cur = await self._conn.execute("SELECT control FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
        return Control(row[0]) if row is not None else Control.NONE

    async def set_control(self, job_id: int, control: Control) -> bool:
        """Ask a running job to pause/stop; ``False`` when it is not running (nothing to ask)."""
        async with self._tx() as db:
            cur = await db.execute(
                "UPDATE jobs SET control = ? WHERE id = ? AND status = 'running'", (control, job_id)
            )
            return cur.rowcount > 0

    async def finish(
        self,
        job_id: int,
        status: JobStatus,
        *,
        fail_reason: str | None = None,
        resume_at: datetime | None = None,
    ) -> None:
        """Leave ``running``: set the final status, clear the control flag."""
        async with self._tx() as db:
            await db.execute(
                "UPDATE jobs SET status = ?, control = 'none', fail_reason = ?, resume_at = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    status,
                    fail_reason,
                    resume_at.isoformat() if resume_at else None,
                    self._ts(),
                    job_id,
                ),
            )

    # ---- batches --------------------------------------------------------------------------

    async def begin_batch(self, job_id: int, units: Sequence[Unit]) -> int:
        """Write-ahead: record the batch as ``pending`` before Telegram is called."""
        async with self._tx() as db:
            batch_id = await msgmap.next_batch_id(db, job_id)
            await msgmap.insert_pending(db, job_id, units, batch_id, self._ts())
        return batch_id

    async def commit_batch(
        self,
        job_id: int,
        batch_id: int,
        results: Sequence[MessageResult],
        cursor: int,
        *,
        extra_stats: Mapping[str, int] | None = None,
        limiter: LimiterState | None = None,
    ) -> Job:
        """One transaction: rows to ``done``/``failed``, cursor forward, stats, heartbeat, and the
        limiter's state (``sent_today`` moves with the messages it counts)."""
        ts = self._ts()
        async with self._tx() as db:
            done, failed = await msgmap.finish_batch(db, job_id, batch_id, results, ts)
            if await msgmap.pending_rows(db, job_id):
                # The cursor may only pass a batch with nothing pending (rule 3).
                raise StoreError("another batch is still pending; refusing to move the cursor")
            await self._bump(
                db, job_id, {"done": done, "failed": failed, **(extra_stats or {})}, cursor, ts
            )
            if limiter is not None:
                await limiterstate.save(db, await self._account(db, job_id), limiter, ts)
        return await self._require(job_id)

    async def discard_batch(self, job_id: int, batch_id: int) -> None:
        """Forget a batch Telegram provably refused (nothing was created)."""
        async with self._tx() as db:
            await msgmap.delete_pending(db, job_id, batch_id)

    async def pending_rows(self, job_id: int) -> list[PendingRow]:
        async with self._lock:
            return await msgmap.pending_rows(self._conn, job_id)

    async def discard_pending(self, job_id: int) -> None:
        """Reconcile found nothing in the destination: the batch will be sent again."""
        async with self._tx() as db:
            await msgmap.delete_pending(db, job_id)

    async def confirm_pending(self, job_id: int, mapping: dict[int, int]) -> None:
        """Reconcile found the copies: pending rows become ``done`` and the cursor passes them."""
        ts = self._ts()
        async with self._tx() as db:
            await msgmap.confirm_pending(db, job_id, mapping, ts)
            await self._bump(db, job_id, {"done": len(mapping)}, max(mapping), ts)

    async def advance_cursor(
        self, job_id: int, cursor: int, *, extra_stats: Mapping[str, int] | None = None
    ) -> Job:
        """Move the cursor past messages that need no work (already ``done``, or filtered out)."""
        async with self._tx() as db:
            await self._bump(db, job_id, extra_stats or {}, cursor, self._ts())
        return await self._require(job_id)

    async def replace_filters(self, job_id: int, filters_json: str) -> Job:
        """``run --refilter``: new filter, read the source again from the start.

        The one place the cursor moves backwards (rule 3 forbids it everywhere else): messages
        already ``done`` are skipped through ``msg_map`` when the source is scanned again, and the
        filter-skip counter starts over because that scan counts it again. Refused while another
        process is running the job.
        """
        now = self._now()
        async with self._tx() as db:
            cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
            if row is None:
                raise StoreError(f"job {job_id} does not exist")
            job = job_from_row(row)
            if job.status is JobStatus.RUNNING and now - job.updated_at < HEARTBEAT_TIMEOUT:
                raise JobBusy(job_id)
            stats = {k: v for k, v in job.stats.items() if k != "skipped_filter"}
            await db.execute(
                "UPDATE jobs SET filters_json = ?, cursor_src_id = 0, stats_json = ?, "
                "updated_at = ? WHERE id = ?",
                (filters_json, json.dumps(stats, sort_keys=True), now.isoformat(), job_id),
            )
        return await self._require(job_id)

    async def done_ids(self, job_id: int, ids: Sequence[int]) -> set[int]:
        async with self._lock:
            return await msgmap.done_ids(self._conn, job_id, ids)

    async def last_done_dst_id(self, job_id: int) -> int:
        async with self._lock:
            return await msgmap.last_done_dst_id(self._conn, job_id)

    async def log_flood(
        self,
        job_id: int,
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
                job_id=job_id,
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

    async def _account(self, db: aiosqlite.Connection, job_id: int) -> str:
        cur = await db.execute("SELECT account FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        if row is None:
            raise StoreError(f"job {job_id} does not exist")
        return str(row[0])

    async def _bump(
        self,
        db: aiosqlite.Connection,
        job_id: int,
        stats: Mapping[str, int],
        cursor: int,
        ts: str,
    ) -> None:
        cur = await db.execute("SELECT stats_json FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        assert row is not None
        merged: dict[str, int] = json.loads(row[0])
        for key, amount in stats.items():
            merged[key] = merged.get(key, 0) + amount
        # MAX(): the cursor never moves backwards (skill checkpoint-state, rule 3)
        await db.execute(
            "UPDATE jobs SET cursor_src_id = MAX(cursor_src_id, ?), stats_json = ?, updated_at = ? "
            "WHERE id = ?",
            (cursor, json.dumps(merged, sort_keys=True), ts, job_id),
        )
