"""SQL for ``limiter_state`` and its lanes: what the limiter learned, kept per account."""

from datetime import date, datetime

import aiosqlite

from tgmirror.core.limiter import LaneBudget, LaneState, LimiterState, SentBucket


async def load(db: aiosqlite.Connection, account: str) -> LimiterState | None:
    cur = await db.execute(
        "SELECT delay_ms, day, sent_today FROM limiter_state WHERE account = ?", (account,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    cur = await db.execute(
        "SELECT action, dst, delay_ms, budget, window_hours, budget_since, flood_at, "
        "flood_seconds, ok_amount, bad_amount, bad_at, checked_at, probe_fails "
        "FROM limiter_lanes WHERE account = ? ORDER BY action, dst",
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
                ok=r[8],
                bad=r[9],
                bad_at=datetime.fromisoformat(r[10]) if r[10] is not None else None,
                checked_at=datetime.fromisoformat(r[11]) if r[11] is not None else None,
                probe_fails=r[12],
            )
            if r[3] is not None
            else None,
        )
        for r in await cur.fetchall()
    )
    cur = await db.execute(
        "SELECT action, dst, bucket, sent FROM sent_buckets WHERE account = ? "
        "ORDER BY action, dst, bucket",
        (account,),
    )
    buckets = tuple(SentBucket(r[0], r[1], r[2], r[3]) for r in await cur.fetchall())
    return LimiterState(
        row["delay_ms"] / 1000, date.fromisoformat(row["day"]), row["sent_today"], lanes, buckets
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
        "budget_since, flood_at, flood_seconds, ok_amount, bad_amount, bad_at, checked_at, "
        "probe_fails) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    await db.execute("DELETE FROM sent_buckets WHERE account = ?", (account,))
    await db.executemany(
        "INSERT INTO sent_buckets(account, action, dst, bucket, sent) VALUES(?, ?, ?, ?, ?)",
        [(account, b.action, b.dst, b.bucket, b.sent) for b in state.buckets],
    )


async def forget_budgets(db: aiosqlite.Connection, account: str, dst: int | None) -> int:
    """Clear what the write lanes of ``dst`` (every destination: ``None``) learned about their
    limits; their delays stay. Returns how many lanes had a budget."""
    where = "account = ? AND budget IS NOT NULL AND action IN ('forward', 'send')"
    args: tuple[object, ...] = (account,)
    if dst is not None:
        where += " AND dst = ?"
        args = (account, dst)
    cur = await db.execute(
        "UPDATE limiter_lanes SET budget = NULL, window_hours = NULL, budget_since = NULL, "
        "flood_at = NULL, flood_seconds = NULL, ok_amount = NULL, bad_amount = NULL, "
        f"bad_at = NULL, checked_at = NULL, probe_fails = 0 WHERE {where}",
        args,
    )
    return cur.rowcount


def _budget_columns(
    budget: LaneBudget | None,
) -> tuple[
    int | None,
    int | None,
    str | None,
    str | None,
    int | None,
    int | None,
    int | None,
    str | None,
    str | None,
    int,
]:
    if budget is None:
        return (None, None, None, None, None, None, None, None, None, 0)
    return (
        budget.amount,
        budget.window_hours,
        budget.since.isoformat(),
        budget.flood_at.isoformat(),
        budget.flood_seconds,
        budget.ok,
        budget.bad,
        budget.bad_at.isoformat() if budget.bad_at is not None else None,
        budget.checked_at.isoformat() if budget.checked_at is not None else None,
        budget.probe_fails,
    )
