"""Pacing of write calls (docs/05-chong-flood.md).

**Interim, phase 2.** It only spaces the batches: ``min_delay`` with jitter, and a long pause every
``long_pause_every`` messages. Phase 4 replaces the body with the AIMD limiter (delay doubling on
flood, decay after successes, daily cap, ``limiter_state``) behind this same ``acquire`` interface.
Until then a FloodWait stops the job instead of being waited out (see ``engine/runner.py``).

The numbers are conservative starting points, not documented Telegram limits.
"""

import random
from collections.abc import Awaitable, Callable

from tgmirror.core.config import Limits

Sleep = Callable[[float], Awaitable[None]]


class Limiter:
    def __init__(self, limits: Limits, sleep: Sleep, rng: random.Random | None = None) -> None:
        self._limits = limits
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not security
        self._calls = 0
        self._since_long_pause = 0

    async def acquire(self, cost: int) -> None:
        """Wait until the next write call of ``cost`` messages may go out.

        ``sleep`` may return early (the runner's sleep does when a pause/stop is requested); the
        caller must check for that before it sends.
        """
        if self._calls:  # the first call of a run goes out at once
            lim = self._limits
            delay = lim.min_delay * self._rng.uniform(1 - lim.jitter, 1 + lim.jitter)
            if self._since_long_pause >= lim.long_pause_every:
                delay += self._rng.uniform(*lim.long_pause_range)
                self._since_long_pause = 0
            await self._sleep(delay)
        self._calls += 1
        self._since_long_pause += cost
