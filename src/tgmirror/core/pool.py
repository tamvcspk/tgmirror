"""The request budget and the part scheduler behind fast file transfers.

Moving a file one request at a time is bound by latency, not bandwidth (docs/06-lo-trinh.md,
spike 12: 4 MB/s one request at a time, 30 MB/s with eight in flight). Two pieces fix that without
making the account look like a flood:

- ``RequestBudget`` is the one number of file-transfer requests that may be in flight at once,
  shared by every download and upload of the process. It starts small, grows a step at a time while
  things go well, and is halved when the server pushes back. 8 connections / 8 requests in flight
  (one per connection), one file at a time, measured clean twice in a row; going past either number
  broke both times it was tried (docs/06-lo-trinh.md, 2026-09-23) — so that stays the ceiling.
- ``run_parts`` runs the parts of one file under that budget: workers take the next part, whoever is
  free, so a slow connection simply does fewer parts. A part that meets pushback is repeated after a
  wait; a FloodWait ends the whole transfer (the caller's ``FloodGuard`` sits it out and repeats).

Nothing here knows about Telegram or Telethon: the adapter in ``core/telethon_gateway.py`` supplies
the ``work`` that moves one part and turns transport errors into ``TransportPressure``.
"""

import asyncio
import contextlib
import heapq
import itertools
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from tgmirror.core.errors import FloodWait, Transient, TransportPressure

log = logging.getLogger(__name__)  # ``tgmirror.core.pool``: shown by the CLI, warnings and up

GROW_AFTER = 32  # parts that must go through, without pushback, before the budget grows a step
COOLDOWN = 30.0  # seconds after pushback during which the budget does not grow
ATTEMPTS = 5  # tries for one part before the transfer is given up (``Transient``)
BACKOFF = 1.0  # seconds; doubles with every failed try of the same part
# HTTP 429 on the connection: the first real run showed that retrying within seconds only got
# the run killed. It becomes a FloodWait of this many seconds: ``FloodGuard`` logs it, backs
# off, sits it out and repeats the transfer (the parts already done are kept).
TRANSPORT_WAIT = 60


class RequestBudget:
    """How many file-transfer requests may be in flight at once (AIMD, like the limiter).

    ``acquire`` waits for a free slot; a lower ``priority`` number is served first (an upload,
    which belongs to an older unit, before the download of the next one). The limit moves between
    1 and ``maximum``: ``succeeded`` grows it by one after ``grow_after`` parts in a row that met no
    pushback (and not within ``cooldown`` seconds of the last), ``pressure`` halves it.
    """

    def __init__(
        self,
        *,
        start: int,
        maximum: int,
        grow_after: int = GROW_AFTER,
        cooldown: float = COOLDOWN,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= start <= maximum:
            raise ValueError("a budget needs 1 <= start <= maximum")
        self._limit = start
        self._maximum = maximum
        self._grow_after = grow_after
        self._cooldown = cooldown
        self._clock = clock
        self._in_flight = 0
        self._successes = 0
        self._quiet_until = 0.0
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def maximum(self) -> int:
        return self._maximum

    @property
    def in_flight(self) -> int:
        return self._in_flight

    async def acquire(self, priority: int = 0) -> None:
        while self._waiters and self._waiters[0][2].cancelled():
            heapq.heappop(self._waiters)  # abandoned by a cancelled task: nobody is ahead of us
        if self._in_flight < self._limit and not self._waiters:
            self._in_flight += 1
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (priority, next(self._order), waiter))
        try:
            await waiter
        except BaseException:
            if waiter.done() and not waiter.cancelled():
                self.release()  # the slot was handed over just as this waiter was cancelled
            raise

    def release(self) -> None:
        self._in_flight -= 1
        self._wake()

    @contextlib.asynccontextmanager
    async def slot(self, priority: int = 0) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            self.release()

    def succeeded(self) -> None:
        """A part went through without pushback."""
        self._successes += 1
        if (
            self._successes >= self._grow_after
            and self._limit < self._maximum
            and self._clock() >= self._quiet_until
        ):
            self._limit += 1
            self._successes = 0
            self._wake()

    def pressure(self, reason: str = "") -> None:
        """The server pushed back (a FloodWait, HTTP 429, a closed connection): halve the limit.

        Said aloud (a warning): a transfer that suddenly runs at a fraction of its speed is
        otherwise a mystery, and this is what makes it one request at a time."""
        before = self._limit
        self._limit = max(1, self._limit // 2)
        log.warning(
            "transfer requests in flight: %d -> %d (%s); growing again after %d clean parts",
            before,
            self._limit,
            reason or "pushback",
            self._grow_after,
        )
        self._successes = 0
        self._quiet_until = self._clock() + self._cooldown

    def _wake(self) -> None:
        while self._waiters and self._in_flight < self._limit:
            _, _, waiter = heapq.heappop(self._waiters)
            if waiter.cancelled():
                continue
            self._in_flight += 1
            waiter.set_result(None)


async def run_parts(
    count: int,
    work: Callable[[int], Awaitable[None]],
    budget: RequestBudget,
    *,
    priority: int = 0,
    attempts: int = ATTEMPTS,
    backoff: float = BACKOFF,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> None:
    """Run ``work(0) .. work(count - 1)``, each under a slot of ``budget``, in parallel.

    The parts are taken in order by as many workers as the budget can ever allow; each part is done
    exactly once. ``TransportPressure`` from a part halves the budget and repeats that part after
    an exponentially growing wait (the slot is free meanwhile), ``attempts`` times at most and then
    ``Transient``; one that says it is a flood (HTTP 429) is not repeated in place but ends the
    transfer as a ``FloodWait`` of ``TRANSPORT_WAIT`` seconds. ``FloodWait`` halves the budget and
    ends the transfer at once, as does any other error; whatever is still running is cancelled
    before this returns or raises.
    """
    if count <= 0:
        return
    jitter = rng or random.Random()  # noqa: S311 - a wait's jitter, not security
    parts = iter(range(count))

    async def attempt(index: int) -> None:
        for tries in range(attempts):
            async with budget.slot(priority):
                try:
                    await work(index)
                except TransportPressure as exc:
                    budget.pressure(str(exc))
                    if exc.flood:
                        raise FloodWait(TRANSPORT_WAIT, transport=True) from exc
                except FloodWait as exc:
                    budget.pressure(str(exc))
                    raise
                else:
                    budget.succeeded()
                    return
            await sleep(backoff * 2**tries * jitter.uniform(0.75, 1.25))
        raise Transient(f"part {index} of a transfer kept meeting pushback from Telegram")

    async def worker() -> None:
        for index in parts:
            await attempt(index)

    tasks = [asyncio.create_task(worker()) for _ in range(min(count, budget.maximum))]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            task.result()  # the first failure, if any
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
