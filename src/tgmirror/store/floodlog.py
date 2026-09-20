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
) -> None:
    await db.execute(
        "INSERT INTO flood_log(run_id, ts, kind, seconds, method, delay_ms, batch_size) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (run_id, ts, kind, seconds, method, delay_ms, batch_size),
    )


async def events_of_run(db: aiosqlite.Connection, run_id: int) -> list[FloodEvent]:
    cur = await db.execute(
        "SELECT ts, kind, seconds, method FROM flood_log WHERE run_id = ? ORDER BY id", (run_id,)
    )
    return [
        FloodEvent(datetime.fromisoformat(r[0]), r[1], r[2], r[3]) for r in await cur.fetchall()
    ]
