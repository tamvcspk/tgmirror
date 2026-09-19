"""SQL for ``flood_log``: the data the defaults of docs/05-chong-flood.md are tuned from."""

import aiosqlite


async def log_flood(
    db: aiosqlite.Connection,
    *,
    job_id: int | None,
    ts: str,
    kind: str,
    seconds: int | None,
    method: str,
    delay_ms: int | None,
    batch_size: int | None,
) -> None:
    await db.execute(
        "INSERT INTO flood_log(job_id, ts, kind, seconds, method, delay_ms, batch_size) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (job_id, ts, kind, seconds, method, delay_ms, batch_size),
    )
