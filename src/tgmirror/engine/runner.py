"""The runner: reconcile, then batch by batch until the source is exhausted or a stop arrives.

Follows docs/01-kien-truc.md ("Vòng lặp runner") and docs/04-state-checkpoint.md. The run itself
is started by ``engine.runs.begin_run`` (it claims the pair); this module only carries it out.
Every batch is written ``pending`` before Telegram is called and settled in one transaction
afterwards, so a kill at any point is repaired by ``_reconcile`` on the next run.

A run with ``options.retry_of`` (``tgmirror retry``) differs only in where its units come from: the
messages that run left ``failed``, read by id. Everything after that is the same loop.

Pause is *in place*: the runner finishes the batch it is on, marks the run ``paused`` and waits,
keeping the process, the terminal and the heartbeat, until it is resumed or stopped.

A run that may re-upload (``mode`` reupload, or a caption to rewrite) reads its batches through a
``Pipeline`` (``engine/reupload.py``): the unit after the one being sent is downloaded meanwhile,
into ``<tmp>/run-<id>``, which is emptied when the run ends. Copy-only runs read inline as before.

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
import shutil
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
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
from tgmirror.core.gateway import (
    CaptionMode,
    ChatKind,
    MessageReader,
    SrcMessage,
    TelegramGateway,
    Unit,
)
from tgmirror.core.limiter import Limiter, Sleep
from tgmirror.engine import planner
from tgmirror.engine.batcher import Batch, batches
from tgmirror.engine.copy import copy_batch
from tgmirror.engine.flood import FloodGuard, FloodOwner, Interrupted
from tgmirror.engine.planner import Skip
from tgmirror.engine.reconcile import Outcome, judge
from tgmirror.engine.reupload import (
    ActionKind,
    Options,
    Pipeline,
    Ready,
    Window,
    left_out,
    plan_unit,
    reserve_size,
    send_unit,
    send_unit_by_reference,
)
from tgmirror.engine.runs import DAILY_CAP
from tgmirror.engine.strategy import Strategy, may_reupload, router
from tgmirror.engine.topics import TopicResolver, TopicRoute
from tgmirror.engine.transfer import Transfer, TransferTracker
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import plan_read
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.msgmap import MessageResult
from tgmirror.store.runs import Control, Run, RunStatus


class Reporter(Protocol):
    """Where the runner tells the outside world what it is doing (the CLI prints it)."""

    def notice(self, code: str, **params: object) -> None:
        """Something worth a line: ``analyzed``, ``cap_days``, ``reconciled``, ``reconcile_resend``,
        ``reconcile_ambiguous``, ``flood_waiting``, ``flood_stopped``, ``throttled``, ``paused``,
        ``resumed``. Codes map to ``run.<code>`` in ``ui/messages.py``."""
        ...

    def progress(self, run: Run) -> None:
        """A batch was committed; ``run`` carries the new cursor and counters."""
        ...

    def transfer(self, transfer: Transfer) -> None:
        """A file being downloaded or uploaded got further (called often: throttle when showing)."""
        ...


class NullReporter:
    def notice(self, code: str, **params: object) -> None:
        pass

    def progress(self, run: Run) -> None:
        pass

    def transfer(self, transfer: Transfer) -> None:
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
        tmp_dir: Path | None = None,
        mono: Callable[[], float] = time.monotonic,
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
        self._mono = mono  # how long the bytes of a unit took to go up (credit against the pace)
        self._tmp_dir = tmp_dir or Path(tempfile.gettempdir()) / "tgmirror"  # strategy B downloads
        self._pipeline: Pipeline | None = None
        self._tracker = TransferTracker(self._reporter.transfer)
        self._limiter: Limiter | None = None
        self._guard: FloodGuard | None = None
        self._reader: MessageReader | None = None
        self._topics: TopicResolver | None = None

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
            owner=FloodOwner.of_run(run),
            limits=self._limits,
            notifier=self._reporter,
            nap=nap,
            rng=self._rng,
            wait=self._wait,
        )
        self._reader = self._guard.reader(self._gateway)
        heartbeat = asyncio.create_task(self._heartbeat(run.id))
        await self._clear_tmp()  # what a killed run left behind
        try:
            await self._reconcile(run)
            run = await self._analyze(run)
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
            await self._clear_tmp()
        final = await self._store.get_run(run.id)
        assert final is not None
        return final

    # ---- analysis -------------------------------------------------------------------------

    async def _analyze(self, run: Run) -> Run:
        """Count what the run has to look at, so progress can say "x of y" (docs/01, "Analyze").

        One paced read request, and only the *number*: sizes are learnt file by file as the run
        gets to them. It is an upper bound (see ``RunOptions.total_items``), capped by the id span
        recorded when the run began. A retry knows its total exactly and needs no analysis. Analysis
        is a courtesy: a Telegram error that is not a rate limit leaves the total unknown and the
        run carries on (``status`` then falls back to the source id).
        """
        assert self._reader is not None
        if run.options.retry_of is not None:
            return run
        spec = FilterSpec.from_json(run.filters_json)
        plan = plan_read(spec, run.cursor_from, pushdown=run.options.pushdown)
        try:
            total = await self._reader.count(run.src_id, min_id=plan.min_id, filters=plan.server)
        except (FloodWait, PeerFlood):
            raise
        except GatewayError:
            return run
        head = run.options.src_last_id
        if head > 0:
            total = min(total, max(head - plan.min_id, 0))
        run = await self._store.set_total(run.id, total)
        self._reporter.notice("analyzed", total=total)
        if (days := self._cap_days(total)) > 0:
            self._reporter.notice("cap_days", total=total, cap=self._limits.daily_cap, days=days)
        return run

    def _cap_days(self, total: int) -> int:
        """Whole days the run would have to rest for the daily cap if it had to send ``total``."""
        assert self._limiter is not None
        cap = self._limits.daily_cap
        left = max(cap - self._limiter.state.sent_today, 0)
        return max(-(-(total - left) // cap), 0)  # ceil((total - left) / cap), never negative

    # ---- the loop -------------------------------------------------------------------------

    async def _loop(self, run: Run) -> RunStatus:
        assert self._limiter is not None and self._guard is not None and self._reader is not None
        limiter = self._limiter
        read: AsyncIterator[Unit | Skip]
        if run.options.retry_of is not None:  # ``tgmirror retry``: only what failed, no filter
            read = self._failed_units(run, run.options.retry_of)
        else:
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
        caption = CaptionMode(run.options.caption)
        async with (
            aclosing(read) as units,
            aclosing(
                batches(
                    units,
                    lambda: limiter.batch_size(run.options.batch_size),
                    route=router(
                        run.mode,
                        caption,
                        by_reference=not run.options.src_protected,
                        topic_hashtag=_topic_hashtag(run),
                    ),
                )
            ) as stream,
            aclosing(self._ready(run, stream)) as ready_stream,
        ):
            async for ready in ready_stream:
                try:
                    if not await self._gate(run):
                        return RunStatus.STOPPED
                    if not await self._process(run, ready):
                        return RunStatus.STOPPED
                finally:
                    if self._pipeline is not None:
                        await self._pipeline.finish(ready)
        return RunStatus.DONE

    async def _ready(self, run: Run, stream: AsyncIterator[Batch]) -> AsyncIterator[Ready]:
        """The batches with what they need before they are sent.

        A run that can only copy reads inline. One that may re-upload reads ahead through a
        ``Pipeline``, so the next unit is downloaded while this one uploads.
        """
        self._pipeline = None
        caption = CaptionMode(run.options.caption)
        if not may_reupload(run.mode, caption, _topic_hashtag(run)):
            async for batch in stream:
                yield Ready(await self._without_done(run.id, batch))
            return
        options = Options.of(run.options)
        window = Window(self._limits.prefetch + 1, self._limits.tmp_budget_mb * 1024 * 1024)

        async def make(batch: Batch) -> Ready:
            return await self._make(run, batch, options, window)

        async def idle() -> None:
            if await self._requested(run.id) is Control.STOP:
                raise Interrupted  # the download in flight is dropped with the pipeline

        self._pipeline = Pipeline(window, make, poll_interval=self._timing.poll_interval)
        async with aclosing(self._pipeline.stream(stream, idle)) as ready_stream:
            async for ready in ready_stream:
                yield ready

    async def _make(self, run: Run, batch: Batch, options: Options, window: Window) -> Ready:
        """Settle what can be settled before the send: what is already ``done``, what to do with
        the unit, and (for a unit that will be uploaded) its download, or (for one sent by
        reference) the messages read again for fresh file references."""
        assert self._reader is not None
        todo = await self._without_done(run.id, batch)
        if not todo.units or todo.strategy is Strategy.COPY:
            return Ready(todo)
        unit = todo.units[0]
        action = plan_unit(unit, options)  # raises UnsupportedMedia
        if action.kind is not ActionKind.SEND:
            return Ready(todo, action)
        if todo.strategy is Strategy.REFERENCE:  # nothing to download: nothing on disk
            try:
                return Ready(todo, action, await self._reader.fetch(run.src_id, unit))
            except PerMessage as exc:  # gone since it was read: the unit fails
                return Ready(todo, action, rejected=exc.reason)
        size = reserve_size(unit)
        await window.reserve(size)
        try:
            prepared = await self._reader.prepare(
                run.src_id, unit, self._run_tmp(run), self._tracker.update
            )
            # what is really on disk, against what was reserved: a size Telegram got wrong (or a
            # cover picture) must not let downloads run past the budget unnoticed
            actual = await asyncio.to_thread(_bytes_on_disk, prepared.files)
            if actual > size:
                await window.grow(actual - size)
                size = actual
        except PerMessage as exc:  # gone since it was read, or nothing to send: the unit fails
            await window.release(size)
            return Ready(todo, action, rejected=exc.reason)
        except BaseException:
            await window.release(size)
            raise
        return Ready(todo, action, prepared, reserved=size)

    async def _process(self, run: Run, ready: Ready) -> bool:
        """Send (or settle) one ready batch. ``False`` when a stop arrived while it waited."""
        assert self._guard is not None
        todo = ready.batch
        if not todo.units:  # nothing to send: only the cursor and the filter count move
            await self._advance(run, todo)
            return True
        if ready.rejected is not None:  # Telegram refused the fetch: fail the unit, send nothing
            failed = [MessageResult(i, None, ready.rejected) for i in todo.ids]
            await self._settle(run, todo, failed)
            return True
        if ready.action is not None and ready.action.kind is ActionKind.DROP:
            self._reporter.notice("skipped_unsupported", id=todo.ids[0], reason=ready.action.reason)
            await self._settle(run, todo, left_out(todo.units[0], ready.action))
            return True
        credit = 0.0
        if ready.prepared is not None and todo.strategy is Strategy.REUPLOAD:
            # The bytes go up first: no message is created, so nothing is pending yet and the
            # time they take counts towards the gap before the post (docs/04, docs/05).
            self._guard.check_cap(todo.size)  # do not upload what cannot be posted today
            prepared, started = ready.prepared, self._mono()
            ready.prepared = await self._guard.transfer(
                "upload_prepared",
                lambda: self._gateway.upload_prepared(prepared, self._tracker.update),
            )
            credit = self._mono() - started
        await self._guard.pace(todo.size, credit)
        if not await self._gate(run):  # arrived while sleeping
            return False
        await self._send(run, todo, ready)
        return True

    async def _settle(self, run: Run, batch: Batch, results: list[MessageResult]) -> None:
        """Record what happened to a batch that needed no call to Telegram."""
        batch_id = await self._store.begin_batch(run.id, batch.units)
        updated = await self._store.commit_batch(
            run.id, batch_id, results, batch.last_id, extra_stats=_extra_stats(batch)
        )
        self._reporter.progress(updated)

    async def _failed_units(self, run: Run, failed_run: int) -> AsyncIterator[Unit]:
        """The messages ``failed_run`` left ``failed``, read by id (a retry, docs/04).

        The ids are taken once, up front: a message this run sends turns ``done`` or is written
        again by this run, so it drops out of ``failed_run``'s list either way. One found deleted
        at the source is set aside for good (``mark_gone``) and not sent.
        """
        assert self._reader is not None
        ids = [f.src_msg_id for f in await self._store.run_failures(failed_run)]
        async with aclosing(planner.failed_units(self._reader, run.src_id, ids)) as stream:
            async for item in stream:
                if isinstance(item, planner.Gone):
                    await self._store.mark_gone(run.id, item.ids)
                else:
                    yield item

    async def _without_done(self, run_id: int, batch: Batch) -> Batch:
        """Drop units that already have a ``done`` row (resume, step 4); may leave it empty.

        The cursor still passes the dropped units: ``last_id`` of the result is that of ``batch``.
        """
        done = await self._store.done_ids(run_id, batch.ids)
        if not done:
            return batch
        todo = tuple(u for u in batch.units if not any(i in done for i in u.ids))
        passed = sum(len(u.messages) for u in batch.units if u not in todo)
        return replace(batch, units=todo, upto=batch.last_id, already=batch.already + passed)

    async def _advance(self, run: Run, batch: Batch) -> None:
        stats = _extra_stats(batch)
        updated = await self._store.advance_cursor(run.id, batch.last_id, extra_stats=stats)
        if stats:  # a long stretch without matches still shows a sign of life
            self._reporter.progress(updated)

    async def _topic_route(self, run: Run, batch: Batch) -> TopicRoute:
        """Phase 8: where this batch's messages should land. ``batch.topic_id`` is ``None`` for a
        non-forum source and for a forum's General topic alike (Telethon carries no ``reply_to``
        for a General message, unverified on a real account, docs/06-lo-trinh.md open question
        9) — Telegram is left to default a topic-less post to General on its own."""
        if batch.topic_id is None:
            return TopicRoute()
        assert self._guard is not None and self._reader is not None
        guard = self._guard
        if self._topics is None:

            async def create(title: str) -> int:
                return await guard.write(
                    "create_topic", 1, lambda: self._gateway.create_topic(run.dst_id, title)
                )

            self._topics = TopicResolver(self._reader, self._store, run, create)
        return await self._topics.resolve(batch.topic_id)

    async def _send(self, run: Run, batch: Batch, ready: Ready | None = None) -> None:
        assert self._guard is not None and self._limiter is not None
        route = await self._topic_route(run, batch) if batch.units else TopicRoute()
        batch_id = await self._store.begin_batch(run.id, batch.units)  # write-ahead
        try:
            # A FloodWait is sat out inside ``write`` and the same call repeated: the batch stays
            # ``pending`` meanwhile and nothing is rebuilt.
            if (
                ready is not None
                and ready.action is not None
                and ready.prepared is not None
                and batch.strategy is Strategy.REFERENCE
            ):  # strategy B, by the files' ids: one unit
                options = Options.of(run.options)
                results = await self._guard.write(
                    "send_by_reference",
                    batch.size,
                    lambda: send_unit_by_reference(
                        self._gateway,
                        self._reader,
                        batch.units[0],
                        ready.prepared,
                        options,
                        src=run.src_id,
                        dst=run.dst_id,
                        tmp=self._run_tmp(run),
                        on_transfer=self._tracker.update,
                        on_fallback=lambda: self._reporter.notice(
                            "reference_fallback", id=batch.units[0].ids[0]
                        ),
                        topic=route.dst_topic_id,
                        hashtag=route.hashtag,
                    ),
                )
            elif ready is not None and ready.action is not None:  # strategy B: one unit
                action, options = ready.action, Options.of(run.options)
                method = "send_text" if action.kind is ActionKind.PLACEHOLDER else "send_prepared"
                results = await self._guard.write(
                    method,
                    batch.size,
                    lambda: send_unit(
                        self._gateway,
                        run.dst_id,
                        batch.units[0],
                        ready.prepared,
                        action,
                        options,
                        self._tracker.update,
                        topic=route.dst_topic_id,
                        hashtag=route.hashtag,
                    ),
                )
            else:
                results = await self._guard.write(
                    "copy_messages",
                    batch.size,
                    lambda: copy_batch(
                        self._gateway, run.src_id, run.dst_id, batch, topic=route.dst_topic_id
                    ),
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
        updated = await self._store.commit_batch(
            run.id,
            batch_id,
            results,
            batch.last_id,
            extra_stats=_extra_stats(batch),
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
            alone = (
                replace(batch, units=(unit,))
                if i == last
                else Batch((unit,), topic_id=unit.topic_id)
            )
            await self._send(run, alone)

    # ---- strategy B's scratch space -----------------------------------------------------------

    def _run_tmp(self, run: Run) -> Path:
        return self._tmp_dir / f"run-{run.id}"

    async def _clear_tmp(self) -> None:
        """Delete the downloads of any run (this one, or one that was killed). One process owns the
        session, so nothing else can be using them."""
        if self._tmp_dir.is_dir():
            await asyncio.to_thread(_remove_runs, self._tmp_dir)

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


def _remove_runs(root: Path) -> None:
    for entry in root.glob("run-*"):
        shutil.rmtree(entry, ignore_errors=True)


def _topic_hashtag(run: Run) -> bool:
    """Phase 8: forum units must carry their topic as a hashtag (only a unit sent again can)."""
    return run.options.topic_as_hashtag and run.dst_kind is not ChatKind.FORUM


def _extra_stats(batch: Batch) -> dict[str, int] | None:
    """What a batch adds to the run's counters besides its own messages: the ones that needed no
    work (dropped by the filter, or already in the destination)."""
    extra = {}
    if batch.skipped:
        extra["skipped_filter"] = batch.skipped
    if batch.already:
        extra["already_done"] = batch.already
    return extra or None


def _bytes_on_disk(files: tuple[Path, ...]) -> int:
    return sum(f.stat().st_size for f in files if f.exists())
