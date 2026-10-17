"""What the runner does about Telegram's rate limits, for reads and writes alike.

``FloodGuard`` sits between the runner and the gateway (docs/05-chong-flood.md, "Xử lý FloodWait"):

- ``pace`` waits for the limiter before a write; ``write`` runs the call, and on a FloodWait logs
  it, backs the limiter off, sleeps ``seconds`` plus a little jitter and then repeats **the same
  call** (the batch is already ``pending``, so nothing is rebuilt and the cursor does not move).
- ``reader`` wraps the gateway's reads the same way: ``iter_messages`` paces every read request
  and, after a FloodWait, carries on from the last message it handed out; ``get_messages`` (a
  retry reading failed messages by id) is one paced request, repeated after a FloodWait; ``count``
  (the total progress is measured against) is the same but not paced: it opens the run.
- A wait longer than ``max_auto_wait`` (unless ``wait``) or too many floods in a row on one call
  are not slept through: the FloodWait propagates and the runner parks the run as
  ``waiting_flood``. PeerFlood is logged and propagates at once; it is never retried.

Every call belongs to a limiter *lane* (phase 19d1, ``core/limiter.py``): ``forward`` or ``send``
for a write (per destination), ``history`` or ``file`` for a read. A flood backs off only its own
lane, and ``flood_log`` records the lane and how much it had sent when Telegram pushed back.

Every sleep goes through ``nap``, which raises ``Interrupted`` when a pause/stop is requested, so
none of this can hold up Ctrl+C.
"""

import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from tgmirror.core.config import Limits
from tgmirror.core.errors import FloodWait, GatewayError, PeerFlood
from tgmirror.core.gateway import (
    NO_FILTER,
    ChatKind,
    ExportedMessage,
    MediaCounters,
    MessageReader,
    OnTransfer,
    Prepared,
    ServerFilter,
    SrcMessage,
    TelegramGateway,
    TopicInfo,
    Unit,
)
from tgmirror.core.limiter import (
    FILE,
    FORWARD,
    HISTORY,
    SEND,
    WRITE_ACTIONS,
    Limiter,
    Sleep,
)
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store
from tgmirror.store.runs import Run

T = TypeVar("T")

# Telegram keeps refusing the very same call: stop rather than sleep in a loop. The run is parked
# and a later run tries again.
MAX_FLOODS_PER_CALL = 5

# The limiter lane each gateway method belongs to. A method not listed is a write on ``send``.
LANES: dict[str, str] = {
    "copy_messages": FORWARD,
    "send_prepared": SEND,
    "send_text": SEND,
    "send_by_reference": SEND,
    "upload_prepared": SEND,
    "create_topic": SEND,
    "iter_messages": HISTORY,
    "get_messages": HISTORY,
    "count": HISTORY,
    "list_topics": HISTORY,
    "media_counters": HISTORY,
    "fetch": HISTORY,
    "prepare": FILE,
    "export_unit": FILE,
}


def action_of(method: str) -> str:
    """The limiter lane a gateway method counts against."""
    return LANES.get(method, SEND)


class Interrupted(Exception):
    """A sleep was cut short because a pause/stop was requested (raised by the runner's nap)."""


class Notifier(Protocol):
    def notice(self, code: str, **params: object) -> None: ...


@dataclass(frozen=True, slots=True)
class FloodOwner:
    """What ``FloodGuard`` paces for and logs flood events against: a run or a backup (phase 11).

    ``flood_log`` has a column for each (``run_id``/``backup_id``): the two tables number their
    rows independently, so a run's events must never be logged under a backup's id or vice versa.
    """

    account: str
    batch_size: int  # the base batch size ``Limiter.batch_size`` scales (a backup: 1, no batching)
    run_id: int | None = None
    backup_id: int | None = None
    dst: int | None = None  # where a run writes: the destination half of a write lane

    @classmethod
    def of_run(cls, run: Run) -> "FloodOwner":
        return cls(run.account, run.options.batch_size, run_id=run.id, dst=run.dst_id)

    @classmethod
    def of_backup(cls, backup: Backup) -> "FloodOwner":
        return cls(backup.account, 1, backup_id=backup.id)

    @classmethod
    def of_inspect(cls, account: str = "default") -> "FloodOwner":
        """A scan with no row of its own (phase 18a): its flood events carry neither id, but still
        feed the account's limiter state, so a later run starts from what the scan learned."""
        return cls(account, 1)


class FloodGuard:
    def __init__(
        self,
        *,
        limiter: Limiter,
        store: Store,
        owner: FloodOwner,
        limits: Limits,
        notifier: Notifier,
        nap: Sleep,
        rng: random.Random | None = None,
        wait: bool = False,
    ) -> None:
        self._limiter = limiter
        self._store = store
        self._owner = owner
        self._limits = limits
        self._notifier = notifier
        self._nap = nap
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not security
        self._wait = wait
        self._premium: bool | None = None  # not known until ``learn_context`` asks

    @property
    def _dst(self) -> int:
        return self._owner.dst or 0

    @property
    def notifier(self) -> Notifier:
        return self._notifier

    def set_premium(self, premium: bool | None) -> None:
        """Whether the account has Premium: written next to every flood from now on."""
        self._premium = premium

    def set_slow_mode(self, seconds: int) -> None:
        """The destination's slow mode: no post to it is paced faster than this."""
        if self._owner.dst is not None:
            self._limiter.set_slow_mode(self._owner.dst, seconds)

    # ---- writes -----------------------------------------------------------------------------

    async def pace(self, cost: int, credit: float = 0.0, action: str = SEND) -> None:
        """Wait until a write of ``cost`` messages may go out on lane ``action``, ``credit``
        seconds of the delay having been spent already (uploading its bytes). Raises
        ``BudgetReached``."""
        await self._limiter.acquire(cost, "write", credit, action=action, dst=self._dst)

    def check_budget(self, cost: int, action: str = SEND) -> None:
        """Raise ``BudgetReached`` now if a write of ``cost`` messages on lane ``action`` would
        pass the budget that lane learned (a lane with none is never stopped)."""
        self._limiter.check_budget(cost, action, self._dst)

    async def pace_read(self, requests: int, action: str = HISTORY) -> None:
        """Wait until ``requests`` read requests may go out (their own, lighter bucket, at the
        delay of lane ``action``)."""
        await self._limiter.acquire(requests, "read", action=action)

    def read_done(self, method: str) -> None:
        """A read call went through: its lane's delay may shrink."""
        self._limiter.on_read_success(action_of(method))

    async def write(self, method: str, cost: int, call: Callable[[], Awaitable[T]]) -> T:
        """Run ``call`` (a write of ``cost`` messages), repeating it after each FloodWait."""
        result = await self.transfer(method, call)
        self._limiter.on_success(cost, action_of(method), self._dst)
        if (sent := self._limiter.cap_warning()) is not None:  # only a reminder: it stops nothing
            self._notifier.notice("cap_warning", sent=sent, cap=self._limits.daily_cap)
        return result

    async def transfer(self, method: str, call: Callable[[], Awaitable[T]]) -> T:
        """Run ``call`` that moves bytes but creates no message (uploading a file before it is
        posted), repeating it after each FloodWait. It is neither paced nor counted: only the
        post that follows is a write."""
        floods = 0
        while True:
            try:
                return await call()
            except FloodWait as exc:
                floods += 1
                await self.flooded(method, exc, give_up=floods >= MAX_FLOODS_PER_CALL)
            except PeerFlood:
                await self.peer_flood(method)
                raise

    # ---- reads ------------------------------------------------------------------------------

    def reader(self, inner: MessageReader) -> MessageReader:
        """``inner`` with every read paced and retried after a FloodWait."""
        return _GuardedReader(self, inner)

    # ---- flood ------------------------------------------------------------------------------

    async def flooded(self, method: str, exc: FloodWait, *, give_up: bool = False) -> None:
        """Handle one FloodWait: log it, back off, then sleep it out (or re-raise it).

        Returning means the wait is over and the call may be repeated.
        """
        kind = (
            "transport_429"
            if exc.transport
            else "slow_mode"
            if exc.slow_mode
            else "premium_wait"
            if exc.premium
            else "flood_wait"
        )
        await self._log(method, kind, exc.seconds)
        throttled = self._limiter.throttle
        action = action_of(method)
        # slow mode, transport 429 and Premium waits are bursts and rules of their own, never the
        # lane's quota: only a plain FloodWait longer than ``short_flood`` may set a budget
        quota = not (exc.slow_mode or exc.transport or exc.premium)
        budget = self._limiter.on_flood(action, self._dst, seconds=exc.seconds, quota=quota)
        if budget is not None:
            self._notifier.notice(
                "budget_learned",
                action=action,
                budget=budget.amount,
                window=budget.window_hours,
                seconds=exc.seconds,
                bad=budget.bad if budget.bad is not None else "-",
            )
        await self._store.save_limiter_state(self._owner.account, self._limiter.state)
        if self._limiter.throttle > throttled:
            self._notifier.notice(
                "throttled",
                batch_size=self._limiter.batch_size(self._owner.batch_size),
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
        """One ``flood_log`` row; its delay is the one that led to the flood, before backing off.

        It carries the lane (``action``, and ``dst`` for a write) and how much that write lane had
        sent in the last hour and 24 hours: what a later phase learns a budget from."""
        action = action_of(method)
        write = action in WRITE_ACTIONS
        await self._store.log_flood(
            self._owner.run_id,
            backup_id=self._owner.backup_id,
            kind=kind,
            seconds=seconds,
            method=method,
            delay_ms=int(self._limiter.delay_of(action, self._dst) * 1000),
            batch_size=self._limiter.batch_size(self._owner.batch_size),
            action=action,
            dst=self._owner.dst if write else None,
            sent_1h=self._limiter.sent_in(action, self._dst, 1) if write else None,
            sent_24h=self._limiter.sent_in(action, self._dst, 24) if write else None,
            premium=self._premium,
        )


async def learn_context(
    guard: FloodGuard,
    gateway: TelegramGateway,
    *,
    dst: int | None = None,
    dst_kind: ChatKind | None = None,
) -> None:
    """The two facts known before the first write, read once as part of the run's setup (like
    ``last_message_id``; docs/05-chong-flood.md): whether the account has Premium, and the
    destination's slow mode, which becomes the floor of the pace of every post to it.

    Neither is needed to run: a failed read (a FloodWait included — the next real call meets the
    same wait and handles it properly) leaves the fact unknown."""
    try:
        guard.set_premium(await gateway.is_premium())
    except GatewayError:
        guard.set_premium(None)
    if dst is None or dst_kind not in (ChatKind.SUPERGROUP, ChatKind.FORUM):
        return  # a channel or a basic group has no slow mode
    try:
        seconds = await gateway.slowmode_seconds(dst)
    except GatewayError:
        return
    if seconds > 0:
        guard.set_slow_mode(seconds)
        guard.notifier.notice("slow_mode", seconds=seconds)


async def inspect_guard(
    store: Store, limits: Limits, *, notifier: Notifier, nap: Sleep
) -> FloodGuard:
    """The guard a read-only look at a source reads through (``tgmirror inspect``, the "Khám
    phá" screen): paced like a run's reads, its floods feeding the account's limiter state."""
    owner = FloodOwner.of_inspect()
    limiter = Limiter(limits, nap, state=await store.load_limiter_state(owner.account))
    return FloodGuard(
        limiter=limiter, store=store, owner=owner, limits=limits, notifier=notifier, nap=nap
    )


class _GuardedReader:
    def __init__(self, guard: FloodGuard, inner: MessageReader) -> None:
        self._guard = guard
        self._inner = inner

    async def _call(
        self,
        method: str,
        call: Callable[[], Awaitable[T]],
        *,
        requests: int = 1,
        paced: bool = True,
    ) -> T:
        """One read call: paced on its lane, repeated after a FloodWait (up to
        ``MAX_FLOODS_PER_CALL``), never after a PeerFlood."""
        guard, floods = self._guard, 0
        while True:
            if paced:
                await guard.pace_read(requests, action_of(method))
            try:
                result = await call()
            except FloodWait as exc:
                floods += 1
                await guard.flooded(method, exc, give_up=floods >= MAX_FLOODS_PER_CALL)
            except PeerFlood:
                await guard.peer_flood(method)
                raise
            else:
                guard.read_done(method)
                return result

    async def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        # a date bound costs the gateway one more request to turn the date into an id
        requests = 1 + (filters.since is not None) + (filters.until is not None)
        position, floods = min_id, 0
        while True:
            await self._guard.pace_read(requests, HISTORY)
            try:
                async with aclosing(
                    self._inner.iter_messages(src, min_id=position, filters=filters)
                ) as stream:
                    async for message in stream:
                        position, floods = message.id, 0  # progress: the next read starts after it
                        yield message
                self._guard.read_done("iter_messages")
                return
            except FloodWait as exc:
                floods += 1
                give_up = floods >= MAX_FLOODS_PER_CALL
                await self._guard.flooded("iter_messages", exc, give_up=give_up)
            except PeerFlood:
                await self._guard.peer_flood("iter_messages")
                raise

    async def count(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int:
        """The analysis at the start of a run: one request (a few when a date has to become an id)
        that is *not* paced. Like the setup reads before the run, it is a single call the person
        started; the read bucket begins with the first page of messages. A FloodWait is still
        logged and sat out (or parks the run)."""
        return await self._call(
            "count",
            lambda: self._inner.count(src, min_id=min_id, filters=filters),
            paced=False,
        )

    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]:
        return await self._call("get_messages", lambda: self._inner.get_messages(src, ids))

    async def list_topics(self, src: int) -> list[TopicInfo]:
        """One paced read request (phase 8)."""
        return await self._call("list_topics", lambda: self._inner.list_topics(src))

    async def media_counters(self, src: int, topic_id: int | None = None) -> MediaCounters:
        """One paced read request (``inspect --quick``)."""
        return await self._call("media_counters", lambda: self._inner.media_counters(src, topic_id))

    async def fetch(self, src: int, unit: Unit) -> Prepared:
        """One paced read request (the messages again, for sending by reference)."""
        return await self._call("fetch", lambda: self._inner.fetch(src, unit))

    async def prepare(
        self, src: int, unit: Unit, tmp: Path, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        """One paced read request (strategy B: the messages again, then their downloads). After a
        FloodWait the call is repeated; the gateway keeps what it already downloaded."""
        return await self._call("prepare", lambda: self._inner.prepare(src, unit, tmp, on_transfer))

    async def export_unit(
        self, src: int, unit: Unit, media_dir: Path, on_transfer: OnTransfer | None = None
    ) -> list[ExportedMessage]:
        """One paced read request (phase 11, backup: as ``prepare``, but the records kept for
        ``messages.jsonl`` instead of a ``Prepared``)."""
        return await self._call(
            "export_unit", lambda: self._inner.export_unit(src, unit, media_dir, on_transfer)
        )
