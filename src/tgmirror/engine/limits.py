"""What the limiter learned about each write lane, as ``tgmirror limits`` shows it (phase 19d4).

Reads the store only (no Telegram): the account's lanes, with the budget each grew into by now,
what it sent in its window, and both sides of what is known about the limit — ``bad`` (the fewest
messages per window that ever drew a long flood) and ``ok`` (the most that ran clean). A lane with
no budget has met no long flood yet: only its pacing and FloodWait limit it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from tgmirror.core.limiter import BAD_TTL, WRITE_ACTIONS, grow_budget, window_sent
from tgmirror.store.db import Store

ACCOUNT = "default"


@dataclass(frozen=True, slots=True)
class LaneInfo:
    action: str
    dst: int
    dst_title: str | None  # from the log of pairs; ``None``: no run into it is logged
    delay: float
    amount: int | None  # ``None``: not learned yet
    window_hours: int | None
    sent: int  # what the lane sent in its window (24 hours when it has no budget)
    ok: int | None
    bad: int | None
    checked_at: datetime | None
    probe_fails: int
    flood_at: datetime | None
    flood_seconds: int | None


async def lane_infos(
    store: Store, *, now: datetime, bad_ttl: timedelta = BAD_TTL, account: str = ACCOUNT
) -> list[LaneInfo]:
    """The write lanes of ``account``, by destination then action."""
    state = await store.load_limiter_state(account)
    if state is None:
        return []
    titles = {run.dst_id: run.dst_title for run in await store.list_pairs(1000)}
    found = []
    for lane in state.lanes:
        if lane.action not in WRITE_ACTIONS:
            continue
        budget = grow_budget(lane.budget, now, bad_ttl) if lane.budget is not None else None
        window = budget.window_hours if budget is not None else 24
        found.append(
            LaneInfo(
                lane.action,
                lane.dst,
                titles.get(lane.dst),
                lane.delay,
                budget.amount if budget else None,
                budget.window_hours if budget else None,
                window_sent(state.buckets, now, window, action=lane.action, dst=lane.dst),
                budget.ok if budget else None,
                budget.bad if budget else None,
                budget.checked_at if budget else None,
                budget.probe_fails if budget else 0,
                budget.flood_at if budget else None,
                budget.flood_seconds if budget else None,
            )
        )
    return sorted(found, key=lambda i: (i.dst, i.action))
