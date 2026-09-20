"""The job runner: claim, reconcile, then batch by batch until done, paused or stopped.

Follows docs/01-kien-truc.md ("Vòng lặp runner") and docs/04-state-checkpoint.md. Every batch is
written ``pending`` before Telegram is called and settled in one transaction afterwards, so a kill
at any point is repaired by ``_reconcile`` on the next run.

Rate limits (docs/05-chong-flood.md) are ``engine/flood.py``'s business: it paces every read and
write and sits out a FloodWait of up to ``max_auto_wait`` by repeating the same call. What reaches
this module is what cannot be waited out. A FloodWait that is too long, or repeated too often, is
forgotten by the batch that hit it (Telegram created nothing), the job becomes ``waiting_flood``
with ``resume_at`` and the error is re-raised (exit code 3); the daily cap does the same with
``DailyCapReached``. PeerFlood fails the job and is never retried.
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
from tgmirror.engine.jobs import DAILY_CAP
from tgmirror.engine.reconcile import Outcome, judge
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import plan_read
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.jobs import Control, Job, JobStatus
from tgmirror.store.msgmap import MessageResult

_CONTROL_STATUS = {Control.PAUSE: JobStatus.PAUSED, Control.STOP: JobStatus.STOPPED}


class Reporter(Protocol):
    """Where the runner tells the outside world what it is doing (the CLI prints it)."""

    def notice(self, code: str, **params: object) -> None:
        """Something worth a line: ``reconciled``, ``reconcile_resend``, ``reconcile_ambiguous``,
        ``flood_waiting``, ``flood_stopped``, ``throttled``. Codes map to ``run.<code>`` in
        ``ui/messages.py``."""
        ...

    def progress(self, job: Job) -> None:
        """A batch was committed; ``job`` carries the new cursor and counters."""
        ...


class NullReporter:
    def notice(self, code: str, **params: object) -> None:
        pass

    def progress(self, job: Job) -> None:
        pass


class StopSignal:
    """Set by Ctrl+C: finish the current batch, save, leave. Thread-safe, for a signal handler."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True, slots=True)
class RunnerTiming:
    poll_interval: float = 2.0  # how often a sleeping runner looks for pause/stop
    heartbeat_interval: float = 30.0  # must stay well below the store's 2-minute timeout


class Runner:
    def __init__(
        self,
        store: Store,
        gateway: TelegramGateway,
        limits: Limits,
        *,
        reporter: Reporter | None = None,
        stop: StopSignal | None = None,
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
        self._stop = stop or StopSignal()
        self._sleep = sleep
        self._rng = rng
        self._clock = clock
        self._timing = timing or RunnerTiming()
        self._wait = wait  # sit out FloodWaits of any length instead of parking the job
        self._limiter: Limiter | None = None
        self._guard: FloodGuard | None = None
        self._reader: MessageReader | None = None

    async def run(self, job_id: int, *, force_takeover: bool = False) -> Job:
        """Run the job to a resting state and return it. Errors are saved, then re-raised."""
        job = await self._store.claim(job_id, force=force_takeover)

        async def nap(seconds: float) -> None:
            await self._nap(seconds, job.id)

        self._limiter = Limiter(
            self._limits,
            nap,
            state=await self._store.load_limiter_state(job.account),
            clock=self._clock,
            rng=self._rng,
        )
        self._guard = FloodGuard(
            limiter=self._limiter,
            store=self._store,
            job=job,
            limits=self._limits,
            notifier=self._reporter,
            nap=nap,
            rng=self._rng,
            wait=self._wait,
        )
        self._reader = self._guard.reader(self._gateway)
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            await self._reconcile(job)
            status = await self._loop(job)
            await self._store.finish(job.id, status)
        except Interrupted:  # pause/stop/Ctrl+C arrived while the runner slept
            ctl = await self._requested(job.id) or Control.STOP
            await self._store.finish(job.id, _CONTROL_STATUS[ctl])
        except FloodWait as exc:
            await self._stop_on_flood(job, exc)
            raise
        except DailyCapReached as exc:  # our own budget: a rest, not a failure
            await self._store.finish(
                job.id, JobStatus.WAITING_FLOOD, fail_reason=DAILY_CAP, resume_at=exc.resume_at
            )
            raise
        except PeerFlood:  # already logged by the guard
            await self._store.finish(job.id, JobStatus.FAILED, fail_reason="peer_flood")
            raise
        except TgMirrorError as exc:
            # Transient leaves the batch pending on purpose: its outcome is unknown, so the next
            # run reconciles it. Any other error left nothing pending (see ``_send``).
            reason = "transient" if isinstance(exc, Transient) else f"{type(exc).__name__}: {exc}"
            await self._store.finish(job.id, JobStatus.FAILED, fail_reason=reason[:200])
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        final = await self._store.get_job(job.id)
        assert final is not None
        return final

    # ---- the loop -------------------------------------------------------------------------

    async def _loop(self, job: Job) -> JobStatus:
        assert self._limiter is not None and self._guard is not None and self._reader is not None
        limiter = self._limiter
        spec = FilterSpec.from_json(job.filters_json)
        plan = plan_read(spec, job.cursor_src_id, pushdown=job.options.pushdown)
        read = planner.units(
            self._reader,
            job.src_id,
            min_id=plan.min_id,
            filters=plan.server,
            matcher=None if spec.is_empty else Matcher(spec),
            complete_albums=plan.complete_albums,
        )
        async with (
            aclosing(read) as units,
            aclosing(batches(units, lambda: limiter.batch_size(job.options.batch_size))) as stream,
        ):
            async for batch in stream:
                if (ctl := await self._requested(job.id)) is not None:
                    return _CONTROL_STATUS[ctl]
                todo = await self._without_done(job.id, batch)
                if not todo.units:  # nothing to send: only the cursor and the filter count move
                    await self._advance(job, todo)
                    continue
                await self._guard.pace(todo.size)
                if (ctl := await self._requested(job.id)) is not None:  # arrived while sleeping
                    return _CONTROL_STATUS[ctl]
                await self._send(job, todo)
        return JobStatus.DONE

    async def _without_done(self, job_id: int, batch: Batch) -> Batch:
        """Drop units that already have a ``done`` row (resume, step 4); may leave it empty.

        The cursor still passes the dropped units: ``last_id`` of the result is that of ``batch``.
        """
        done = await self._store.done_ids(job_id, batch.ids)
        if not done:
            return batch
        todo = tuple(u for u in batch.units if not any(i in done for i in u.ids))
        return replace(batch, units=todo, upto=batch.last_id)

    async def _advance(self, job: Job, batch: Batch) -> None:
        stats = {"skipped_filter": batch.skipped} if batch.skipped else None
        updated = await self._store.advance_cursor(job.id, batch.last_id, extra_stats=stats)
        if batch.skipped:  # a long stretch without matches still shows a sign of life
            self._reporter.progress(updated)

    async def _send(self, job: Job, batch: Batch) -> None:
        assert self._guard is not None and self._limiter is not None
        batch_id = await self._store.begin_batch(job.id, batch.units)  # write-ahead
        try:
            # A FloodWait is sat out inside ``write`` and the same call repeated: the batch stays
            # ``pending`` meanwhile and nothing is rebuilt.
            results = await self._guard.write(
                "copy_messages",
                batch.size,
                lambda: copy_batch(self._gateway, job.src_id, job.dst_id, batch),
            )
        except PerMessage as exc:
            if len(batch.units) > 1:
                # Telegram refused the ids and created nothing. One bad message must not fail its
                # neighbours: forget the batch and go again unit by unit.
                await self._store.discard_batch(job.id, batch_id)
                await self._send_units_apart(job, batch)
                return
            results = [MessageResult(i, None, exc.reason) for i in batch.ids]
        except Transient:
            raise  # outcome unknown: the rows stay pending, the next run reconciles them
        except (GatewayError, Interrupted):
            # Rejected, or paused while waiting out a flood: nothing was created either way.
            await self._store.discard_batch(job.id, batch_id)
            raise
        extra = {"skipped_filter": batch.skipped} if batch.skipped else None
        updated = await self._store.commit_batch(
            job.id,
            batch_id,
            results,
            batch.last_id,
            extra_stats=extra,
            limiter=self._limiter.state,
        )
        self._reporter.progress(updated)

    async def _send_units_apart(self, job: Job, batch: Batch) -> None:
        assert self._guard is not None
        last = len(batch.units) - 1
        for i, unit in enumerate(batch.units):
            if await self._requested(job.id) is not None:
                return  # the units not sent yet are read again on the next run
            await self._guard.pace(len(unit.messages))
            # the filter count and the cursor past the skipped messages go with the last unit
            await self._send(job, replace(batch, units=(unit,)) if i == last else Batch((unit,)))

    # ---- flood ----------------------------------------------------------------------------

    async def _stop_on_flood(self, job: Job, exc: FloodWait) -> None:
        """A FloodWait the guard would not sit out (too long or too often); it is logged already."""
        resume_at = self._clock() + timedelta(seconds=exc.seconds)
        await self._store.finish(job.id, JobStatus.WAITING_FLOOD, resume_at=resume_at)
        self._reporter.notice("flood_stopped", seconds=exc.seconds, resume_at=resume_at)

    # ---- reconcile (docs/04-state-checkpoint.md, "Resume" step 1) --------------------------

    async def _reconcile(self, job: Job) -> None:
        pending = await self._store.pending_rows(job.id)
        if not pending:
            return
        ids = [p.src_msg_id for p in pending]  # ascending
        source = await self._read(job.src_id, after=ids[0] - 1, until=ids[-1], only=set(ids))
        base = max(await self._store.last_done_dst_id(job.id), job.options.dst_base_id)
        tail = await self._read(job.dst_id, after=base)

        if len(source) != len(ids):  # a pending message vanished from the source: cannot compare
            outcome, dst_ids = (Outcome.AMBIGUOUS if tail else Outcome.RESEND), []
        else:
            outcome, dst_ids = judge(source, tail)

        if outcome is Outcome.CONFIRMED:
            await self._store.confirm_pending(job.id, dict(zip(ids, dst_ids, strict=True)))
            self._reporter.notice("reconciled", count=len(ids))
            return
        await self._store.discard_pending(job.id)  # the batch is sent again by the loop
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

    async def _requested(self, job_id: int) -> Control | None:
        """``PAUSE``/``STOP`` when asked (Ctrl+C or ``tgmirror pause|stop``), else ``None``."""
        if self._stop.requested:
            return Control.STOP
        control = await self._store.read_control(job_id)
        return None if control is Control.NONE else control

    async def _nap(self, seconds: float, job_id: int) -> None:
        """Sleep in short slices so a pause/stop is noticed within ``poll_interval``.

        Raises ``Interrupted`` when one arrives: whatever was waiting to go out must not.
        """
        remaining = seconds
        while remaining > 0:
            step = min(remaining, self._timing.poll_interval)
            await self._sleep(step)
            remaining -= step
            if await self._requested(job_id) is not None:
                raise Interrupted

    async def _heartbeat(self, job_id: int) -> None:
        while True:
            await asyncio.sleep(self._timing.heartbeat_interval)
            await self._store.heartbeat(job_id)
