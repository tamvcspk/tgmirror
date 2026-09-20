"""What the runner does about Telegram's rate limits, for reads and writes alike.

``FloodGuard`` sits between the runner and the gateway (docs/05-chong-flood.md, "Xử lý FloodWait"):

- ``pace`` waits for the limiter before a write; ``write`` runs the call, and on a FloodWait logs
  it, backs the limiter off, sleeps ``seconds`` plus a little jitter and then repeats **the same
  call** (the batch is already ``pending``, so nothing is rebuilt and the cursor does not move).
- ``reader`` wraps the gateway's reads the same way: ``iter_messages`` paces every read request
  and, after a FloodWait, carries on from the last message it handed out; ``get_messages`` (a
  retry reading failed messages by id) is one paced request, repeated after a FloodWait.
- A wait longer than ``max_auto_wait`` (unless ``wait``) or too many floods in a row on one call
  are not slept through: the FloodWait propagates and the runner parks the run as
  ``waiting_flood``. PeerFlood is logged and propagates at once; it is never retried.

Every sleep goes through ``nap``, which raises ``Interrupted`` when a pause/stop is requested, so
none of this can hold up Ctrl+C.
"""

import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import aclosing
from typing import Protocol, TypeVar

from tgmirror.core.config import Limits
from tgmirror.core.errors import FloodWait, PeerFlood
from tgmirror.core.gateway import NO_FILTER, MessageReader, ServerFilter, SrcMessage
from tgmirror.core.limiter import Limiter, Sleep
from tgmirror.store.db import Store
from tgmirror.store.runs import Run

T = TypeVar("T")

# Telegram keeps refusing the very same call: stop rather than sleep in a loop. The run is parked
# and a later run tries again.
MAX_FLOODS_PER_CALL = 5


class Interrupted(Exception):
    """A sleep was cut short because a pause/stop was requested (raised by the runner's nap)."""


class Notifier(Protocol):
    def notice(self, code: str, **params: object) -> None: ...


class FloodGuard:
    def __init__(
        self,
        *,
        limiter: Limiter,
        store: Store,
        run: Run,
        limits: Limits,
        notifier: Notifier,
        nap: Sleep,
        rng: random.Random | None = None,
        wait: bool = False,
    ) -> None:
        self._limiter = limiter
        self._store = store
        self._run = run
        self._limits = limits
        self._notifier = notifier
        self._nap = nap
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not security
        self._wait = wait

    # ---- writes -----------------------------------------------------------------------------

    async def pace(self, cost: int) -> None:
        """Wait until a write of ``cost`` messages may go out. Raises ``DailyCapReached``."""
        await self._limiter.acquire(cost, "write")

    async def pace_read(self, requests: int) -> None:
        """Wait until ``requests`` read requests may go out (their own, lighter bucket)."""
        await self._limiter.acquire(requests, "read")

    async def write(self, method: str, cost: int, call: Callable[[], Awaitable[T]]) -> T:
        """Run ``call`` (a write of ``cost`` messages), repeating it after each FloodWait."""
        floods = 0
        while True:
            try:
                result = await call()
            except FloodWait as exc:
                floods += 1
                await self.flooded(method, exc, give_up=floods >= MAX_FLOODS_PER_CALL)
                continue
            except PeerFlood:
                await self.peer_flood(method)
                raise
            self._limiter.on_success(cost)
            return result

    # ---- reads ------------------------------------------------------------------------------

    def reader(self, inner: MessageReader) -> MessageReader:
        """``inner`` with every read paced and retried after a FloodWait."""
        return _GuardedReader(self, inner)

    # ---- flood ------------------------------------------------------------------------------

    async def flooded(self, method: str, exc: FloodWait, *, give_up: bool = False) -> None:
        """Handle one FloodWait: log it, back off, then sleep it out (or re-raise it).

        Returning means the wait is over and the call may be repeated.
        """
        await self._log(method, "slow_mode" if exc.slow_mode else "flood_wait", exc.seconds)
        throttled = self._limiter.throttle
        self._limiter.on_flood()
        await self._store.save_limiter_state(self._run.account, self._limiter.state)
        if self._limiter.throttle > throttled:
            self._notifier.notice(
                "throttled",
                batch_size=self._limiter.batch_size(self._run.options.batch_size),
                delay=round(self._limiter.delay, 1),
            )
        if give_up or (exc.seconds > self._limits.max_auto_wait and not self._wait):
            raise exc
        self._notifier.notice("flood_waiting", seconds=exc.seconds)
        await self._nap(exc.seconds + self._rng.uniform(1, 5))

    async def peer_flood(self, method: str) -> None:
        """Record a PEER_FLOOD. The caller re-raises it: it is never waited out or retried."""
        await self._log(method, "peer_flood", None)

    async def _log(self, method: str, kind: str, seconds: int | None) -> None:
        """One ``flood_log`` row; its delay is the one that led to the flood, before backing off."""
        await self._store.log_flood(
            self._run.id,
            kind=kind,
            seconds=seconds,
            method=method,
            delay_ms=int(self._limiter.delay * 1000),
            batch_size=self._limiter.batch_size(self._run.options.batch_size),
        )


class _GuardedReader:
    def __init__(self, guard: FloodGuard, inner: MessageReader) -> None:
        self._guard = guard
        self._inner = inner

    async def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        # a date bound costs the gateway one more request to turn the date into an id
        requests = 1 + (filters.since is not None) + (filters.until is not None)
        position, floods = min_id, 0
        while True:
            await self._guard.pace_read(requests)
            try:
                async with aclosing(
                    self._inner.iter_messages(src, min_id=position, filters=filters)
                ) as stream:
                    async for message in stream:
                        position, floods = message.id, 0  # progress: the next read starts after it
                        yield message
                return
            except FloodWait as exc:
                floods += 1
                give_up = floods >= MAX_FLOODS_PER_CALL
                await self._guard.flooded("iter_messages", exc, give_up=give_up)
            except PeerFlood:
                await self._guard.peer_flood("iter_messages")
                raise

    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]:
        floods = 0
        while True:
            await self._guard.pace_read(1)
            try:
                return await self._inner.get_messages(src, ids)
            except FloodWait as exc:
                floods += 1
                await self._guard.flooded(
                    "get_messages", exc, give_up=floods >= MAX_FLOODS_PER_CALL
                )
            except PeerFlood:
                await self._guard.peer_flood("get_messages")
                raise
