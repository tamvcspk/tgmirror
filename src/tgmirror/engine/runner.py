"""The runner: reconcile, then batch by batch until the source is exhausted or a stop arrives.

Follows docs/01-kien-truc.md ("Vòng lặp runner") and docs/04-state-checkpoint.md. The run itself
is started by ``engine.runs.begin_run`` (it claims the pair); this module only carries it out.
Every batch is written ``pending`` before Telegram is called and settled in one transaction
afterwards, so a kill at any point is repaired by ``_reconcile`` on the next run.

Pause is *in place*: the runner finishes the batch it is on, marks the run ``paused`` and waits,
keeping the process, the terminal and the heartbeat, until it is resumed or stopped.

Rate limits (docs/05-chong-flood.md) are ``engine/flood.py``'s business: it paces every read and
write and sits out a FloodWait of up to ``max_auto_wait`` by repeating the same call. What reaches
this module is what cannot be waited out. A FloodWait that is too long, or repeated too often, is
forgotten by the batch that hit it (Telegram created nothing), the run becomes ``waiting_flood``
with ``resume_at`` and the error is re-raised (exit code 3); the daily cap does the same with
``DailyCapReached``. PeerFlood fails the run and is never retried.
"""

import asyncio
import contextlib
import random
import threading
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Protocol

from tgmirror.core.config import Limits
from tgmirror.core.errors import (
    DailyCapReached,
    FloodWait,
    GatewayError,
    PeerFlood,
    PerMessage,
    TgMirrorError,
    Transient,
)
from tgmirror.core.gateway import MessageReader, SrcMessage, TelegramGateway
from tgmirror.core.limiter import Limiter, Sleep
from tgmirror.engine import planner
from tgmirror.engine.batcher import Batch, batches
from tgmirror.engine.copy import copy_batch
from tgmirror.engine.flood import FloodGuard, Interrupted
from tgmirror.engine.reconcile import Outcome, judge
from tgmirror.engine.runs import DAILY_CAP
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import plan_read
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.msgmap import MessageResult
from tgmirror.store.runs import Control, Run, RunStatus


class Reporter(Protocol):
    """Where the runner tells the outside world what it is doing (the CLI prints it)."""

    def notice(self, code: str, **params: object) -> None:
        """Something worth a line: ``reconciled``, ``reconcile_resend``, ``reconcile_ambiguous``,
        ``flood_waiting``, ``flood_stopped``, ``throttled``, ``paused``, ``resumed``. Codes map to
        ``run.<code>`` in ``ui/messages.py``."""
        ...

    def progress(self, run: Run) -> None:
        """A batch was committed; ``run`` carries the new cursor and counters."""
        ...


class NullReporter:
    def notice(self, code: str, **params: object) -> None:
        pass

    def progress(self, run: Run) -> None:
        pass


class RunControl:
    """What the person at the terminal asks of the running clone. Thread-safe, because Ctrl+C and
    the hotkeys arrive on other threads; ``tgmirror pause|stop|run`` from another terminal reach
    the runner through the store instead. The future Rich view drives the same three calls."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._resume = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def request_pause(self) -> None:
        self._resume.clear()
        self._pause.set()

    def request_resume(self) -> None:
        self._pause.clear()
        self._resume.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    @property
    def pause_requested(self) -> bool:
        return self._pause.is_set()

    def take_resume(self) -> bool:
        """``True`` once per resume request; the runner then clears a pause kept in the store."""
        was = self._resume.is_set()
        self._resume.clear()
        return was


@dataclass(frozen=True, slots=True)
class RunnerTiming:
    poll_interval: float = 2.0  # how often a sleeping or paused runner looks for pause/stop/resume
    heartbeat_interval: float = 30.0  # must stay well below the store's 2-minute timeout


class Runner:
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
        timing: RunnerTiming | None = None,
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
        self._timing = timing or RunnerTiming()
        self._wait = wait  # sit out FloodWaits of any length instead of parking the run
        self._limiter: Limiter | None = None
        self._guard: FloodGuard | None = None
        self._reader: MessageReader | None = None

    async def run(self, run: Run) -> Run:
        """Carry out ``run`` (already started by ``begin_run``) to a resting state and return it.

        Errors are saved on the run, then re-raised.
        """

        async def nap(seconds: float) -> None:
            await self._nap(seconds, run.id)

        self._limiter = Limiter(
            self._limits,
            nap,
            state=await self._store.load_limiter_state(run.account),
            clock=self._clock,
            rng=self._rng,
        )
        self._guard = FloodGuard(
            limiter=self._limiter,
            store=self._store,
            run=run,
            limits=self._limits,
            notifier=self._reporter,
            nap=nap,
            rng=self._rng,
            wait=self._wait,
        )
        self._reader = self._guard.reader(self._gateway)
        heartbeat = asyncio.create_task(self._heartbeat(run.id))
        try:
            await self._reconcile(run)
            status = await self._loop(run)
            await self._store.finish(run.id, status)
        except Interrupted:  # a stop arrived while the runner slept
            await self._store.finish(run.id, RunStatus.STOPPED)
        except FloodWait as exc:
            await self._stop_on_flood(run, exc)
            raise
        except DailyCapReached as exc:  # our own budget: a rest, not a failure
            await self._store.finish(
                run.id, RunStatus.WAITING_FLOOD, fail_reason=DAILY_CAP, resume_at=exc.resume_at
            )
            raise
        except PeerFlood:  # already logged by the guard
            await self._store.finish(run.id, RunStatus.FAILED, fail_reason="peer_flood")
            raise
        except TgMirrorError as exc:
            # Transient leaves the batch pending on purpose: its outcome is unknown, so the next
            # run reconciles it. Any other error left nothing pending (see ``_send``).
            reason = "transient" if isinstance(exc, Transient) else f"{type(exc).__name__}: {exc}"
            await self._store.finish(run.id, RunStatus.FAILED, fail_reason=reason[:200])
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        final = await self._store.get_run(run.id)
        assert final is not None
        return final

    # ---- the loop -------------------------------------------------------------------------

    async def _loop(self, run: Run) -> RunStatus:
        assert self._limiter is not None and self._guard is not None and self._reader is not None
        limiter = self._limiter
        spec = FilterSpec.from_json(run.filters_json)
        plan = plan_read(spec, run.cursor_from, pushdown=run.options.pushdown)
        read = planner.units(
            self._reader,
            run.src_id,
            min_id=plan.min_id,
            filters=plan.server,
            matcher=None if spec.is_empty else Matcher(spec),
            complete_albums=plan.complete_albums,
        )
        async with (
            aclosing(read) as units,
            aclosing(batches(units, lambda: limiter.batch_size(run.options.batch_size))) as stream,
        ):
            async for batch in stream:
                if not await self._gate(run):
                    return RunStatus.STOPPED
                todo = await self._without_done(run.id, batch)
                if not todo.units:  # nothing to send: only the cursor and the filter count move
                    await self._advance(run, todo)
                    continue
                await self._guard.pace(todo.size)
                if not await self._gate(run):  # arrived while sleeping
                    return RunStatus.STOPPED
                await self._send(run, todo)
        return RunStatus.DONE

    async def _without_done(self, run_id: int, batch: Batch) -> Batch:
        """Drop units that already have a ``done`` row (resume, step 4); may leave it empty.

        The cursor still passes the dropped units: ``last_id`` of the result is that of ``batch``.
        """
        done = await self._store.done_ids(run_id, batch.ids)
        if not done:
            return batch
        todo = tuple(u for u in batch.units if not any(i in done for i in u.ids))
        return replace(batch, units=todo, upto=batch.last_id)

    async def _advance(self, run: Run, batch: Batch) -> None:
        stats = {"skipped_filter": batch.skipped} if batch.skipped else None
        updated = await self._store.advance_cursor(run.id, batch.last_id, extra_stats=stats)
        if batch.skipped:  # a long stretch without matches still shows a sign of life
            self._reporter.progress(updated)

    async def _send(self, run: Run, batch: Batch) -> None:
        assert self._guard is not None and self._limiter is not None
        batch_id = await self._store.begin_batch(run.id, batch.units)  # write-ahead
        try:
            # A FloodWait is sat out inside ``write`` and the same call repeated: the batch stays
            # ``pending`` meanwhile and nothing is rebuilt.
            results = await self._guard.write(
                "copy_messages",
                batch.size,
                lambda: copy_batch(self._gateway, run.src_id, run.dst_id, batch),
            )
        except PerMessage as exc:
            if len(batch.units) > 1:
                # Telegram refused the ids and created nothing. One bad message must not fail its
                # neighbours: forget the batch and go again unit by unit.
                await self._store.discard_batch(run.id, batch_id)
                await self._send_units_apart(run, batch)
                return
            results = [MessageResult(i, None, exc.reason) for i in batch.ids]
        except Transient:
            raise  # outcome unknown: the rows stay pending, the next run reconciles them
        except (GatewayError, Interrupted):
            # Rejected, or stopped while waiting out a flood: nothing was created either way.
            await self._store.discard_batch(run.id, batch_id)
            raise
        extra = {"skipped_filter": batch.skipped} if batch.skipped else None
        updated = await self._store.commit_batch(
            run.id,
            batch_id,
            results,
            batch.last_id,
            extra_stats=extra,
            limiter=self._limiter.state,
        )
        self._reporter.progress(updated)

    async def _send_units_apart(self, run: Run, batch: Batch) -> None:
        assert self._guard is not None
        last = len(batch.units) - 1
        for i, unit in enumerate(batch.units):
            if not await self._gate(run):
                return  # the units not sent yet are read again on the next run
            await self._guard.pace(len(unit.messages))
            # the filter count and the cursor past the skipped messages go with the last unit
            await self._send(run, replace(batch, units=(unit,)) if i == last else Batch((unit,)))

    # ---- flood ----------------------------------------------------------------------------

    async def _stop_on_flood(self, run: Run, exc: FloodWait) -> None:
        """A FloodWait the guard would not sit out (too long or too often); it is logged already."""
        resume_at = self._clock() + timedelta(seconds=exc.seconds)
        await self._store.finish(run.id, RunStatus.WAITING_FLOOD, resume_at=resume_at)
        self._reporter.notice("flood_stopped", seconds=exc.seconds, resume_at=resume_at)

    # ---- reconcile (docs/04-state-checkpoint.md, "Resume" step 1) --------------------------

    async def _reconcile(self, run: Run) -> None:
        pending = await self._store.pending_rows(run.id)
        if not pending:
            return
        ids = [p.src_msg_id for p in pending]  # ascending
        source = await self._read(run.src_id, after=ids[0] - 1, until=ids[-1], only=set(ids))
        base = max(await self._store.last_done_dst_id(run.id), run.options.dst_base_id)
        tail = await self._read(run.dst_id, after=base)

        if len(source) != len(ids):  # a pending message vanished from the source: cannot compare
            outcome, dst_ids = (Outcome.AMBIGUOUS if tail else Outcome.RESEND), []
        else:
            outcome, dst_ids = judge(source, tail)

        if outcome is Outcome.CONFIRMED:
            await self._store.confirm_pending(run.id, dict(zip(ids, dst_ids, strict=True)))
            self._reporter.notice("reconciled", count=len(ids))
            return
        await self._store.discard_pending(run.id)  # the batch is sent again by the loop
        if outcome is Outcome.RESEND:
            self._reporter.notice("reconcile_resend", count=len(ids))
        else:
            self._reporter.notice("reconcile_ambiguous", count=len(ids))

    async def _read(
        self, chat: int, *, after: int, until: int | None = None, only: set[int] | None = None
    ) -> list[SrcMessage]:
        """Non-service messages of ``chat`` with ``after < id <= until`` (and in ``only``)."""
        assert self._reader is not None
        found: list[SrcMessage] = []
        async with aclosing(self._reader.iter_messages(chat, min_id=after)) as stream:
            async for m in stream:
                if until is not None and m.id > until:
                    break
                if m.is_service or (only is not None and m.id not in only):
                    continue
                found.append(m)
        return found

    # ---- control --------------------------------------------------------------------------

    async def _requested(self, run_id: int) -> Control | None:
        """``STOP`` or ``PAUSE`` when asked, else ``None``.

        Asked by Ctrl+C or a hotkey (``RunControl``) or by ``tgmirror pause|stop|run`` from another
        terminal (the store's control flag). A resume request also clears a pause left in the store.
        """
        if self._control.stop_requested:
            return Control.STOP
        if self._control.take_resume():
            await self._store.set_control(run_id, Control.NONE)
        control = await self._store.read_control(run_id)
        if control is Control.STOP:
            return Control.STOP
        if self._control.pause_requested or control is Control.PAUSE:
            return Control.PAUSE
        return None

    async def _gate(self, run: Run) -> bool:
        """Between batches: ``True`` to go on (after holding if paused), ``False`` to stop."""
        ctl = await self._requested(run.id)
        if ctl is None:
            return True
        if ctl is Control.STOP:
            return False
        return await self._hold(run)

    async def _hold(self, run: Run) -> bool:
        """Pause in place until resumed (``True``) or stopped (``False``). The heartbeat goes on."""
        await self._store.set_status(run.id, RunStatus.PAUSED)
        self._reporter.notice("paused")
        while True:
            await self._sleep(self._timing.poll_interval)
            ctl = await self._requested(run.id)
            if ctl is Control.STOP:
                return False
            if ctl is None:
                await self._store.set_status(run.id, RunStatus.RUNNING)
                self._reporter.notice("resumed")
                return True

    async def _nap(self, seconds: float, run_id: int) -> None:
        """Sleep in short slices so a stop is noticed within ``poll_interval``.

        Raises ``Interrupted`` when one arrives: whatever was waiting to go out must not. A pause
        asked meanwhile is honoured at the next batch boundary, not by cutting the wait short.
        """
        remaining = seconds
        while remaining > 0:
            step = min(remaining, self._timing.poll_interval)
            await self._sleep(step)
            remaining -= step
            if await self._requested(run_id) is Control.STOP:
                raise Interrupted

    async def _heartbeat(self, run_id: int) -> None:
        while True:
            await asyncio.sleep(self._timing.heartbeat_interval)
            await self._store.heartbeat(run_id)
