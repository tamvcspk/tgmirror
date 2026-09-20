"""Pacing of Telegram calls: the AIMD limiter (docs/05-chong-flood.md).

Writes are spaced by ``delay``, which doubles on every flood (up to ``max_delay``) and shrinks by
10% after 20 successful writes (never below ``min_delay``). Reads have their own, lighter bucket
that follows the same delay. A long pause follows every ``long_pause_every`` messages, and the
account may send ``daily_cap`` messages per local day. Three floods within ten minutes also halve
the batch size and raise the floor of the delay until ten quiet minutes have passed.

The limiter only decides *how long to wait*; it never talks to Telegram or to the store. The
caller (``engine/flood.py``) reports what happened with ``on_success``/``on_flood`` and saves
``state`` so a restart does not forget the lesson. Time and sleeping are injected: tests use a fake
clock and a recording sleep.

The numbers are conservative starting points, not documented Telegram limits.
"""

import random
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from tgmirror.core.config import Limits
from tgmirror.core.errors import DailyCapReached

Kind = Literal["read", "write"]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]

DECAY_AFTER = 20  # consecutive successful writes before the delay shrinks
DECAY_FACTOR = 0.9
FLOOD_WINDOW = timedelta(minutes=10)
FLOODS_TO_THROTTLE = 3  # this many floods within FLOOD_WINDOW halve the batch size
MAX_THROTTLE = 6  # 100 -> 1 message per batch


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LimiterState:
    """What survives a restart (table ``limiter_state``)."""

    delay: float  # seconds between two write calls, before jitter
    day: date  # the local day ``sent_today`` belongs to
    sent_today: int


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
        start = state.delay if state else limits.min_delay
        self._delay = min(max(start, limits.min_delay), limits.max_delay)  # config may have changed
        self._day = state.day if state else self._today()
        self._sent_today = state.sent_today if state else 0
        self._writes = 0  # write calls paced in this run: the first one goes out at once
        self._reads = 0
        self._since_long_pause = 0
        self._successes = 0
        self._floods: deque[datetime] = deque()
        self._last_flood: datetime | None = None
        self._throttle = 0

    # ---- what the caller may read ----------------------------------------------------------

    @property
    def delay(self) -> float:
        return self._delay

    @property
    def throttle(self) -> int:
        """How many times the batch size is halved right now (0 = not throttled)."""
        self._relax()
        return self._throttle

    @property
    def state(self) -> LimiterState:
        self._rollover()
        return LimiterState(self._delay, self._day, self._sent_today)

    def batch_size(self, requested: int) -> int:
        """The batch size to use now: ``requested`` halved once per active throttle step."""
        return max(1, requested >> self.throttle)

    # ---- pacing -----------------------------------------------------------------------------

    def check_cap(self, cost: int) -> None:
        """Raise ``DailyCapReached`` if a write of ``cost`` messages would pass the daily cap.

        It consumes nothing: it lets the runner refuse *before* it spends time uploading what
        could not be posted today."""
        self._rollover()
        lim = self._limits
        # ``and self._sent_today``: a first batch bigger than the cap must not block forever
        if self._sent_today and self._sent_today + cost > lim.daily_cap:
            raise DailyCapReached(self._next_midnight(), self._sent_today, lim.daily_cap)

    async def acquire(self, cost: int, kind: Kind = "write", credit: float = 0.0) -> None:
        """Wait until the next call may go out.

        ``cost`` is the number of messages of a write, or the number of requests of a read. Raises
        ``DailyCapReached`` for a write that would pass the daily cap. ``sleep`` may raise (the
        runner's does when a pause/stop is requested); nothing has been recorded then.

        ``credit`` is time the caller already spent between two writes (uploading the bytes of
        the next message): it counts towards the delay, never towards a long pause, so what
        Telegram limits, the gap between two posts, stays at least ``delay``.
        """
        self._relax()
        lim = self._limits
        if kind == "read":
            if self._reads:  # the first read of a run goes out at once
                scale = self._delay / lim.min_delay  # reads slow down together with writes
                await self._sleep(cost * lim.read_delay * scale * self._jitter())
            self._reads += 1
            return

        self.check_cap(cost)
        if self._writes:
            wait = max(self._delay * self._jitter() - credit, 0.0)
            if self._since_long_pause >= lim.long_pause_every:
                wait += self._rng.uniform(*lim.long_pause_range)
                self._since_long_pause = 0
            if wait > 0:
                await self._sleep(wait)
        self._writes += 1

    def on_success(self, cost: int) -> None:
        """A write of ``cost`` messages went through."""
        self._rollover()
        self._sent_today += cost
        self._since_long_pause += cost
        self._successes += 1
        if self._successes >= DECAY_AFTER:
            self._successes = 0
            self._delay = max(self._delay * DECAY_FACTOR, self._floor())

    def on_flood(self) -> None:
        """Telegram asked us to wait (FloodWait or slow mode): back off."""
        now = self._clock()
        self._delay = min(self._delay * 2, self._limits.max_delay)
        self._successes = 0
        self._last_flood = now
        self._floods.append(now)
        while now - self._floods[0] > FLOOD_WINDOW:
            self._floods.popleft()
        if len(self._floods) >= FLOODS_TO_THROTTLE:
            self._throttle = min(self._throttle + 1, MAX_THROTTLE)
            self._floods.clear()  # the next step needs three new floods

    # ---- internals --------------------------------------------------------------------------

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

    def _next_midnight(self) -> datetime:
        """00:00 local time of the next day: when the daily cap starts counting from zero."""
        return datetime.combine(self._today() + timedelta(days=1), time.min).astimezone()
