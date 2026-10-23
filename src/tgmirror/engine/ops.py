"""Operations on a chat's own messages: ``tgmirror delete`` (docs/07-inspect-transform.md section 6,
docs/06-lo-trinh.md Phase 20, decision D10).

Three steps, each of which stands alone:

1. ``plan_delete`` reads the chat with the filter and **freezes** the ids it would remove into the
   store (``ops`` + ``op_items``, status ``planned``). Nothing on Telegram changes. The CLI shows
   the summary and asks for the confirmation (typing the number back, or ``--yes --expect N``).
2. ``begin_op`` takes a confirmed plan up (``running``).
3. ``OpRunner.run`` applies it batch by batch. For each batch it **reads the messages again by
   id** and checks the filter against what is there now (a message edited or removed since the
   plan is left alone), writes the batch ``pending`` ahead of the call, deletes, and marks it
   ``done`` — one transaction each (rule 3). After a crash the ``pending`` rows are settled first:
   an id that is no longer in the chat was deleted, one that is still there goes back to
   ``planned``. So running it again never leaves anything out and never touches anything outside
   the plan.

An album is never split (rule 4): a batch is cut between units. Every Telegram call goes through
``FloodGuard`` (rule 1): the plan's scan like ``inspect``'s, the apply with the ``delete`` write
lane of the chat.
"""

import asyncio
import contextlib
import random
from collections import deque
from collections.abc import Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from tgmirror.core.config import Limits
from tgmirror.core.errors import (
    BudgetReached,
    FloodWait,
    NoPermission,
    PeerFlood,
    PerMessage,
    TgMirrorError,
    Transient,
)
from tgmirror.core.gateway import (
    MAX_IDS_PER_CALL,
    ChannelInfo,
    MessageReader,
    SrcMessage,
    TelegramGateway,
    Unit,
)
from tgmirror.core.limiter import DELETE, Limiter, Sleep
from tgmirror.engine import planner
from tgmirror.engine.flood import FloodGuard, FloodOwner, Interrupted, inspect_guard, learn_context
from tgmirror.engine.planner import Skip
from tgmirror.engine.runner import RunControl
from tgmirror.engine.runs import DAILY_CAP, RunError
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import plan_read
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.ops import (
    ItemStatus,
    Op,
    OpItem,
    OpKind,
    OpSpec,
    OpStatus,
    PlannedItem,
)
from tgmirror.store.runs import Control

UNITS_PER_READ = MAX_IDS_PER_CALL  # units taken from the plan at a time: enough to fill a batch
SAMPLE_EDGE = 5  # messages shown from each end of the plan
SAMPLE_TEXT = 60  # characters of a sampled message

SKIP_GONE = "gone"  # the message was no longer in the chat when its turn came
SKIP_CHANGED = "changed"  # it was there, but the filter no longer matches it


class OpError(RunError):
    """Base class for problems with the operation the user named or is starting (exit code 2)."""


class OpNotFound(OpError):
    def __init__(self, ref: str | None) -> None:
        super().__init__("there is no operation yet" if ref is None else f"no operation: {ref!r}")
        self.ref = ref  # ``None``: there is none at all


class NotAdmin(NoPermission):
    """The account does not administer the chat, so it may not remove messages from it."""

    def __init__(self, chat: ChannelInfo) -> None:
        super().__init__(f"not an administrator of {chat.title!r}")
        self.chat = chat


@dataclass(frozen=True, slots=True)
class Sample:
    id: int
    date: datetime
    media: str
    text: str


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """What the plan holds, for the person to judge before confirming."""

    messages: int
    units: int
    albums: int
    by_media: dict[str, int]
    by_month: dict[str, int]
    first: tuple[Sample, ...]
    last: tuple[Sample, ...]  # the other end; empty when the plan is short enough to show whole


@dataclass(frozen=True, slots=True)
class DeletePlan:
    """The frozen plan: ``op`` is ``None`` when the filter selected nothing (no row is made)."""

    op: Op | None
    summary: PlanSummary


class Reporter(Protocol):
    def notice(self, code: str, **params: object) -> None: ...
    def progress(self, op: Op) -> None: ...


class NullReporter:
    def notice(self, code: str, **params: object) -> None:
        pass

    def progress(self, op: Op) -> None:
        pass


# ---- planning ------------------------------------------------------------------------------------


def _sample(message: SrcMessage) -> Sample:
    text = " ".join(message.text.split())
    if len(text) > SAMPLE_TEXT:
        text = text[: SAMPLE_TEXT - 1] + "…"
    return Sample(message.id, message.date, message.media.value, text)


async def plan_delete(
    store: Store,
    gateway: TelegramGateway,
    chat: ChannelInfo,
    spec: FilterSpec,
    limits: Limits,
    *,
    pushdown: bool = True,
    notifier: Reporter | None = None,
    nap: Sleep = asyncio.sleep,
) -> DeletePlan:
    """Read ``chat`` with ``spec`` and freeze what it selects as a ``planned`` delete.

    Re-reads the chat first (one setup request, hard rule 1's exception) because it is the rights
    that count now that decide, not those of the list it was chosen from. The scan itself is paced
    and retried like ``inspect``'s. A filter that selects nothing makes no plan."""
    current = await gateway.get_channel(chat.id)
    if not current.is_admin:
        raise NotAdmin(current)
    guard = await inspect_guard(store, limits, notifier=notifier or NullReporter(), nap=nap)
    reader = guard.reader(gateway)
    read = plan_read(spec, 0, pushdown=pushdown)
    matcher = None if spec.is_empty else Matcher(spec)

    items: list[PlannedItem] = []
    by_media: dict[str, int] = {}
    by_month: dict[str, int] = {}
    first: list[Sample] = []
    last: deque[Sample] = deque(maxlen=SAMPLE_EDGE)
    units = albums = 0
    async with aclosing(
        planner.units(
            reader,
            current.id,
            min_id=read.min_id,
            filters=read.server,
            matcher=matcher,
            complete_albums=read.complete_albums,
        )
    ) as stream:
        async for item in stream:
            if isinstance(item, Skip):  # judged and not selected (the server drops most itself)
                continue
            units += 1
            albums += item.is_album
            for m in item.messages:
                items.append(PlannedItem(m.id, item.ids[0], m.grouped_id))
                by_media[m.media.value] = by_media.get(m.media.value, 0) + 1
                month = m.date.strftime("%Y-%m")
                by_month[month] = by_month.get(month, 0) + 1
                if len(first) < SAMPLE_EDGE:
                    first.append(_sample(m))
                else:
                    last.append(_sample(m))
    summary = PlanSummary(
        messages=len(items),
        units=units,
        albums=albums,
        by_media=dict(sorted(by_media.items(), key=lambda kv: -kv[1])),
        by_month=dict(sorted(by_month.items())),
        first=tuple(first),
        last=tuple(last),
    )
    if not items:
        return DeletePlan(None, summary)
    op = await store.create_op(OpSpec(OpKind.DELETE, current, spec.to_json()), items)
    return DeletePlan(op, summary)


async def resolve_op(store: Store, ref: str | None) -> Op:
    """An operation by its number (``12`` or ``O12``), or the latest when ``ref`` is ``None``."""
    if ref is None:
        if (latest := await store.latest_op()) is None:
            raise OpNotFound(None)
        return latest
    number = ref.strip().removeprefix("O").removeprefix("o")
    if number.isdecimal() and (op := await store.get_op(int(number))) is not None:
        return op
    raise OpNotFound(ref)


async def begin_op(store: Store, op_id: int, *, force: bool = False) -> Op:
    """Take a confirmed (or interrupted) operation up: ``running``. ``OpBusy`` when another process
    works on the same chat."""
    return await store.start_op(op_id, force=force)


# ---- applying ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpTiming:
    poll_interval: float = 2.0
    heartbeat_interval: float = 30.0


def pack(
    units: Sequence[Sequence[OpItem]], size: int = MAX_IDS_PER_CALL
) -> list[list[Sequence[OpItem]]]:
    """Units cut into batches of at most ``size`` messages, never between the messages of a unit."""
    batches: list[list[Sequence[OpItem]]] = []
    used = 0
    for unit in units:
        if not batches or used + len(unit) > size:
            batches.append([])
            used = 0
        batches[-1].append(unit)
        used += len(unit)
    return batches


class OpRunner:
    """Carries out a plan already taken up by ``begin_op``, batch by batch."""

    def __init__(
        self,
        store: Store,
        gateway: TelegramGateway,
        limits: Limits,
        *,
        reporter: Reporter | None = None,
        control: RunControl | None = None,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        clock: Clock = utc_now,
        timing: OpTiming | None = None,
        wait: bool = False,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._limits = limits
        self._reporter: Reporter = reporter or NullReporter()
        self._control = control or RunControl()
        self._sleep = sleep
        self._rng = rng
        self._clock = clock
        self._timing = timing or OpTiming()
        self._wait = wait

    async def run(self, op: Op) -> Op:
        limiter = Limiter(
            self._limits,
            lambda seconds: self._nap(seconds, op.id),
            state=await self._store.load_limiter_state(op.account),
            clock=self._clock,
            rng=self._rng,
        )
        guard = FloodGuard(
            limiter=limiter,
            store=self._store,
            owner=FloodOwner.of_op(op),
            limits=self._limits,
            notifier=self._reporter,
            nap=lambda seconds: self._nap(seconds, op.id),
            rng=self._rng,
            wait=self._wait,
        )
        reader = guard.reader(self._gateway)
        heartbeat = asyncio.create_task(self._heartbeat(op.id))
        try:
            await learn_context(guard, self._gateway)  # Premium, for the flood log
            spec = FilterSpec.from_json(op.filters_json)
            matcher = None if spec.is_empty else Matcher(spec)
            op = await self._settle_pending(op, reader)
            status = OpStatus.DONE
            while status is OpStatus.DONE:
                units = await self._store.op_units(op.id, ItemStatus.PLANNED, UNITS_PER_READ)
                if not units:
                    break
                for batch in pack(units):
                    if not await self._gate(op):
                        status = OpStatus.STOPPED
                        break
                    op = await self._delete_batch(op, guard, reader, matcher, batch)
                    # what the lane has sent is kept with the batch, so a later run (and a budget
                    # learned from a long flood) counts the messages this one deleted
                    await self._store.save_limiter_state(op.account, limiter.state)
            await self._store.finish_op(op.id, status)
        except Interrupted:
            await self._store.finish_op(op.id, OpStatus.STOPPED)
        except FloodWait as exc:
            resume_at = self._clock() + timedelta(seconds=exc.seconds)
            await self._store.finish_op(op.id, OpStatus.WAITING_FLOOD, resume_at=resume_at)
            self._reporter.notice("flood_stopped", seconds=exc.seconds, resume_at=resume_at)
            raise
        except BudgetReached as exc:  # our own budget: a rest, not a failure
            await self._store.finish_op(
                op.id, OpStatus.WAITING_FLOOD, fail_reason=DAILY_CAP, resume_at=exc.resume_at
            )
            raise
        except PeerFlood:
            await self._store.finish_op(op.id, OpStatus.FAILED, fail_reason="peer_flood")
            raise
        except TgMirrorError as exc:
            # Transient leaves its batch ``pending`` on purpose (the call may have gone through);
            # the next run settles it by reading the chat. Any other error left nothing pending.
            reason = "transient" if isinstance(exc, Transient) else f"{type(exc).__name__}: {exc}"
            await self._store.finish_op(op.id, OpStatus.FAILED, fail_reason=reason[:200])
            raise
        except BaseException as exc:
            # as ``Runner.run`` (N4, Phase 15b): whatever it was, close the row instead of leaving
            # it ``running`` with a fresh heartbeat for a process that is gone
            interrupted = isinstance(exc, KeyboardInterrupt | asyncio.CancelledError)
            reason = "interrupted" if interrupted else f"crashed: {type(exc).__name__}: {exc}"
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(
                    self._store.finish_op(op.id, OpStatus.FAILED, fail_reason=reason[:200])
                )
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        final = await self._store.get_op(op.id)
        assert final is not None
        return final

    async def _settle_pending(self, op: Op, reader: MessageReader) -> Op:
        """What a process that died mid-batch left ``pending``: read the chat. An id that is gone
        was deleted (``done``); one that is still there was not (back to ``planned``, and the
        batch that finds it will read it again like any other)."""
        pending = await self._store.op_items_in(op.id, ItemStatus.PENDING)
        if not pending:
            return op
        ids = [i.msg_id for i in pending]
        present: set[int] = set()
        for start in range(0, len(ids), MAX_IDS_PER_CALL):
            found = await reader.get_messages(op.chat_id, ids[start : start + MAX_IDS_PER_CALL])
            present.update(m.id for m in found)
        deleted = [i for i in ids if i not in present]
        again = [i for i in ids if i in present]
        if deleted:
            op = await self._store.mark_op_items(
                op.id, deleted, ItemStatus.DONE, stats={"done": len(deleted)}
            )
        if again:
            op = await self._store.mark_op_items(op.id, again, ItemStatus.PLANNED)
        self._reporter.notice("settled", deleted=len(deleted), again=len(again))
        return op

    async def _delete_batch(
        self,
        op: Op,
        guard: FloodGuard,
        reader: MessageReader,
        matcher: Matcher | None,
        batch: Sequence[Sequence[OpItem]],
    ) -> Op:
        ids = [item.msg_id for unit in batch for item in unit]
        live = {m.id: m for m in await reader.get_messages(op.chat_id, ids)}
        keep: list[int] = []
        changed: list[int] = []
        gone: list[int] = []
        for unit in batch:
            members = [live[i.msg_id] for i in unit if i.msg_id in live]
            gone.extend(i.msg_id for i in unit if i.msg_id not in live)
            if not members:
                continue
            if self._still_selected(members, matcher):
                keep.extend(m.id for m in members)
            else:
                changed.extend(m.id for m in members)
        if gone:
            op = await self._store.mark_op_items(
                op.id, gone, ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": len(gone)}
            )
        if changed:
            op = await self._store.mark_op_items(
                op.id,
                changed,
                ItemStatus.SKIPPED,
                reason=SKIP_CHANGED,
                stats={"skipped": len(changed)},
            )
        if keep:
            op = await self._delete(op, guard, keep)
        self._reporter.progress(op)
        return op

    @staticmethod
    def _still_selected(members: list[SrcMessage], matcher: Matcher | None) -> bool:
        """The filter applied again to what the chat holds now. A unit that is no longer one album
        (an edit cannot do that, but Telegram has surprised before) is left alone."""
        if matcher is None:
            return True
        if len(members) > 1 and (
            members[0].grouped_id is None or len({m.grouped_id for m in members}) != 1
        ):
            return False
        return matcher.matches(Unit(tuple(members)))

    async def _delete(self, op: Op, guard: FloodGuard, ids: list[int]) -> Op:
        await guard.pace(
            len(ids), action=DELETE
        )  # may rest, or raise BudgetReached: nothing marked
        op = await self._store.mark_op_items(op.id, ids, ItemStatus.PENDING)  # write-ahead
        try:
            deleted = await guard.write(
                "delete_messages", len(ids), lambda: self._gateway.delete_messages(op.chat_id, ids)
            )
        except PerMessage:  # every id is invalid: they are gone already
            return await self._store.mark_op_items(
                op.id, ids, ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": len(ids)}
            )
        except (FloodWait, PeerFlood, Interrupted, NoPermission):
            # Telegram refused, or we gave up before asking again: nothing was deleted
            await self._store.mark_op_items(op.id, ids, ItemStatus.PLANNED)
            raise
        # ``deleted`` can be less than asked when a message vanished between the read and the call
        deleted = min(deleted, len(ids))
        return await self._store.mark_op_items(
            op.id, ids, ItemStatus.DONE, stats={"done": deleted, "gone": len(ids) - deleted}
        )

    # ---- control (mirrors engine.backup.BackupWriter) ----------------------------------------

    async def _requested(self, op_id: int) -> Control | None:
        if self._control.stop_requested:
            return Control.STOP
        if self._control.take_resume():
            await self._store.set_op_control(op_id, Control.NONE)
        control = await self._store.read_op_control(op_id)
        if control is Control.STOP:
            return Control.STOP
        if self._control.pause_requested or control is Control.PAUSE:
            return Control.PAUSE
        return None

    async def _gate(self, op: Op) -> bool:
        ctl = await self._requested(op.id)
        if ctl is None:
            return True
        if ctl is Control.STOP:
            return False
        return await self._hold(op)

    async def _hold(self, op: Op) -> bool:
        await self._store.set_op_status(op.id, OpStatus.PAUSED)
        self._reporter.notice("paused")
        while True:
            await self._sleep(self._timing.poll_interval)
            ctl = await self._requested(op.id)
            if ctl is Control.STOP:
                return False
            if ctl is None:
                await self._store.set_op_status(op.id, OpStatus.RUNNING)
                self._reporter.notice("resumed")
                return True

    async def _nap(self, seconds: float, op_id: int) -> None:
        remaining = seconds
        while remaining > 0:
            step = min(remaining, self._timing.poll_interval)
            await self._sleep(step)
            remaining -= step
            if await self._requested(op_id) is Control.STOP:
                raise Interrupted

    async def _heartbeat(self, op_id: int) -> None:
        while True:
            await asyncio.sleep(self._timing.heartbeat_interval)
            await self._store.heartbeat_op(op_id)
