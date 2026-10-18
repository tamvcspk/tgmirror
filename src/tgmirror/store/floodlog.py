"""SQL for ``flood_log``: the data the defaults of docs/05-chong-flood.md are tuned from."""

from datetime import datetime

import aiosqlite

from tgmirror.store.runs import FloodEvent


async def log_flood(
    db: aiosqlite.Connection,
    *,
    run_id: int | None,
    ts: str,
    kind: str,
    seconds: int | None,
    method: str,
    delay_ms: int | None,
    batch_size: int | None,
    backup_id: int | None = None,
    op_id: int | None = None,
    action: str | None = None,
    dst: int | None = None,
    sent_1h: int | None = None,
    sent_24h: int | None = None,
    premium: bool | None = None,
) -> None:
    """``run_id``/``backup_id``/``op_id`` are mutually exclusive: a run's flood events, a backup's
    (phase 11) and an operation's (phase 20) share this table but never the same row, so
    ``events_of_run`` never mixes them even though the ``runs``, ``backups`` and ``ops`` tables
    number their rows independently.

    Phase 19d1 adds the context a flood is understood by: the limiter lane (``action``/``dst``),
    how many messages that lane had sent in the last hour and 24 hours (writes only), and whether
    the account had Premium (``None``: not known)."""
    await db.execute(
        "INSERT INTO flood_log(run_id, backup_id, op_id, ts, kind, seconds, method, delay_ms, "
        "batch_size, action, dst, sent_1h, sent_24h, premium) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            backup_id,
            op_id,
            ts,
            kind,
            seconds,
            method,
            delay_ms,
            batch_size,
            action,
            dst,
            sent_1h,
            sent_24h,
            None if premium is None else int(premium),
        ),
    )


async def count_since(db: aiosqlite.Connection, since: datetime) -> int:
    """Rate-limit events of every run since ``since`` (one account, so the account's)."""
    cur = await db.execute("SELECT COUNT(*) FROM flood_log WHERE ts >= ?", (since.isoformat(),))
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


_EVENT = "SELECT ts, kind, seconds, method, action, dst, sent_1h, sent_24h FROM flood_log"


def _event(row: aiosqlite.Row) -> FloodEvent:
    return FloodEvent(datetime.fromisoformat(row[0]), *row[1:8])


async def events_of_run(db: aiosqlite.Connection, run_id: int) -> list[FloodEvent]:
    cur = await db.execute(f"{_EVENT} WHERE run_id = ? ORDER BY id", (run_id,))
    return [_event(r) for r in await cur.fetchall()]


async def events_of_backup(db: aiosqlite.Connection, backup_id: int) -> list[FloodEvent]:
    """As ``events_of_run``, for a backup (T1, Phase 15b: ``tgmirror status`` showing one)."""
    cur = await db.execute(f"{_EVENT} WHERE backup_id = ? ORDER BY id", (backup_id,))
    return [_event(r) for r in await cur.fetchall()]


async def events_of_op(db: aiosqlite.Connection, op_id: int) -> list[FloodEvent]:
    """As ``events_of_run``, for an operation (phase 20)."""
    cur = await db.execute(f"{_EVENT} WHERE op_id = ? ORDER BY id", (op_id,))
    return [_event(r) for r in await cur.fetchall()]
