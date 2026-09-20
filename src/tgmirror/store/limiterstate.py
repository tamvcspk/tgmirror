"""SQL for ``limiter_state``: what the limiter learned, kept per account between runs."""

from datetime import date

import aiosqlite

from tgmirror.core.limiter import LimiterState


async def load(db: aiosqlite.Connection, account: str) -> LimiterState | None:
    cur = await db.execute(
        "SELECT delay_ms, day, sent_today FROM limiter_state WHERE account = ?", (account,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return LimiterState(row["delay_ms"] / 1000, date.fromisoformat(row["day"]), row["sent_today"])


async def save(db: aiosqlite.Connection, account: str, state: LimiterState, ts: str) -> None:
    await db.execute(
        "INSERT INTO limiter_state(account, delay_ms, day, sent_today, updated_at) "
        "VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(account) DO UPDATE SET delay_ms = excluded.delay_ms, day = excluded.day, "
        "sent_today = excluded.sent_today, updated_at = excluded.updated_at",
        (account, round(state.delay * 1000), state.day.isoformat(), state.sent_today, ts),
    )
