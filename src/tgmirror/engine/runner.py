"""The job runner: claim, reconcile, then batch by batch until done, paused or stopped.

Follows docs/01-kien-truc.md ("Vòng lặp runner") and docs/04-state-checkpoint.md. Every batch is
written ``pending`` before Telegram is called and settled in one transaction afterwards, so a kill
at any point is repaired by ``_reconcile`` on the next run.

Interim behaviour until the limiter of phase 4: a FloodWait is not waited out. The batch that hit
it is forgotten (Telegram created nothing), the job becomes ``waiting_flood`` with ``resume_at``,
and the error is re-raised (exit code 3). PeerFlood fails the job and is never retried.
"""

import asyncio
import contextlib
import random
import threading
from contextlib import aclosing
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from tgmirror.core.config import Limits
from tgmirror.core.errors import (
    FloodWait,
    GatewayError,
    PeerFlood,
    PerMessage,
    TgMirrorError,
    Transient,
)
from tgmirror.core.gateway import SrcMessage, TelegramGateway
from tgmirror.core.limiter import Limiter, Sleep
from tgmirror.engine import planner
from tgmirror.engine.batcher import Batch, batches
from tgmirror.engine.copy import copy_batch
from tgmirror.engine.reconcile import Outcome, judge
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.jobs import Control, Job, JobStatus
from tgmirror.store.msgmap import MessageResult

_CONTROL_STATUS = {Control.PAUSE: JobStatus.PAUSED, Control.STOP: JobStatus.STOPPED}


class Reporter(Protocol):
    """Where the runner tells the outside world what it is doing (the CLI prints it)."""

    def notice(self, code: str, **params: object) -> None:
        """Something worth a line: ``reconciled``, ``reconcile_resend``, ``reconcile_ambiguous``,
        ``flood_stopped``. Codes map to ``run.<code>`` in ``ui/messages.py``."""
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
        self._limiter: Limiter | None = None
        self._calling = "iter_messages"  # what a FloodWait is blamed on in flood_log

    async def run(self, job_id: int, *, force_takeover: bool = False) -> Job:
        """Run the job to a resting state and return it. Errors are saved, then re-raised."""
        job = await self._store.claim(job_id, force=force_takeover)
        self._limiter = Limiter(self._limits, lambda s: self._nap(s, job.id), self._rng)
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            await self._reconcile(job)
            status = await self._loop(job)
            await self._store.finish(job.id, status)
        except FloodWait as exc:
            await self._stop_on_flood(job, exc)
            raise
        except PeerFlood:
            await self._log_flood(job, "peer_flood", None)
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
        assert self._limiter is not None
        async with (
            aclosing(planner.units(self._gateway, job.src_id, min_id=job.cursor_src_id)) as units,
            aclosing(batches(units, job.options.batch_size)) as stream,
        ):
            async for batch in stream:
                if (ctl := await self._requested(job.id)) is not None:
                    return _CONTROL_STATUS[ctl]
                todo = await self._without_done(job.id, batch)
                if todo is None:
                    await self._store.advance_cursor(job.id, batch.last_id)
                    continue
                await self._limiter.acquire(todo.size)
                if (ctl := await self._requested(job.id)) is not None:  # arrived while sleeping
                    return _CONTROL_STATUS[ctl]
                await self._send(job, todo)
        return JobStatus.DONE

    async def _without_done(self, job_id: int, batch: Batch) -> Batch | None:
        """Drop units that already have a ``done`` row (resume, step 4). ``None``: nothing left."""
        done = await self._store.done_ids(job_id, batch.ids)
        if not done:
            return batch
        todo = tuple(u for u in batch.units if not any(i in done for i in u.ids))
        return Batch(todo) if todo else None

    async def _send(self, job: Job, batch: Batch) -> None:
        batch_id = await self._store.begin_batch(job.id, batch.units)  # write-ahead
        self._calling = "copy_messages"
        try:
            results = await copy_batch(self._gateway, job.src_id, job.dst_id, batch)
        except PerMessage as exc:
            if len(batch.units) > 1:
                # Telegram refused the ids and created nothing. One bad message must not fail its
                # neighbours: forget the batch and go again unit by unit.
                await self._store.discard_batch(job.id, batch_id)
                await self._send_units_apart(job, batch)
                self._calling = "iter_messages"
                return
            results = [MessageResult(i, None, exc.reason) for i in batch.ids]
        except Transient:
            raise  # outcome unknown: the rows stay pending, the next run reconciles them
        except GatewayError:
            await self._store.discard_batch(job.id, batch_id)  # rejected: nothing was created
            raise
        updated = await self._store.commit_batch(job.id, batch_id, results, batch.last_id)
        self._calling = "iter_messages"  # back to reading; an error above keeps the label
        self._reporter.progress(updated)

    async def _send_units_apart(self, job: Job, batch: Batch) -> None:
        assert self._limiter is not None
        for unit in batch.units:
            if await self._requested(job.id) is not None:
                return  # the units not sent yet are read again on the next run
            await self._limiter.acquire(len(unit.messages))
            await self._send(job, Batch((unit,)))

    # ---- flood ----------------------------------------------------------------------------

    async def _stop_on_flood(self, job: Job, exc: FloodWait) -> None:
        await self._log_flood(job, "flood_wait", exc.seconds)
        resume_at = self._clock() + timedelta(seconds=exc.seconds)
        await self._store.finish(job.id, JobStatus.WAITING_FLOOD, resume_at=resume_at)
        self._reporter.notice("flood_stopped", seconds=exc.seconds, resume_at=resume_at)

    async def _log_flood(self, job: Job, kind: str, seconds: int | None) -> None:
        await self._store.log_flood(
            job.id,
            kind=kind,
            seconds=seconds,
            method=self._calling,
            delay_ms=int(self._limits.min_delay * 1000),
            batch_size=job.options.batch_size,
        )

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
        found: list[SrcMessage] = []
        async with aclosing(self._gateway.iter_messages(chat, min_id=after)) as stream:
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
        """Sleep in short slices so a pause/stop is noticed within ``poll_interval``."""
        remaining = seconds
        while remaining > 0:
            step = min(remaining, self._timing.poll_interval)
            await self._sleep(step)
            remaining -= step
            if await self._requested(job_id) is not None:
                return

    async def _heartbeat(self, job_id: int) -> None:
        while True:
            await asyncio.sleep(self._timing.heartbeat_interval)
            await self._store.heartbeat(job_id)
