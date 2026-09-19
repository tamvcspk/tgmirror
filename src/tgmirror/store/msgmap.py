"""SQL for ``msg_map``. Every function takes an open transaction from ``Store`` (``store/db.py``).

The states follow docs/04-state-checkpoint.md: ``pending`` is written *before* Telegram is called
(write-ahead), and turns ``done`` or ``failed`` in the same transaction that moves the cursor.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import aiosqlite

from tgmirror.core.errors import StoreError
from tgmirror.core.gateway import Unit
from tgmirror.store.jobs import MsgStatus


@dataclass(frozen=True, slots=True)
class MessageResult:
    """What happened to one source message in a batch: copied (``dst_id``) or not (``reason``)."""

    src_id: int
    dst_id: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if (self.dst_id is None) == (self.reason is None):
            raise ValueError("a result has either a dst_id or a failure reason")


@dataclass(frozen=True, slots=True)
class PendingRow:
    src_msg_id: int
    grouped_id: int | None
    batch_id: int


async def next_batch_id(db: aiosqlite.Connection, job_id: int) -> int:
    cur = await db.execute(
        "SELECT COALESCE(MAX(batch_id), 0) + 1 FROM msg_map WHERE job_id = ?", (job_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def insert_pending(
    db: aiosqlite.Connection, job_id: int, units: Sequence[Unit], batch_id: int, ts: str
) -> None:
    """Write-ahead rows. A ``failed`` row from an earlier attempt is reused; ``done`` never is."""
    rows = [
        (job_id, m.id, m.grouped_id, MsgStatus.PENDING, batch_id, ts)
        for unit in units
        for m in unit.messages
    ]
    await db.executemany(
        "INSERT INTO msg_map(job_id, src_msg_id, grouped_id, status, batch_id, ts) "
        "VALUES(?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(job_id, src_msg_id) DO UPDATE SET "
        "  dst_msg_id = NULL, grouped_id = excluded.grouped_id, status = excluded.status, "
        "  reason = NULL, batch_id = excluded.batch_id, ts = excluded.ts "
        "WHERE msg_map.status != 'done'",
        rows,
    )


async def finish_batch(
    db: aiosqlite.Connection,
    job_id: int,
    batch_id: int,
    results: Iterable[MessageResult],
    ts: str,
) -> tuple[int, int]:
    """Turn a batch's pending rows into ``done``/``failed``; returns ``(done, failed)`` counts."""
    results = list(results)
    expected = {r.src_msg_id for r in await pending_rows(db, job_id, batch_id)}
    if {r.src_id for r in results} != expected or len(results) != len(expected):
        raise StoreError(f"results do not match the pending rows of batch {batch_id}")
    done = failed = 0
    for r in results:
        if r.dst_id is not None:
            status, done = MsgStatus.DONE, done + 1
        else:
            status, failed = MsgStatus.FAILED, failed + 1
        await db.execute(
            "UPDATE msg_map SET status = ?, dst_msg_id = ?, reason = ?, ts = ? "
            "WHERE job_id = ? AND src_msg_id = ?",
            (status, r.dst_id, r.reason, ts, job_id, r.src_id),
        )
    return done, failed


async def pending_rows(
    db: aiosqlite.Connection, job_id: int, batch_id: int | None = None
) -> list[PendingRow]:
    sql = (
        "SELECT src_msg_id, grouped_id, batch_id FROM msg_map "
        "WHERE job_id = ? AND status = 'pending'"
    )
    params: tuple[int, ...] = (job_id,)
    if batch_id is not None:
        sql += " AND batch_id = ?"
        params = (job_id, batch_id)
    cur = await db.execute(sql + " ORDER BY src_msg_id", params)
    return [PendingRow(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def delete_pending(
    db: aiosqlite.Connection, job_id: int, batch_id: int | None = None
) -> None:
    sql = "DELETE FROM msg_map WHERE job_id = ? AND status = 'pending'"
    params: tuple[int, ...] = (job_id,)
    if batch_id is not None:
        sql += " AND batch_id = ?"
        params = (job_id, batch_id)
    await db.execute(sql, params)


async def confirm_pending(
    db: aiosqlite.Connection, job_id: int, mapping: dict[int, int], ts: str
) -> None:
    """Reconcile: pending rows whose copy was found in the destination become ``done``."""
    expected = {r.src_msg_id for r in await pending_rows(db, job_id)}
    if set(mapping) != expected:
        raise StoreError("reconcile mapping does not cover exactly the pending rows")
    for src_id, dst_id in mapping.items():
        await db.execute(
            "UPDATE msg_map SET status = 'done', dst_msg_id = ?, reason = NULL, ts = ? "
            "WHERE job_id = ? AND src_msg_id = ?",
            (dst_id, ts, job_id, src_id),
        )


async def done_ids(db: aiosqlite.Connection, job_id: int, ids: Sequence[int]) -> set[int]:
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    cur = await db.execute(
        f"SELECT src_msg_id FROM msg_map WHERE job_id = ? AND status = 'done' "  # noqa: S608
        f"AND src_msg_id IN ({marks})",
        (job_id, *ids),
    )
    return {r[0] for r in await cur.fetchall()}


async def last_done_dst_id(db: aiosqlite.Connection, job_id: int) -> int:
    cur = await db.execute(
        "SELECT COALESCE(MAX(dst_msg_id), 0) FROM msg_map WHERE job_id = ? AND status = 'done'",
        (job_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])
