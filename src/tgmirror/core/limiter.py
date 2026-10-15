"""Pacing of Telegram calls: the AIMD limiter (docs/05-chong-flood.md).

Writes are spaced by ``delay``, which doubles on every flood (up to ``max_delay``) and shrinks by
10% after 20 successful writes (never below ``min_delay``). Since phase 19d1 every *lane* has a
delay of its own: a write lane is ``(action, destination)`` (``forward`` or ``send``), a read lane
is just the action (``history`` or ``file``), so a flood on one kind of call no longer slows the
others. A destination with slow mode never gets a post faster than its ``slowmode_seconds``
(``set_slow_mode``). A long pause follows every ``long_pause_every`` messages. Three floods within
ten minutes also halve the batch size and raise the floor of the delay until ten quiet minutes
have passed.

Messages sent are counted per write lane in hourly buckets (a sliding window, not a calendar
day). A FloodWait longer than ``short_flood`` seconds is a quota, not a burst (phase 19d2): it does
not double the delay but sets the lane's *budget* (80% of what the lane sent in the window it
names) or, if it has one, halves it; every 24 hours without such a flood the budget grows by 10%.
A lane with no budget is never stopped by one. A write that would pass the budget raises
``BudgetReached`` with the time the window lets enough go. ``daily_cap`` only warns
(``cap_warning``): it never stops anything.

The limiter only decides *how long to wait*; it never talks to Telegram or to the store. The
caller (``engine/flood.py``) reports what happened with ``on_success``/``on_flood`` and saves
``state`` so a restart does not forget the lesson. Time and sleeping are injected: tests use a fake
clock and a recording sleep.

The numbers are conservative starting points, not documented Telegram limits.
"""

import random
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from tgmirror.core.config import Limits
from tgmirror.core.errors import BudgetReached

Kind = Literal["read", "write"]
FORWARD, SEND, HISTORY, FILE = "forward", "send", "history", "file"
WRITE_ACTIONS = (FORWARD, SEND)
READ_ACTIONS = (HISTORY, FILE)
HOUR = 3600
KEPT_HOURS = 24  # buckets older than this are dropped: the longest window anything asks about
Lane = tuple[str, int]  # (action, destination); reads use destination 0
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]

DECAY_AFTER = 20  # consecutive successful writes before the delay shrinks
DECAY_FACTOR = 0.9
FLOOD_WINDOW = timedelta(minutes=10)
FLOODS_TO_THROTTLE = 3  # this many floods within FLOOD_WINDOW halve the batch size
MAX_THROTTLE = 6  # 100 -> 1 message per batch

# Budget learning (phase 19d2). Starting points, not Telegram's numbers; tuned from flood_log.
CUT_TO = 0.8  # a first long flood: budget = this share of what the lane sent in the window
HALVE = 0.5  # a long flood on a lane that has a budget
GROW = 1.1  # per GROW_EVERY without a long flood
GROW_EVERY = timedelta(hours=24)
MIN_BUDGET = 20  # messages: a budget never falls below this
SHORT_WINDOW_LIMIT = 3600  # a wait up to this many seconds names the 1-hour window, else 24 hours


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LaneBudget:
    """How many messages a write lane may send per sliding window, learned from a long flood."""

    amount: int
    window_hours: int  # 1 or 24
    since: datetime  # the last cut or growth: the clock the 24-hour growth counts from
    flood_at: datetime  # the long flood that set it, or last cut it
    flood_seconds: int  # ... and how long Telegram asked us to wait


@dataclass(frozen=True, slots=True)
class LaneState:
    """What one lane learned (table ``limiter_lanes``): its delay, and a budget once it has one."""

    action: str
    dst: int
    delay: float
    budget: LaneBudget | None = None


@dataclass(frozen=True, slots=True)
class SentHour:
    """Messages one write lane sent in one UTC hour (table ``sent_hours``)."""

    action: str
    dst: int
    hour: int  # unix time // 3600
    sent: int


@dataclass(frozen=True, slots=True)
class LimiterState:
    """What survives a restart (tables ``limiter_state``, ``limiter_lanes``, ``sent_hours``)."""

    delay: float  # the highest write-lane delay: what ``status`` calls "the delay"
    day: date  # the local day ``sent_today`` belongs to
    sent_today: int
    lanes: tuple[LaneState, ...] = ()
    hours: tuple[SentHour, ...] = ()


def grow_budget(budget: LaneBudget, now: datetime) -> LaneBudget:
    """``budget`` after 10% growth for each full day since it last changed (a pure function: the
    limiter and ``status`` both read a budget through it)."""
    days = (now - budget.since) // GROW_EVERY
    if days <= 0:
        return budget
    return LaneBudget(
        max(round(budget.amount * GROW**days), MIN_BUDGET),
        budget.window_hours,
        budget.since + days * GROW_EVERY,
        budget.flood_at,
        budget.flood_seconds,
    )


def window_sent(
    hours: Iterable[SentHour],
    now: datetime,
    window: int,
    *,
    action: str | None = None,
    dst: int | None = None,
) -> int:
    """Messages in the hourly buckets of the last ``window`` hours before ``now``.

    The current bucket and the ``window - 1`` before it count in full, the one before those by
    the share of its hour still inside the window: a sliding window made from buckets, exact
    enough for a budget and for the flood log. ``action``/``dst`` pick one lane (default: all)."""
    stamp = now.timestamp()
    current = int(stamp // HOUR)
    elapsed = (stamp % HOUR) / HOUR
    total = 0.0
    for h in hours:
        if (action is not None and h.action != action) or (dst is not None and h.dst != dst):
            continue
        age = current - h.hour
        if 0 <= age < window:
            total += h.sent
        elif age == window:
            total += h.sent * (1 - elapsed)
    return round(total)


class Limiter:
    def __init__(
        self,
        limits: Limits,
        sleep: Sleep,
        *,
        state: LimiterState | None = None,
        clock: Clock = _utc_now,
        rng: random.Random | None = None,
    ) -> None:
        self._limits = limits
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not security
        # A database from before the lanes has one delay only: a lane never seen starts from it.
        legacy = state.delay if state and not state.lanes else limits.min_delay
        self._start = self._bounded(legacy)
        self._lanes: dict[Lane, float] = {
            (lane.action, lane.dst): self._bounded(lane.delay)
            for lane in (state.lanes if state else ())
        }
        self._slow: dict[int, int] = {}  # destination -> slowmode_seconds
        self._budgets: dict[Lane, LaneBudget] = {
            (lane.action, lane.dst): lane.budget
            for lane in (state.lanes if state else ())
            if lane.budget is not None
        }
        self._cap_level = 0  # 0: no cap warning yet in this run, 1: warned at 1x, 2: at 2x
        self._hours: dict[tuple[str, int, int], int] = {
            (h.action, h.dst, h.hour): h.sent for h in (state.hours if state else ())
        }
        self._day = state.day if state else self._today()
        self._sent_today = state.sent_today if state else 0
        self._writes = 0  # write calls paced in this run: the first one goes out at once
        self._reads = 0
        self._since_long_pause = 0
        self._successes: dict[Lane, int] = {}  # consecutive successes per lane
        self._floods: deque[datetime] = deque()
        self._last_flood: datetime | None = None
        self._throttle = 0

    # ---- what the caller may read ----------------------------------------------------------

    @property
    def delay(self) -> float:
        """The highest delay of any write lane (``min_delay`` and up): the one pace to report."""
        writes = [d for (action, _), d in self._lanes.items() if action in WRITE_ACTIONS]
        return max(writes, default=self._start)

    def delay_of(self, action: str, dst: int = 0) -> float:
        """The delay of one lane, before jitter (a lane never used starts at the base delay)."""
        return self._lanes.get(self._lane(action, dst), self._start)

    def set_slow_mode(self, dst: int, seconds: int) -> None:
        """``dst`` has slow mode: no post to it goes out faster than ``seconds`` apart."""
        self._slow[dst] = max(seconds, 0)

    def slow_mode_of(self, dst: int) -> int:
        return self._slow.get(dst, 0)

    def sent_in(self, action: str, dst: int, hours: int) -> int:
        """Messages a write lane sent in roughly the last ``hours`` hours (the oldest bucket counts
        by the share of it still inside the window)."""
        return window_sent(
            (SentHour(a, d, h, n) for (a, d, h), n in self._hours.items()),
            self._clock(),
            hours,
            action=action,
            dst=dst,
        )

    def sent_total(self, hours: int = 24) -> int:
        """Messages every write lane of the account sent in roughly the last ``hours`` hours."""
        return window_sent(
            (SentHour(a, d, h, n) for (a, d, h), n in self._hours.items()), self._clock(), hours
        )

    def budget_of(self, action: str, dst: int = 0) -> LaneBudget | None:
        """A write lane's budget now, grown by 10% for each full day since it last changed."""
        lane = self._lane(action, dst)
        budget = self._budgets.get(lane)
        if budget is None:
            return None
        grown = grow_budget(budget, self._clock())
        self._budgets[lane] = grown
        return grown

    def budgets_for(self, dst: int) -> list[tuple[str, LaneBudget, int]]:
        """``(action, budget, sent in its window)`` of every write lane of ``dst`` that has one."""
        found = []
        for action in WRITE_ACTIONS:
            if (budget := self.budget_of(action, dst)) is not None:
                found.append((action, budget, self.sent_in(action, dst, budget.window_hours)))
        return found

    def cap_warning(self) -> int | None:
        """The account's 24-hour total, the first time it passes ``daily_cap`` in this run and
        again the first time it passes twice that; ``None`` otherwise. Only a reminder."""
        cap = self._limits.daily_cap
        total = self.sent_total(24)
        level = min(total // cap, 2)
        if level > self._cap_level:
            self._cap_level = level
            return total
        return None

    @property
    def throttle(self) -> int:
        """How many times the batch size is halved right now (0 = not throttled)."""
        self._relax()
        return self._throttle

    @property
    def state(self) -> LimiterState:
        self._rollover()
        self._prune_hours()
        keys = sorted(set(self._lanes) | set(self._budgets))
        lanes = tuple(
            LaneState(a, d, self._lanes.get((a, d), self._start), self._budgets.get((a, d)))
            for a, d in keys
        )
        hours = tuple(SentHour(a, d, h, n) for (a, d, h), n in sorted(self._hours.items()))
        return LimiterState(self.delay, self._day, self._sent_today, lanes, hours)

    def batch_size(self, requested: int) -> int:
        """The batch size to use now: ``requested`` halved once per active throttle step."""
        return max(1, requested >> self.throttle)

    # ---- pacing -----------------------------------------------------------------------------

    def check_budget(self, cost: int, action: str | None = None, dst: int = 0) -> None:
        """Raise ``BudgetReached`` if a write of ``cost`` messages would pass the lane's budget.

        A lane with no budget (it never met a long flood) is never stopped here. It consumes
        nothing: it lets the runner refuse *before* it spends time uploading what could not be
        posted yet."""
        action = action or SEND
        budget = self.budget_of(action, dst)
        if budget is None:
            return
        used = self.sent_in(action, dst, budget.window_hours)
        # ``and used``: a first batch bigger than the budget must not block forever
        if used and used + cost > budget.amount:
            raise BudgetReached(
                self._resume_at(action, dst, budget, used, cost),
                used,
                budget.amount,
                budget.window_hours,
                action,
                dst,
            )

    async def acquire(
        self,
        cost: int,
        kind: Kind = "write",
        credit: float = 0.0,
        *,
        action: str | None = None,
        dst: int = 0,
    ) -> None:
        """Wait until the next call may go out.

        ``cost`` is the number of messages of a write, or the number of requests of a read. Raises
        ``BudgetReached`` for a write that would pass the lane's budget. ``sleep`` may raise (the
        runner's does when a pause/stop is requested); nothing has been recorded then.

        ``credit`` is time the caller already spent between two writes (uploading the bytes of
        the next message): it counts towards the delay, never towards a long pause, so what
        Telegram limits, the gap between two posts, stays at least ``delay``.

        ``action``/``dst`` pick the lane whose delay applies (a write: ``forward``/``send`` and the
        destination; a read: ``history``/``file``); without them a write is a ``send`` and a read
        a ``history``.
        """
        self._relax()
        lim = self._limits
        if kind == "read":
            if self._reads:  # the first read of a run goes out at once
                scale = self.delay_of(action or HISTORY) / lim.min_delay  # its own lane's delay
                await self._sleep(cost * lim.read_delay * scale * self._jitter())
            self._reads += 1
            return

        self.check_budget(cost, action, dst)
        if self._writes:
            pace = self.delay_of(action or SEND, dst) * self._jitter()
            wait = max(max(pace, self._slow.get(dst, 0)) - credit, 0.0)  # slow mode: a hard floor
            if self._since_long_pause >= lim.long_pause_every:
                wait += self._rng.uniform(*lim.long_pause_range)
                self._since_long_pause = 0
            if wait > 0:
                await self._sleep(wait)
        self._writes += 1

    def on_success(self, cost: int, action: str | None = None, dst: int = 0) -> None:
        """A write of ``cost`` messages went through on the lane ``action``/``dst``."""
        self._rollover()
        action = action or SEND
        self._sent_today += cost
        self._since_long_pause += cost
        hour = (action, dst, int(self._clock().timestamp() // HOUR))
        self._hours[hour] = self._hours.get(hour, 0) + cost
        self._success(self._lane(action, dst))

    def on_read_success(self, action: str | None = None) -> None:
        """A read request on the lane ``action`` went through: its delay may shrink."""
        self._success(self._lane(action or HISTORY, 0))

    def on_flood(
        self,
        action: str | None = None,
        dst: int = 0,
        *,
        seconds: int | None = None,
        quota: bool = True,
    ) -> LaneBudget | None:
        """Telegram asked us to wait: back off *that lane*.

        A write lane whose wait is longer than ``short_flood`` seconds (``quota``: a real
        FloodWait, not slow mode, a transport 429 or a Premium wait) met its quota: its budget is
        set or cut and returned, and its delay is left alone. Anything else doubles the delay."""
        now = self._clock()
        lane = self._lane(action or SEND, dst)
        cut = None
        if (
            quota
            and seconds is not None
            and seconds > self._limits.short_flood
            and lane[0] in WRITE_ACTIONS
        ):
            cut = self._cut_budget(lane, seconds, now)
        else:
            self._lanes[lane] = min(self._lanes.get(lane, self._start) * 2, self._limits.max_delay)
        self._successes[lane] = 0
        self._last_flood = now
        self._floods.append(now)
        while now - self._floods[0] > FLOOD_WINDOW:
            self._floods.popleft()
        if len(self._floods) >= FLOODS_TO_THROTTLE:
            self._throttle = min(self._throttle + 1, MAX_THROTTLE)
            self._floods.clear()  # the next step needs three new floods
        return cut

    # ---- internals --------------------------------------------------------------------------

    @staticmethod
    def _lane(action: str, dst: int) -> Lane:
        return (action, dst if action in WRITE_ACTIONS else 0)

    def _cut_budget(self, lane: Lane, seconds: int, now: datetime) -> LaneBudget:
        """Set the budget of a lane that has none, or halve the one it has."""
        existing = self.budget_of(*lane)
        if existing is None:
            window = 1 if seconds <= SHORT_WINDOW_LIMIT else 24
            amount = round(self.sent_in(lane[0], lane[1], window) * CUT_TO)
        else:
            window, amount = existing.window_hours, round(existing.amount * HALVE)
        budget = LaneBudget(max(amount, MIN_BUDGET), window, now, now, seconds)
        self._budgets[lane] = budget
        return budget

    def _resume_at(
        self, action: str, dst: int, budget: LaneBudget, used: int, cost: int
    ) -> datetime:
        """When the window has let go of enough messages for ``cost`` more to fit: the end of the
        window of the oldest hour bucket whose release makes room (a bucket counts in full until
        the whole of its hour has left the window, so this is never early)."""
        current = int(self._clock().timestamp() // HOUR)
        mine = sorted(
            (hour, sent)
            for (a, d, hour), sent in self._hours.items()
            if a == action and d == dst and current - hour <= budget.window_hours
        )
        left, expiry = used, current + 1
        for hour, sent in mine:
            left -= sent
            expiry = hour + budget.window_hours + 1
            if left + cost <= budget.amount:
                break
        return datetime.fromtimestamp(expiry * HOUR, UTC).astimezone()

    def _bounded(self, delay: float) -> float:
        lim = self._limits  # the config may have changed since the delay was saved
        return min(max(delay, lim.min_delay), lim.max_delay)

    def _success(self, lane: Lane) -> None:
        count = self._successes.get(lane, 0) + 1
        if count >= DECAY_AFTER:
            count = 0
            self._lanes[lane] = max(
                self._lanes.get(lane, self._start) * DECAY_FACTOR, self._floor()
            )
        self._successes[lane] = count

    def _prune_hours(self) -> None:
        current = int(self._clock().timestamp() // HOUR)
        for key in [k for k in self._hours if current - k[2] > KEPT_HOURS]:
            del self._hours[key]

    def _jitter(self) -> float:
        j = self._limits.jitter
        return self._rng.uniform(1 - j, 1 + j)

    def _floor(self) -> float:
        """Lowest delay the decay may reach: ``min_delay``, doubled per throttle step."""
        return min(self._limits.min_delay * 2**self._throttle, self._limits.max_delay)

    def _relax(self) -> None:
        """Lift the throttle once ``FLOOD_WINDOW`` has passed without a flood."""
        if (
            self._throttle
            and self._last_flood is not None
            and self._clock() - self._last_flood >= FLOOD_WINDOW
        ):
            self._throttle = 0

    def _today(self) -> date:
        return self._clock().astimezone().date()

    def _rollover(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._sent_today = today, 0
