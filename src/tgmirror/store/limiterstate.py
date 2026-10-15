"""SQL for ``limiter_state`` and its lanes: what the limiter learned, kept per account."""

from datetime import date, datetime

import aiosqlite

from tgmirror.core.limiter import LaneBudget, LaneState, LimiterState, SentHour


async def load(db: aiosqlite.Connection, account: str) -> LimiterState | None:
    cur = await db.execute(
        "SELECT delay_ms, day, sent_today FROM limiter_state WHERE account = ?", (account,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    cur = await db.execute(
        "SELECT action, dst, delay_ms, budget, window_hours, budget_since, flood_at, "
        "flood_seconds FROM limiter_lanes WHERE account = ? ORDER BY action, dst",
        (account,),
    )
    lanes = tuple(
        LaneState(
            r[0],
            r[1],
            r[2] / 1000,
            LaneBudget(
                r[3],
                r[4],
                datetime.fromisoformat(r[5]),
                datetime.fromisoformat(r[6]),
                r[7],
            )
            if r[3] is not None
            else None,
        )
        for r in await cur.fetchall()
    )
    cur = await db.execute(
        "SELECT action, dst, hour, sent FROM sent_hours WHERE account = ? "
        "ORDER BY action, dst, hour",
        (account,),
    )
    hours = tuple(SentHour(r[0], r[1], r[2], r[3]) for r in await cur.fetchall())
    return LimiterState(
        row["delay_ms"] / 1000, date.fromisoformat(row["day"]), row["sent_today"], lanes, hours
    )


async def save(db: aiosqlite.Connection, account: str, state: LimiterState, ts: str) -> None:
    await db.execute(
        "INSERT INTO limiter_state(account, delay_ms, day, sent_today, updated_at) "
        "VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(account) DO UPDATE SET delay_ms = excluded.delay_ms, day = excluded.day, "
        "sent_today = excluded.sent_today, updated_at = excluded.updated_at",
        (account, round(state.delay * 1000), state.day.isoformat(), state.sent_today, ts),
    )
    # A limiter loads every lane and bucket it finds and hands them all back, so replacing the
    # account's rows forgets nothing; one process per session keeps two limiters from racing.
    await db.execute("DELETE FROM limiter_lanes WHERE account = ?", (account,))
    await db.executemany(
        "INSERT INTO limiter_lanes(account, action, dst, delay_ms, budget, window_hours, "
        "budget_since, flood_at, flood_seconds) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                account,
                lane.action,
                lane.dst,
                round(lane.delay * 1000),
                *_budget_columns(lane.budget),
            )
            for lane in state.lanes
        ],
    )
    await db.execute("DELETE FROM sent_hours WHERE account = ?", (account,))
    await db.executemany(
        "INSERT INTO sent_hours(account, action, dst, hour, sent) VALUES(?, ?, ?, ?, ?)",
        [(account, h.action, h.dst, h.hour, h.sent) for h in state.hours],
    )


def _budget_columns(
    budget: LaneBudget | None,
) -> tuple[int | None, int | None, str | None, str | None, int | None]:
    if budget is None:
        return (None, None, None, None, None)
    return (
        budget.amount,
        budget.window_hours,
        budget.since.isoformat(),
        budget.flood_at.isoformat(),
        budget.flood_seconds,
    )
