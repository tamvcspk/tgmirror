"""The runner end to end on ``FakeGateway`` and a real SQLite file: no network.

These are the tests phase 2 is judged by (docs/06-lo-trinh.md): kill the runner at every step and
resume without duplicates or gaps; albums are never split; pause, stop and floods leave a state
that resumes cleanly.
"""

import asyncio
import random
import sqlite3
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.config import Limits
from tgmirror.core.errors import (
    FloodWait,
    ForwardsRestricted,
    JobBusy,
    NoPermission,
    PeerFlood,
    Transient,
)
from tgmirror.core.gateway import ChannelInfo, MediaKind, Unit
from tgmirror.engine.endpoints import Endpoints
from tgmirror.engine.jobs import JobWaiting, NewJob, check_runnable, create_job
from tgmirror.engine.runner import Runner, RunnerTiming, StopSignal
from tgmirror.store.db import Store
from tgmirror.store.jobs import Control, Job, JobStatus
from tgmirror.store.msgmap import MessageResult

LIMITS = Limits(min_delay=2.0, jitter=0.0, long_pause_every=10_000, max_auto_wait=60.0)


class Crash(BaseException):
    """Stands for the process being killed: not a ``TgMirrorError``, so nothing handles it."""


class Recorder:
    def __init__(self) -> None:
        self.notices: list[tuple[str, dict[str, object]]] = []
        self.jobs: list[Job] = []

    def notice(self, code: str, **params: object) -> None:
        self.notices.append((code, params))

    def progress(self, job: Job) -> None:
        self.jobs.append(job)

    @property
    def codes(self) -> list[str]:
        return [c for c, _ in self.notices]


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.gw = FakeGateway()
        self.src: ChannelInfo = self.gw.add_channel("Source")
        self.dst: ChannelInfo = self.gw.add_channel("Copy")
        self.path = tmp_path / "state.db"
        self.delays: list[float] = []
        self.recorder = Recorder()
        self._stores: list[Store] = []

    async def store(self, **kw: Any) -> Store:
        store = await Store.open(self.path, **kw)
        self._stores.append(store)
        return store

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)

    def runner(
        self,
        store: Store,
        *,
        limits: Limits = LIMITS,
        stop: StopSignal | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        timing: RunnerTiming | None = None,
        clock: Callable[[], datetime] | None = None,
        wait: bool = False,
    ) -> Runner:
        extra: dict[str, Any] = {"clock": clock} if clock else {}
        return Runner(
            store,
            self.gw,
            limits,
            reporter=self.recorder,
            stop=stop,
            sleep=sleep or self.sleep,
            rng=random.Random(0),
            timing=timing or RunnerTiming(poll_interval=0.5, heartbeat_interval=3600),
            wait=wait,
            **extra,
        )

    def fill(self, count: int, start: int = 1) -> None:
        for i in range(start, start + count):
            self.gw.add_message(self.src.id, f"m{i}")

    async def job(self, store: Store, batch_size: int = 3) -> Job:
        endpoints = Endpoints(self.src, self.dst, created=False)
        return await create_job(store, self.gw, endpoints, NewJob(batch_size=batch_size))

    @property
    def src_texts(self) -> list[str]:
        return [m.text for m in self.gw.messages[self.src.id] if not m.is_service]

    @property
    def dst_texts(self) -> list[str]:
        return [m.text for m in self.gw.messages[self.dst.id] if not m.is_service]

    def copy_calls(self) -> list[list[int]]:
        return [list(c.args[2]) for c in self.gw.calls_to("copy_messages")]  # type: ignore[call-overload]

    def wrap_copy(
        self, hook: Callable[[list[int], int], Awaitable[None]], *, after: bool = False
    ) -> None:
        """Run ``hook(ids, call_number)`` before (or after) every ``copy_messages`` call."""
        original = self.gw.copy_messages
        counter = 0

        async def wrapped(src: int, dst: int, ids: list[int]) -> list[int | None]:
            nonlocal counter
            counter += 1
            if not after:
                await hook(ids, counter)
            result = await original(src, dst, ids)
            if after:
                await hook(ids, counter)
            return result

        self.gw.copy_messages = wrapped  # type: ignore[method-assign]

    def unwrap_copy(self) -> None:
        del self.gw.copy_messages


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


# ---- the plain run --------------------------------------------------------------------------


async def test_copies_everything_in_order_and_records_it(rig: Rig) -> None:
    rig.fill(7)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)

    final = await rig.runner(store).run(job.id)

    assert rig.dst_texts == rig.src_texts
    assert rig.copy_calls() == [[1, 2, 3], [4, 5, 6], [7]]
    assert (final.status, final.cursor_src_id, final.stats) == (
        JobStatus.DONE,
        7,
        {"done": 7, "failed": 0},
    )
    assert [j.cursor_src_id for j in rig.recorder.jobs] == [3, 6, 7]


async def test_an_empty_source_is_done_without_touching_telegram_writes(rig: Rig) -> None:
    store = await rig.store()
    job = await rig.job(store)

    final = await rig.runner(store).run(job.id)

    assert final.status is JobStatus.DONE and final.stats == {}
    assert rig.copy_calls() == [] and rig.delays == []


async def test_the_destination_ids_are_recorded_per_message(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    job = await rig.job(store, batch_size=2)
    await rig.runner(store).run(job.id)

    by_id = {m.id: m.text for m in rig.gw.messages[rig.dst.id]}
    assert await store.last_done_dst_id(job.id) == max(by_id)
    assert await store.done_ids(job.id, [1, 2, 3, 4]) == {1, 2, 3, 4}


async def test_the_cursor_only_ever_moves_forward(rig: Rig) -> None:
    rig.fill(9)
    store = await rig.store()
    job = await rig.job(store, batch_size=2)

    await rig.runner(store).run(job.id)

    cursors = [j.cursor_src_id for j in rig.recorder.jobs]
    assert cursors == sorted(cursors) and cursors[-1] == 9


async def test_the_runner_runs_a_job_left_created_or_resting_but_marks_it_running(
    rig: Rig,
) -> None:
    rig.fill(2)
    store = await rig.store()
    job = await rig.job(store)
    seen: list[JobStatus] = []

    async def peek(ids: list[int], n: int) -> None:
        current = await store.get_job(job.id)
        assert current is not None
        seen.append(current.status)

    rig.wrap_copy(peek)
    await rig.runner(store).run(job.id)

    assert seen == [JobStatus.RUNNING]


# ---- albums ---------------------------------------------------------------------------------


async def test_an_album_is_never_split_and_stays_an_album_in_the_destination(rig: Rig) -> None:
    gw = rig.gw
    gw.add_message(rig.src.id, "a")  # 1
    gw.add_message(rig.src.id, "b")  # 2
    gw.add_album(rig.src.id, [MediaKind.PHOTO] * 3, caption="album")  # 3 4 5
    gw.add_message(rig.src.id, "c")  # 6
    store = await rig.store()
    job = await rig.job(store, batch_size=3)

    await rig.runner(store).run(job.id)

    assert rig.copy_calls() == [[1, 2], [3, 4, 5], [6]]
    groups = [m.grouped_id for m in gw.messages[rig.dst.id]]
    assert groups[2] == groups[3] == groups[4] is not None
    assert groups[0] is groups[1] is groups[5] is None


async def test_an_album_is_not_split_across_a_resume(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "a")
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO] * 3)
    rig.gw.add_message(rig.src.id, "b")
    store = await rig.store()
    job = await rig.job(store, batch_size=2)
    stop = StopSignal()

    async def stop_after_first(ids: list[int], n: int) -> None:
        stop.request()

    rig.wrap_copy(stop_after_first, after=True)
    await rig.runner(store, stop=stop).run(job.id)  # stops after the batch [a]
    rig.unwrap_copy()
    await rig.runner(store).run(job.id)

    assert rig.copy_calls() == [[1], [2, 3, 4], [5]]


# ---- kill the runner at every step ----------------------------------------------------------


@pytest.mark.parametrize("crash_at", ["before_copy", "after_copy"])
@pytest.mark.parametrize("batch_number", [1, 2, 3])
async def test_kill_then_resume_leaves_no_gap_and_no_duplicate(
    rig: Rig, crash_at: str, batch_number: int
) -> None:
    rig.fill(8)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)  # batches: 3, 3, 2

    if crash_at == "before_copy":  # dies after the write-ahead rows, before Telegram is called

        async def die(ids: list[int], n: int) -> None:
            if n == batch_number:
                raise Crash

        rig.wrap_copy(die)
    else:  # dies after Telegram made the copies, before the result is committed
        original = store.commit_batch
        calls = 0

        async def die_on_commit(*args: Any, **kwargs: Any) -> Job:
            nonlocal calls
            calls += 1
            if calls == batch_number:
                raise Crash
            return await original(*args, **kwargs)

        store.commit_batch = die_on_commit  # type: ignore[method-assign]

    with pytest.raises(Crash):
        await rig.runner(store).run(job.id)
    await store.close()
    if crash_at == "before_copy":
        rig.unwrap_copy()

    resumed = await rig.store()
    assert len(await resumed.pending_rows(job.id)) > 0  # the killed batch is still pending
    final = await rig.runner(resumed).run(job.id, force_takeover=True)

    assert final.status is JobStatus.DONE
    assert rig.dst_texts == rig.src_texts  # every message exactly once, in order
    assert await resumed.pending_rows(job.id) == []
    assert final.stats["done"] == 8 and final.cursor_src_id == 8
    expected = "reconcile_resend" if crash_at == "before_copy" else "reconciled"
    assert rig.recorder.codes == [expected]


async def test_a_resume_maps_reconciled_copies_to_the_right_destination_ids(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    job = await rig.job(store, batch_size=4)

    async def die(ids: list[int], n: int) -> None:
        raise Crash

    rig.wrap_copy(die, after=True)  # the copy happens, then the runner dies
    with pytest.raises(Crash):
        await rig.runner(store).run(job.id)
    rig.unwrap_copy()
    await store.close()

    resumed = await rig.store()
    await rig.runner(resumed).run(job.id, force_takeover=True)

    assert await resumed.last_done_dst_id(job.id) == max(m.id for m in rig.gw.messages[rig.dst.id])
    assert rig.dst_texts == ["m1", "m2", "m3", "m4"]
    assert rig.copy_calls() == [[1, 2, 3, 4]]  # not sent again


async def test_an_ambiguous_reconcile_sends_again_and_says_so(rig: Rig) -> None:
    """Someone posted in the destination between the crash and the resume: favour no gap."""
    rig.fill(4)
    store = await rig.store()
    job = await rig.job(store, batch_size=2)

    async def die(ids: list[int], n: int) -> None:
        if n == 2:
            raise Crash

    rig.wrap_copy(die, after=True)
    with pytest.raises(Crash):
        await rig.runner(store).run(job.id)
    rig.unwrap_copy()
    await store.close()
    rig.gw.add_message(rig.dst.id, "intruder")

    resumed = await rig.store()
    await rig.runner(resumed).run(job.id, force_takeover=True)

    assert rig.recorder.codes == ["reconcile_ambiguous"]
    assert set(rig.src_texts) <= set(rig.dst_texts)  # no gap
    extra = Counter(rig.dst_texts) - Counter(rig.src_texts)
    assert extra == Counter({"intruder": 1, "m3": 1, "m4": 1})  # duplicates: only the crashed batch


async def test_a_pending_message_that_vanished_from_the_source_is_resent_or_ambiguous(
    rig: Rig,
) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    plan = rig.gw.messages[rig.src.id]

    await store.begin_batch(job.id, [Unit((plan[0],)), Unit((plan[1],))])
    del rig.gw.messages[rig.src.id][1]  # message 2 is deleted while the runner is down
    await store.close()

    resumed = await rig.store()
    final = await rig.runner(resumed).run(job.id, force_takeover=True)

    assert rig.recorder.codes == ["reconcile_resend"]  # nothing in the destination either
    assert rig.dst_texts == ["m1", "m3"] and final.status is JobStatus.DONE


async def test_a_transient_error_keeps_the_batch_pending_and_the_next_run_reconciles(
    rig: Rig,
) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)

    async def cut_off(ids: list[int], n: int) -> None:
        raise Transient("connection lost after the request was sent")

    rig.wrap_copy(cut_off, after=True)  # Telegram did copy, but we never heard back
    with pytest.raises(Transient):
        await rig.runner(store).run(job.id)
    rig.unwrap_copy()

    failed = await store.get_job(job.id)
    assert failed is not None and failed.fail_reason == "transient"
    assert len(await store.pending_rows(job.id)) == 3

    final = await rig.runner(store).run(job.id)  # no takeover needed: the job is not running

    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    assert rig.recorder.codes == ["reconciled"]


# ---- pause, stop ----------------------------------------------------------------------------


async def test_pause_from_another_process_stops_after_the_current_batch(rig: Rig) -> None:
    rig.fill(9)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)

    async def pause_after_first(ids: list[int], n: int) -> None:
        if n == 1:  # what `tgmirror pause` does from another process
            other = await Store.open(rig.path)
            try:
                assert await other.set_control(job.id, Control.PAUSE)
            finally:
                await other.close()

    rig.wrap_copy(pause_after_first, after=True)
    paused = await rig.runner(store).run(job.id)

    assert (paused.status, paused.control, paused.cursor_src_id) == (
        JobStatus.PAUSED,
        Control.NONE,  # the flag is consumed
        3,
    )
    assert rig.dst_texts == ["m1", "m2", "m3"]

    rig.unwrap_copy()
    final = await rig.runner(store).run(job.id)
    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts


async def test_stop_is_reported_as_stopped(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)

    async def stop_first(ids: list[int], n: int) -> None:
        await store.set_control(job.id, Control.STOP)

    rig.wrap_copy(stop_first, after=True)
    final = await rig.runner(store).run(job.id)

    assert final.status is JobStatus.STOPPED and rig.dst_texts == ["m1", "m2", "m3"]


async def test_ctrl_c_finishes_the_batch_saves_and_leaves(rig: Rig) -> None:
    rig.fill(9)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    stop = StopSignal()

    async def interrupt(ids: list[int], n: int) -> None:
        stop.request()  # the signal handler does this in the middle of a Telegram call

    rig.wrap_copy(interrupt)
    final = await rig.runner(store, stop=stop).run(job.id)

    assert final.status is JobStatus.STOPPED
    assert rig.dst_texts == ["m1", "m2", "m3"]  # the batch in flight was finished and committed
    assert final.cursor_src_id == 3 and await store.pending_rows(job.id) == []


async def test_pause_arriving_during_a_sleep_is_noticed_before_the_next_send(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)

    async def sleep_then_pause(seconds: float) -> None:
        rig.delays.append(seconds)
        await store.set_control(job.id, Control.PAUSE)

    final = await rig.runner(store, sleep=sleep_then_pause).run(job.id)

    assert final.status is JobStatus.PAUSED
    assert rig.copy_calls() == [[1, 2, 3]]  # the second batch was never sent
    assert rig.delays == [0.5]  # the sleep was cut short after one slice


# ---- errors ---------------------------------------------------------------------------------


async def test_flood_wait_saves_the_job_as_waiting_and_forgets_the_refused_batch(
    rig: Rig, tmp_path: Path
) -> None:
    rig.fill(6)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    store = await rig.store(clock=lambda: now)
    job = await rig.job(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(300))

    with pytest.raises(FloodWait):
        await rig.runner(store, clock=lambda: now).run(job.id)

    waiting = await store.get_job(job.id)
    assert waiting is not None
    assert waiting.status is JobStatus.WAITING_FLOOD
    assert waiting.resume_at == now + timedelta(seconds=300)
    assert waiting.cursor_src_id == 0 and await store.pending_rows(job.id) == []
    assert rig.recorder.codes == ["flood_stopped"]
    assert rig.dst_texts == []

    # not before the wait is over, then it goes on without a duplicate
    with pytest.raises(JobWaiting):
        check_runnable(waiting, now + timedelta(seconds=299))
    check_runnable(waiting, now + timedelta(seconds=301))
    final = await rig.runner(store).run(job.id)
    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts


async def test_flood_wait_is_logged_for_tuning(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("copy_messages", FloodWait(120))

    with pytest.raises(FloodWait):
        await rig.runner(store).run(job.id)

    con = sqlite3.connect(rig.path)
    try:
        rows = con.execute(
            "SELECT job_id, kind, seconds, method, batch_size FROM flood_log"
        ).fetchall()
    finally:
        con.close()
    assert rows == [(job.id, "flood_wait", 120, "copy_messages", 3)]


async def test_a_flood_wait_while_reading_is_blamed_on_reading(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("iter_messages", FloodWait(120))

    with pytest.raises(FloodWait):
        await rig.runner(store).run(job.id)

    con = sqlite3.connect(rig.path)
    try:
        assert con.execute("SELECT method FROM flood_log").fetchall() == [("iter_messages",)]
    finally:
        con.close()
    assert rig.copy_calls() == []


async def test_peer_flood_fails_the_job_and_is_never_retried(rig: Rig) -> None:
    rig.fill(6)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    store = await rig.store(clock=lambda: now)
    job = await rig.job(store)
    rig.gw.fail_next("copy_messages", PeerFlood("PEER_FLOOD"), times=5)

    with pytest.raises(PeerFlood):
        await rig.runner(store).run(job.id)

    failed = await store.get_job(job.id)
    assert failed is not None
    assert (failed.status, failed.fail_reason) == (JobStatus.FAILED, "peer_flood")
    assert len(rig.copy_calls()) == 1  # not retried
    assert await store.pending_rows(job.id) == []
    with pytest.raises(JobWaiting) as cooling:  # rest for a day
        check_runnable(failed, now + timedelta(hours=23))
    assert cooling.value.reason == "peer_flood"
    check_runnable(failed, now + timedelta(hours=25))


@pytest.mark.parametrize(
    "error", [ForwardsRestricted("CHAT_FORWARDS_RESTRICTED"), NoPermission("x")]
)
async def test_rejections_fail_the_job_and_leave_nothing_pending(rig: Rig, error: Any) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("copy_messages", error)

    with pytest.raises(type(error)):
        await rig.runner(store).run(job.id)

    failed = await store.get_job(job.id)
    assert failed is not None and failed.status is JobStatus.FAILED
    assert type(error).__name__ in (failed.fail_reason or "")
    assert await store.pending_rows(job.id) == [] and failed.cursor_src_id == 0


async def test_a_poisoned_message_fails_alone_and_its_neighbours_are_copied(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    job = await rig.job(store, batch_size=4)
    rig.gw.poison(rig.src.id, 3, "MESSAGE_ID_INVALID")

    final = await rig.runner(store).run(job.id)

    assert rig.dst_texts == ["m1", "m2", "m4"]
    assert (final.status, final.stats) == (JobStatus.DONE, {"done": 3, "failed": 1})
    assert final.cursor_src_id == 4
    assert rig.copy_calls() == [[1, 2, 3, 4], [1], [2], [3], [4]]  # whole batch, then one by one


async def test_a_poisoned_album_fails_as_a_whole(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "a")
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO] * 2)
    store = await rig.store()
    job = await rig.job(store, batch_size=5)
    rig.gw.poison(rig.src.id, 3)

    final = await rig.runner(store).run(job.id)

    assert rig.dst_texts == ["a"] and final.stats == {"done": 1, "failed": 2}


async def test_a_message_telegram_did_not_copy_is_recorded_as_failed(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)

    async def delete_two(ids: list[int], n: int) -> None:
        del rig.gw.messages[rig.src.id][1]  # deleted after we listed it

    rig.wrap_copy(delete_two)
    final = await rig.runner(store).run(job.id)

    assert final.stats == {"done": 2, "failed": 1} and rig.dst_texts == ["m1", "m3"]


# ---- ownership ------------------------------------------------------------------------------


async def test_a_second_runner_is_refused_and_changes_nothing(rig: Rig) -> None:
    rig.fill(3)
    first = await rig.store()
    job = await rig.job(first)
    await first.claim(job.id)  # someone is running it
    second = await rig.store()

    with pytest.raises(JobBusy):
        await rig.runner(second).run(job.id)

    still = await second.get_job(job.id)
    assert still is not None and still.status is JobStatus.RUNNING
    assert rig.copy_calls() == []

    final = await rig.runner(second).run(job.id, force_takeover=True)
    assert final.status is JobStatus.DONE


async def test_the_heartbeat_is_kept_while_the_runner_works(rig: Rig) -> None:
    rig.fill(2)
    store = await rig.store()
    job = await rig.job(store)
    beats = 0
    original = store.heartbeat

    async def counting(job_id: int) -> None:
        nonlocal beats
        beats += 1
        await original(job_id)

    store.heartbeat = counting  # type: ignore[method-assign]

    async def slow(ids: list[int], n: int) -> None:
        await asyncio.sleep(0.1)

    rig.wrap_copy(slow)
    await rig.runner(store, timing=RunnerTiming(0.5, 0.01)).run(job.id)

    assert beats >= 2


# ---- delta and resume bookkeeping -----------------------------------------------------------


async def test_running_a_finished_job_again_copies_only_what_is_new(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    await rig.runner(store).run(job.id)
    rig.fill(3, start=5)

    final = await rig.runner(store).run(job.id)

    assert rig.copy_calls() == [[1, 2, 3], [4], [5, 6, 7]]
    assert rig.dst_texts == rig.src_texts and final.cursor_src_id == 7


async def test_running_a_finished_job_with_nothing_new_is_quick_and_silent(rig: Rig) -> None:
    rig.fill(2)
    store = await rig.store()
    job = await rig.job(store)
    await rig.runner(store).run(job.id)
    before = len(rig.copy_calls())
    rig.delays.clear()

    final = await rig.runner(store).run(job.id)

    assert final.status is JobStatus.DONE and len(rig.copy_calls()) == before
    assert rig.delays == []


async def test_messages_already_done_are_skipped_even_below_the_cursor_reset(rig: Rig) -> None:
    """Resume step 4: a rerun from cursor 0 (the coming ``--refilter``) never repeats a copy."""
    rig.fill(5)
    store = await rig.store()
    job = await rig.job(store, batch_size=2)
    plan = rig.gw.messages[rig.src.id]

    batch_id = await store.begin_batch(job.id, [Unit((plan[0],)), Unit((plan[1],))])
    await store.commit_batch(job.id, batch_id, [MessageResult(1, 91), MessageResult(2, 92)], 0)

    final = await rig.runner(store).run(job.id)

    assert rig.copy_calls() == [[3, 4], [5]]  # batch [1, 2] was skipped whole, without a delay
    assert final.cursor_src_id == 5 and final.stats["done"] == 5


# ---- pacing ---------------------------------------------------------------------------------


async def test_batches_are_spaced_by_the_minimum_delay(rig: Rig) -> None:
    rig.fill(9)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)

    await rig.runner(store).run(job.id)

    assert sum(rig.delays) == pytest.approx(
        4.0
    )  # none before the first batch, 2 s before each other
    assert max(rig.delays) <= 0.5  # slept in slices, so pause/stop are noticed


async def test_a_long_pause_follows_every_so_many_messages(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    job = await rig.job(store, batch_size=2)
    limits = Limits(min_delay=2.0, jitter=0.0, long_pause_every=4, long_pause_range=(10.0, 10.0))

    await rig.runner(store, limits=limits).run(job.id)

    assert sum(rig.delays) == pytest.approx(2.0 + (2.0 + 10.0))  # before batch 2, then before 3


async def test_jitter_stays_within_its_bounds(rig: Rig) -> None:
    rig.fill(40)
    store = await rig.store()
    job = await rig.job(store, batch_size=1)
    limits = Limits(min_delay=2.0, jitter=0.3, long_pause_every=10_000)
    delays: list[float] = []

    async def record_whole(seconds: float) -> None:
        delays.append(seconds)

    runner = rig.runner(store, limits=limits, timing=RunnerTiming(poll_interval=1000.0))
    runner._sleep = record_whole  # slices would hide the drawn value
    await runner.run(job.id)

    assert len(delays) == 39 and all(1.4 <= d <= 2.6 for d in delays) and len(set(delays)) > 1
