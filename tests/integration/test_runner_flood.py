"""Phase 4: the runner under Telegram's rate limits, on ``FakeGateway`` and a real SQLite file.

A FloodWait up to ``max_auto_wait`` is sat out and the very same batch sent again (no gap, no
duplicate); a longer one, or too many in a row, parks the job. Reads are paced and resumed like
writes. The daily cap rests the job until midnight. What the limiter learned survives a restart.
No test really waits: sleeps are recorded, and every sleep is one whole wait (``WHOLE``).
"""

import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import LIMITS, Rig
from tgmirror.core.config import Limits
from tgmirror.core.errors import DailyCapReached, FloodWait, PeerFlood
from tgmirror.core.gateway import NO_FILTER, MediaKind, ServerFilter, SrcMessage, Unit
from tgmirror.engine.jobs import JobWaiting, check_runnable
from tgmirror.engine.runner import RunnerTiming, StopSignal
from tgmirror.store.jobs import Control, JobStatus

WHOLE = RunnerTiming(poll_interval=1000.0, heartbeat_interval=3600)  # one sleep call per wait


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 10, 22, 0).astimezone()

    def __call__(self) -> datetime:
        return self.now


def query(rig: Rig, sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(rig.path)
    try:
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


# ---- writes ---------------------------------------------------------------------------------


async def test_a_flood_wait_is_sat_out_and_the_same_batch_is_sent_again(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))

    final = await rig.runner(store, timing=WHOLE).run(job.id)

    assert (
        final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    )  # no gap, no copy twice
    assert rig.copy_calls() == [[1, 2, 3], [1, 2, 3], [4, 5, 6]]  # the same batch, once more
    assert 31 <= rig.delays[0] <= 35  # seconds + a jitter of 1..5
    assert rig.delays[1] == pytest.approx(4.0)  # then the delay doubled from 2 s
    assert rig.recorder.codes == ["flood_waiting"]
    assert final.stats == {"done": 6, "failed": 0}
    assert query(rig, "SELECT status, COUNT(*) FROM msg_map GROUP BY status") == [("done", 6)]


async def test_the_flood_is_logged_with_the_delay_that_led_to_it(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))

    await rig.runner(store, timing=WHOLE).run(job.id)

    assert query(
        rig, "SELECT job_id, kind, seconds, method, delay_ms, batch_size FROM flood_log"
    ) == [
        (job.id, "flood_wait", 30, "copy_messages", 2000, 3)  # 2 s: before it doubled
    ]


async def test_slow_mode_is_handled_like_a_flood_wait_but_logged_apart(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(8, slow_mode=True))

    final = await rig.runner(store, timing=WHOLE).run(job.id)

    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    assert query(rig, "SELECT kind, seconds FROM flood_log") == [("slow_mode", 8)]


async def test_a_wait_of_exactly_max_auto_wait_is_still_sat_out(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("copy_messages", FloodWait(60))  # LIMITS: max_auto_wait = 60

    final = await rig.runner(store, timing=WHOLE).run(job.id)

    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts


async def test_a_longer_wait_parks_the_job_unless_told_to_wait(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("copy_messages", FloodWait(3600))

    with pytest.raises(FloodWait):
        await rig.runner(store, timing=WHOLE).run(job.id)
    parked = await store.get_job(job.id)
    assert parked is not None and parked.status is JobStatus.WAITING_FLOOD
    assert rig.delays == [] and await store.pending_rows(job.id) == []  # it did not sleep

    rig.gw.fail_next("copy_messages", FloodWait(3600))
    final = await rig.runner(store, timing=WHOLE, wait=True).run(job.id)  # --wait

    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    assert 3601 <= sum(rig.delays) <= 3605  # slept in slices of ``poll_interval``


async def test_floods_in_a_row_on_the_same_call_stop_the_job(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("copy_messages", FloodWait(10), times=5)

    with pytest.raises(FloodWait):
        await rig.runner(store, timing=WHOLE).run(job.id)

    parked = await store.get_job(job.id)
    assert parked is not None and parked.status is JobStatus.WAITING_FLOOD
    assert parked.cursor_src_id == 0 and await store.pending_rows(job.id) == []
    assert len(rig.copy_calls()) == 5 and len(rig.delays) == 4  # four waits, the fifth gave up
    assert len(query(rig, "SELECT id FROM flood_log")) == 5


async def test_four_floods_in_a_row_are_still_survived(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("copy_messages", FloodWait(10), times=4)

    final = await rig.runner(store, timing=WHOLE).run(job.id)

    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    assert len(rig.copy_calls()) == 5


async def test_three_floods_shrink_the_next_batches_and_say_so(rig: Rig) -> None:
    rig.fill(12)
    store = await rig.store()
    job = await rig.job(store, batch_size=4)
    rig.gw.fail_next("copy_messages", FloodWait(5), times=3)

    final = await rig.runner(store, timing=WHOLE).run(job.id)

    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    assert rig.copy_calls() == [[1, 2, 3, 4]] * 4 + [[5, 6], [7, 8], [9, 10], [11, 12]]
    assert ("throttled", {"batch_size": 2, "delay": 16.0}) in rig.recorder.notices


async def test_pause_during_a_flood_wait_returns_at_once_and_resumes_cleanly(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))

    async def pause_while_waiting(seconds: float) -> None:
        rig.delays.append(seconds)
        await store.set_control(job.id, Control.PAUSE)  # what `tgmirror pause` does

    paused = await rig.runner(store, sleep=pause_while_waiting, timing=WHOLE).run(job.id)

    assert paused.status is JobStatus.PAUSED and paused.control is Control.NONE
    assert rig.copy_calls() == [[1, 2, 3]]  # it did not try again
    assert await store.pending_rows(job.id) == [] and paused.cursor_src_id == 0
    assert query(rig, "SELECT delay_ms FROM limiter_state") == [(4000,)]  # the lesson is saved

    final = await rig.runner(store).run(job.id)
    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts


async def test_ctrl_c_during_a_flood_wait_saves_and_stops(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))
    stop = StopSignal()

    async def interrupt(seconds: float) -> None:
        stop.request()

    final = await rig.runner(store, stop=stop, sleep=interrupt, timing=WHOLE).run(job.id)

    assert final.status is JobStatus.STOPPED and rig.dst_texts == []
    assert await store.pending_rows(job.id) == []


async def test_peer_flood_stops_at_once_and_is_never_waited_out(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("copy_messages", PeerFlood("PEER_FLOOD"), times=5)

    with pytest.raises(PeerFlood):
        await rig.runner(store, timing=WHOLE).run(job.id)

    assert len(rig.copy_calls()) == 1 and rig.delays == []
    assert query(rig, "SELECT kind, seconds, method FROM flood_log") == [
        ("peer_flood", None, "copy_messages")
    ]


# ---- reads ----------------------------------------------------------------------------------


async def test_a_flood_wait_while_reading_is_sat_out_and_the_read_repeated(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    job = await rig.job(store, batch_size=2)
    rig.gw.fail_next("iter_messages", FloodWait(20))

    final = await rig.runner(store, timing=WHOLE).run(job.id)

    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    assert 21 <= rig.delays[0] <= 25
    assert query(rig, "SELECT kind, method FROM flood_log") == [("flood_wait", "iter_messages")]


def flaky_reads(rig: Rig, after: int) -> list[int]:
    """The first read raises a FloodWait after ``after`` messages; returns where reads started."""
    original = rig.gw.iter_messages
    starts: list[int] = []

    async def flaky(
        src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        starts.append(min_id)
        seen = 0
        async for message in original(src, min_id=min_id, filters=filters):
            if len(starts) == 1 and seen == after:
                raise FloodWait(20)
            seen += 1
            yield message

    rig.gw.iter_messages = flaky  # type: ignore[method-assign]
    return starts


async def test_a_read_cut_by_a_flood_carries_on_after_the_last_message_it_gave(rig: Rig) -> None:
    rig.fill(10)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    starts = flaky_reads(rig, after=4)

    final = await rig.runner(store, timing=WHOLE).run(job.id)

    assert starts == [0, 4]  # the second read started after message 4: nothing lost, nothing twice
    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    assert final.stats == {"done": 10, "failed": 0}


async def test_an_album_cut_in_two_by_a_flood_is_still_sent_whole(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "a")
    rig.gw.add_message(rig.src.id, "b")
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO] * 3)  # ids 3, 4, 5
    rig.gw.add_message(rig.src.id, "c")
    store = await rig.store()
    job = await rig.job(store, batch_size=10)
    starts = flaky_reads(rig, after=3)  # ids 1-3 arrive, then the flood: mid-album

    final = await rig.runner(store, timing=WHOLE).run(job.id)

    assert starts[0] == 0 and len(starts) == 2
    assert final.status is JobStatus.DONE
    assert rig.copy_calls() == [[1, 2, 3, 4, 5, 6]]  # the album travelled in one piece
    in_album = [m.grouped_id is not None for m in rig.gw.messages[rig.dst.id]]
    assert in_album == [False, False, True, True, True, False]


async def test_peer_flood_while_reading_fails_the_job(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store)
    rig.gw.fail_next("iter_messages", PeerFlood("PEER_FLOOD"))

    with pytest.raises(PeerFlood):
        await rig.runner(store, timing=WHOLE).run(job.id)

    failed = await store.get_job(job.id)
    assert failed is not None and failed.fail_reason == "peer_flood"
    assert query(rig, "SELECT kind, method FROM flood_log") == [("peer_flood", "iter_messages")]


async def test_reads_are_paced_by_their_own_bucket(rig: Rig) -> None:
    """Reconcile reads twice, then the run reads the source: the first read is free, two wait."""
    rig.fill(3)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    plan = rig.gw.messages[rig.src.id]
    await store.begin_batch(job.id, [Unit((m,)) for m in plan])  # a run that died mid-call
    rig.delays.clear()

    await rig.runner(store, timing=WHOLE).run(job.id)

    assert rig.delays == [0.5, 0.5]  # LIMITS: read_delay 0.5 s, no jitter, unthrottled


# ---- limiter state --------------------------------------------------------------------------


async def test_what_the_limiter_learned_is_kept_for_the_next_run(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    job = await rig.job(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(3600))
    with pytest.raises(FloodWait):
        await rig.runner(store, timing=WHOLE).run(job.id)
    assert query(rig, "SELECT delay_ms, sent_today FROM limiter_state") == [(4000, 0)]

    final = await rig.runner(store, timing=WHOLE).run(job.id)  # a new process, same account

    assert final.status is JobStatus.DONE
    assert rig.delays == [4.0]  # the doubled delay between its two batches
    assert query(rig, "SELECT delay_ms, sent_today FROM limiter_state") == [(4000, 6)]


# ---- daily cap ------------------------------------------------------------------------------


def midnight_after(clock: Clock) -> datetime:
    return datetime.combine(clock().date() + timedelta(days=1), time.min).astimezone()


async def test_the_daily_cap_rests_the_job_until_midnight_and_then_it_finishes(rig: Rig) -> None:
    clock = Clock()
    limits = Limits(min_delay=2.0, jitter=0.0, long_pause_every=10_000, daily_cap=6)
    rig.fill(9)
    store = await rig.store(clock=clock)
    job = await rig.job(store, batch_size=3)

    with pytest.raises(DailyCapReached) as stop:
        await rig.runner(store, limits=limits, clock=clock, timing=WHOLE).run(job.id)

    assert (stop.value.sent_today, stop.value.cap) == (6, 6)
    rested = await store.get_job(job.id)
    assert rested is not None
    assert (rested.status, rested.fail_reason) == (JobStatus.WAITING_FLOOD, "daily_cap")
    assert rested.resume_at == midnight_after(clock) == stop.value.resume_at
    assert rested.cursor_src_id == 6 and await store.pending_rows(job.id) == []
    assert rig.dst_texts == [f"m{i}" for i in range(1, 7)]
    with pytest.raises(JobWaiting) as refused:
        check_runnable(rested, clock())
    assert refused.value.reason == "daily_cap"

    # the same day, even without check_runnable: the count is remembered
    with pytest.raises(DailyCapReached):
        await rig.runner(store, limits=limits, clock=clock, timing=WHOLE).run(job.id)
    assert len(rig.copy_calls()) == 2

    clock.now += timedelta(hours=3)  # past midnight
    check_runnable(rested, clock())
    final = await rig.runner(store, limits=limits, clock=clock, timing=WHOLE).run(job.id)

    assert final.status is JobStatus.DONE and rig.dst_texts == rig.src_texts
    assert query(rig, "SELECT sent_today FROM limiter_state") == [(3,)]  # a new day, counted anew


async def test_the_daily_cap_does_not_stop_reads_or_a_job_with_nothing_to_send(rig: Rig) -> None:
    clock = Clock()
    limits = Limits(min_delay=2.0, jitter=0.0, long_pause_every=10_000, daily_cap=3)
    rig.fill(3)
    store = await rig.store(clock=clock)
    job = await rig.job(store, batch_size=3)
    await rig.runner(store, limits=limits, clock=clock, timing=WHOLE).run(job.id)  # exactly the cap

    final = await rig.runner(store, limits=limits, clock=clock, timing=WHOLE).run(job.id)

    assert final.status is JobStatus.DONE  # nothing new: nothing to hold back


# ---- unchanged behaviour --------------------------------------------------------------------


def test_the_shared_test_limits_have_the_auto_wait_these_tests_rely_on() -> None:
    assert LIMITS.max_auto_wait == 60.0 and LIMITS.read_delay == 0.5 and LIMITS.jitter == 0.0
