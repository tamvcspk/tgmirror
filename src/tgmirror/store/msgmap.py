"""SQL for ``msg_map``. Every function takes an open transaction from ``Store`` (``store/db.py``).

Rows belong to a mirror (the source/destination pair), so a later run of the same pair sees what an
earlier one copied. ``run_id`` records the run that last settled the row, which is how ``history``
shows the failed messages of one run.

The states follow docs/04-state-checkpoint.md: ``pending`` is written *before* Telegram is called
(write-ahead), and turns ``done`` or ``failed`` in the same transaction that moves the cursor.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import aiosqlite

from tgmirror.core.errors import StoreError
from tgmirror.core.gateway import Unit
from tgmirror.store.runs import FailedMessage, MsgStatus


@dataclass(frozen=True, slots=True)
class MessageResult:
    """What happened to one source message in a batch: copied (``dst_id``), failed (``reason``), or
    left out on purpose (``skipped``, with its ``reason``; ``dst_id`` is the placeholder text that
    stands for it, if one was posted)."""

    src_id: int
    dst_id: int | None = None
    reason: str | None = None
    skipped: bool = False

    def __post_init__(self) -> None:
        if self.skipped:
            if self.reason is None:
                raise ValueError("a skipped message needs its reason")
        elif (self.dst_id is None) == (self.reason is None):
            raise ValueError("a result has either a dst_id or a failure reason")


@dataclass(frozen=True, slots=True)
class PendingRow:
    src_msg_id: int
    grouped_id: int | None
    batch_id: int


async def next_batch_id(db: aiosqlite.Connection, mirror_id: int) -> int:
    cur = await db.execute(
        "SELECT COALESCE(MAX(batch_id), 0) + 1 FROM msg_map WHERE mirror_id = ?", (mirror_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def insert_pending(
    db: aiosqlite.Connection,
    mirror_id: int,
    run_id: int,
    units: Sequence[Unit],
    batch_id: int,
    ts: str,
) -> None:
    """Write-ahead rows. A ``failed`` row from an earlier attempt is reused; ``done`` never is.

    A reused row keeps its ``reason`` and ``run_id`` while it is ``pending``: that is how
    ``delete_pending`` knows to put it back to ``failed`` instead of forgetting it (a retry sends
    messages that are below the cursor, so nothing else would ever read them again). ``run_id``
    moves to the current run when the batch is settled (``finish_batch``).
    """
    rows = [
        (mirror_id, m.id, m.grouped_id, m.topic_id, MsgStatus.PENDING, batch_id, run_id, ts)
        for unit in units
        for m in unit.messages
    ]
    await db.executemany(
        "INSERT INTO msg_map(mirror_id, src_msg_id, grouped_id, src_topic_id, status, "
        "  batch_id, run_id, ts) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(mirror_id, src_msg_id) DO UPDATE SET "
        "  dst_msg_id = NULL, grouped_id = excluded.grouped_id, "
        "  src_topic_id = excluded.src_topic_id, status = excluded.status, "
        "  reason = CASE WHEN msg_map.status = 'failed' THEN msg_map.reason END, "
        "  batch_id = excluded.batch_id, ts = excluded.ts "
        "WHERE msg_map.status != 'done'",
        rows,
    )


async def finish_batch(
    db: aiosqlite.Connection,
    mirror_id: int,
    run_id: int,
    batch_id: int,
    results: Iterable[MessageResult],
    ts: str,
) -> tuple[int, int, int]:
    """Turn a batch's pending rows into ``done``/``failed``/``skipped`` (now the rows of
    ``run_id``); returns the ``(done, failed, skipped)`` counts."""
    results = list(results)
    expected = {r.src_msg_id for r in await pending_rows(db, mirror_id, batch_id)}
    if {r.src_id for r in results} != expected or len(results) != len(expected):
        raise StoreError(f"results do not match the pending rows of batch {batch_id}")
    done = failed = skipped = 0
    for r in results:
        if r.skipped:
            status, skipped = MsgStatus.SKIPPED, skipped + 1
        elif r.dst_id is not None:
            status, done = MsgStatus.DONE, done + 1
        else:
            status, failed = MsgStatus.FAILED, failed + 1
        await db.execute(
            "UPDATE msg_map SET status = ?, dst_msg_id = ?, reason = ?, run_id = ?, ts = ? "
            "WHERE mirror_id = ? AND src_msg_id = ?",
            (status, r.dst_id, r.reason, run_id, ts, mirror_id, r.src_id),
        )
    return done, failed, skipped


async def pending_rows(
    db: aiosqlite.Connection, mirror_id: int, batch_id: int | None = None
) -> list[PendingRow]:
    sql = (
        "SELECT src_msg_id, grouped_id, batch_id FROM msg_map "
        "WHERE mirror_id = ? AND status = 'pending'"
    )
    params: tuple[int, ...] = (mirror_id,)
    if batch_id is not None:
        sql += " AND batch_id = ?"
        params = (mirror_id, batch_id)
    cur = await db.execute(sql + " ORDER BY src_msg_id", params)
    return [PendingRow(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def delete_pending(
    db: aiosqlite.Connection, mirror_id: int, batch_id: int | None = None
) -> None:
    """Forget pending rows (the batch is sent again, or was never sent).

    A row that had ``failed`` before (it still has its ``reason``, see ``insert_pending``) goes
    back to ``failed`` under its old run: it is not a new message that the cursor will bring back.
    """
    where = "mirror_id = ? AND status = 'pending'"
    params: tuple[int, ...] = (mirror_id,)
    if batch_id is not None:
        where += " AND batch_id = ?"
        params = (mirror_id, batch_id)
    await db.execute(
        f"UPDATE msg_map SET status = 'failed' WHERE {where} AND reason IS NOT NULL",  # noqa: S608
        params,
    )
    await db.execute(f"DELETE FROM msg_map WHERE {where}", params)  # noqa: S608


async def confirm_pending(
    db: aiosqlite.Connection, mirror_id: int, run_id: int, mapping: dict[int, int], ts: str
) -> None:
    """Reconcile: pending rows whose copy was found in the destination become ``done``."""
    expected = {r.src_msg_id for r in await pending_rows(db, mirror_id)}
    if set(mapping) != expected:
        raise StoreError("reconcile mapping does not cover exactly the pending rows")
    for src_id, dst_id in mapping.items():
        await db.execute(
            "UPDATE msg_map SET status = 'done', dst_msg_id = ?, reason = NULL, run_id = ?, "
            "ts = ? WHERE mirror_id = ? AND src_msg_id = ?",
            (dst_id, run_id, ts, mirror_id, src_id),
        )


async def done_ids(db: aiosqlite.Connection, mirror_id: int, ids: Sequence[int]) -> set[int]:
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    cur = await db.execute(
        f"SELECT src_msg_id FROM msg_map WHERE mirror_id = ? AND status = 'done' "  # noqa: S608
        f"AND src_msg_id IN ({marks})",
        (mirror_id, *ids),
    )
    return {r[0] for r in await cur.fetchall()}


async def last_done_dst_id(db: aiosqlite.Connection, mirror_id: int) -> int:
    cur = await db.execute(
        "SELECT COALESCE(MAX(dst_msg_id), 0) FROM msg_map WHERE mirror_id = ? AND status = 'done'",
        (mirror_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def failed_of_run(
    db: aiosqlite.Connection, run_id: int, limit: int | None = None
) -> list[FailedMessage]:
    """The messages a run left ``failed`` (rows a later run picked up again are not listed)."""
    sql = (
        "SELECT src_msg_id, COALESCE(reason, '') FROM msg_map "
        "WHERE run_id = ? AND status = 'failed' ORDER BY src_msg_id"
    )
    params: tuple[int, ...] = (run_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (run_id, limit)
    cur = await db.execute(sql, params)
    return [FailedMessage(r[0], r[1]) for r in await cur.fetchall()]


async def count_failed_of_run(db: aiosqlite.Connection, run_id: int) -> int:
    cur = await db.execute(
        "SELECT COUNT(*) FROM msg_map WHERE run_id = ? AND status = 'failed'", (run_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


GONE = "gone_from_source"  # ``reason`` of a failed message a retry found deleted at the source


async def mark_gone(
    db: aiosqlite.Connection, mirror_id: int, run_id: int, ids: Sequence[int], ts: str
) -> int:
    """``failed`` rows whose source message no longer exists become ``skipped`` (nothing left to
    retry); returns how many. ``run_id`` is the retry that noticed."""
    marks = ",".join("?" * len(ids))
    cur = await db.execute(
        "UPDATE msg_map SET status = 'skipped', reason = ?, run_id = ?, ts = ? "  # noqa: S608
        f"WHERE mirror_id = ? AND status = 'failed' AND src_msg_id IN ({marks})",
        (GONE, run_id, ts, mirror_id, *ids),
    )
    return cur.rowcount


async def count_done(db: aiosqlite.Connection, mirror_id: int) -> int:
    cur = await db.execute(
        "SELECT COUNT(*) FROM msg_map WHERE mirror_id = ? AND status = 'done'", (mirror_id,)
    )
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def delete_all(db: aiosqlite.Connection, mirror_id: int) -> None:
    """Forget every row of the pair (a fresh start): done, failed, skipped and pending alike."""
    await db.execute("DELETE FROM msg_map WHERE mirror_id = ?", (mirror_id,))
