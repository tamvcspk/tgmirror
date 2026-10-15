"""Phase 4: the runner under Telegram's rate limits, on ``FakeGateway`` and a real SQLite file.

A FloodWait up to ``max_auto_wait`` is sat out and the very same batch sent again (no gap, no
duplicate); a longer one, or too many in a row, parks the run. Reads are paced and resumed like
writes. The daily cap rests the run until midnight. What the limiter learned survives a restart.
No test really waits: sleeps are recorded, and every sleep is one whole wait (``WHOLE``).
"""

import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import LIMITS, Rig
from tgmirror.core.errors import BudgetReached, FloodWait, PeerFlood
from tgmirror.core.gateway import (
    NO_FILTER,
    ChatKind,
    MediaKind,
    ServerFilter,
    SrcMessage,
    Unit,
)
from tgmirror.engine.runner import RunControl, RunnerTiming
from tgmirror.store.runs import Control, RunStatus

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
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert (
        final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
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
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))

    await rig.runner(store, timing=WHOLE).run(run)

    assert query(
        rig, "SELECT run_id, kind, seconds, method, delay_ms, batch_size FROM flood_log"
    ) == [
        (run.id, "flood_wait", 30, "copy_messages", 2000, 3)  # 2 s: before it doubled
    ]


async def test_slow_mode_is_handled_like_a_flood_wait_but_logged_apart(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(8, slow_mode=True))

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    assert query(rig, "SELECT kind, seconds FROM flood_log") == [("slow_mode", 8)]


async def test_a_wait_of_exactly_max_auto_wait_is_still_sat_out(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store)
    rig.gw.fail_next("copy_messages", FloodWait(60))  # LIMITS: max_auto_wait = 60

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts


async def test_a_longer_wait_parks_the_run_unless_told_to_wait(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store)
    rig.gw.fail_next("copy_messages", FloodWait(3600))

    with pytest.raises(FloodWait):
        await rig.runner(store, timing=WHOLE).run(run)
    parked = await store.get_run(run.id)
    assert parked is not None and parked.status is RunStatus.WAITING_FLOOD
    assert rig.delays == [] and await store.pending_rows(run.id) == []  # it did not sleep

    rig.gw.fail_next("copy_messages", FloodWait(3600))
    final = await rig.runner(store, timing=WHOLE, wait=True).run(
        await rig.begin(store, after_wait=True)
    )  # --wait

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    assert 3601 <= sum(rig.delays) <= 3605  # slept in slices of ``poll_interval``


async def test_floods_in_a_row_on_the_same_call_stop_the_run(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store)
    rig.gw.fail_next("copy_messages", FloodWait(10), times=5)

    with pytest.raises(FloodWait):
        await rig.runner(store, timing=WHOLE).run(run)

    parked = await store.get_run(run.id)
    assert parked is not None and parked.status is RunStatus.WAITING_FLOOD
    assert parked.cursor_src_id == 0 and await store.pending_rows(run.id) == []
    assert len(rig.copy_calls()) == 5 and len(rig.delays) == 4  # four waits, the fifth gave up
    assert len(query(rig, "SELECT id FROM flood_log")) == 5


async def test_four_floods_in_a_row_are_still_survived(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store)
    rig.gw.fail_next("copy_messages", FloodWait(10), times=4)

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    assert len(rig.copy_calls()) == 5


async def test_three_floods_shrink_the_next_batches_and_say_so(rig: Rig) -> None:
    rig.fill(12)
    store = await rig.store()
    run = await rig.begin(store, batch_size=4)
    rig.gw.fail_next("copy_messages", FloodWait(5), times=3)

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    assert rig.copy_calls() == [[1, 2, 3, 4]] * 4 + [[5, 6], [7, 8], [9, 10], [11, 12]]
    assert ("throttled", {"batch_size": 2, "delay": 16.0}) in rig.recorder.notices


async def test_stop_during_a_flood_wait_returns_at_once_and_resumes_cleanly(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))

    async def stop_while_waiting(seconds: float) -> None:
        rig.delays.append(seconds)
        await store.set_control(run.id, Control.STOP)  # what `tgmirror stop` does

    stopped = await rig.runner(store, sleep=stop_while_waiting, timing=WHOLE).run(run)

    assert stopped.status is RunStatus.STOPPED and stopped.control is Control.NONE
    assert rig.copy_calls() == [[1, 2, 3]]  # it did not try again
    assert await store.pending_rows(run.id) == [] and stopped.cursor_src_id == 0
    assert query(rig, "SELECT delay_ms FROM limiter_state") == [(4000,)]  # the lesson is saved

    final = await rig.runner(store).run(await rig.begin(store))
    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts


async def test_pause_during_a_flood_wait_takes_effect_after_the_wait(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))
    control = RunControl()
    asked = False
    copied_when_held: list[list[list[int]]] = []

    async def pause_then_resume(seconds: float) -> None:
        nonlocal asked
        rig.delays.append(seconds)
        current = await store.get_run(run.id)
        assert current is not None
        if current.status is RunStatus.PAUSED:
            copied_when_held.append(rig.copy_calls())
            control.request_resume()
        elif not asked:
            asked = True
            control.request_pause()

    final = await rig.runner(store, sleep=pause_then_resume, timing=WHOLE, control=control).run(run)

    # the wait was sat out and the same batch sent again; only then did the run hold
    assert copied_when_held == [[[1, 2, 3], [1, 2, 3]]]
    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    assert "paused" in rig.recorder.codes and "resumed" in rig.recorder.codes


async def test_ctrl_c_during_a_flood_wait_saves_and_stops(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))
    stop = RunControl()

    async def interrupt(seconds: float) -> None:
        stop.request_stop()

    final = await rig.runner(store, control=stop, sleep=interrupt, timing=WHOLE).run(run)

    assert final.status is RunStatus.STOPPED and rig.dst_texts == []
    assert await store.pending_rows(run.id) == []


async def test_peer_flood_stops_at_once_and_is_never_waited_out(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store)
    rig.gw.fail_next("copy_messages", PeerFlood("PEER_FLOOD"), times=5)

    with pytest.raises(PeerFlood):
        await rig.runner(store, timing=WHOLE).run(run)

    assert len(rig.copy_calls()) == 1 and rig.delays == []
    assert query(rig, "SELECT kind, seconds, method FROM flood_log") == [
        ("peer_flood", None, "copy_messages")
    ]


# ---- reads ----------------------------------------------------------------------------------


async def test_a_flood_wait_while_reading_is_sat_out_and_the_read_repeated(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    run = await rig.begin(store, batch_size=2)
    rig.gw.fail_next("iter_messages", FloodWait(20))

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
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
    run = await rig.begin(store, batch_size=3)
    starts = flaky_reads(rig, after=4)

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert starts == [0, 4]  # the second read started after message 4: nothing lost, nothing twice
    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    assert final.stats == {"done": 10, "failed": 0}


async def test_an_album_cut_in_two_by_a_flood_is_still_sent_whole(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "a")
    rig.gw.add_message(rig.src.id, "b")
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO] * 3)  # ids 3, 4, 5
    rig.gw.add_message(rig.src.id, "c")
    store = await rig.store()
    run = await rig.begin(store, batch_size=10)
    starts = flaky_reads(rig, after=3)  # ids 1-3 arrive, then the flood: mid-album

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert starts[0] == 0 and len(starts) == 2
    assert final.status is RunStatus.DONE
    assert rig.copy_calls() == [[1, 2, 3, 4, 5, 6]]  # the album travelled in one piece
    in_album = [m.grouped_id is not None for m in rig.gw.messages[rig.dst.id]]
    assert in_album == [False, False, True, True, True, False]


async def test_peer_flood_while_reading_fails_the_run(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store)
    rig.gw.fail_next("iter_messages", PeerFlood("PEER_FLOOD"))

    with pytest.raises(PeerFlood):
        await rig.runner(store, timing=WHOLE).run(run)

    failed = await store.get_run(run.id)
    assert failed is not None and failed.fail_reason == "peer_flood"
    assert query(rig, "SELECT kind, method FROM flood_log") == [("peer_flood", "iter_messages")]


async def test_reads_are_paced_by_their_own_bucket(rig: Rig) -> None:
    """Reconcile reads twice, then the run reads the source: the first read is free, two wait."""
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    plan = rig.gw.messages[rig.src.id]
    await store.begin_batch(run.id, [Unit((m,)) for m in plan])  # a run that died mid-call
    rig.delays.clear()

    await rig.runner(store, timing=WHOLE).run(run)

    assert rig.delays == [0.5, 0.5]  # LIMITS: read_delay 0.5 s, no jitter, unthrottled


# ---- limiter state --------------------------------------------------------------------------


async def test_what_the_limiter_learned_is_kept_for_the_next_run(rig: Rig) -> None:
    rig.fill(6)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(30))  # a burst: the delay doubles, no budget
    final = await rig.runner(store, timing=WHOLE).run(run)
    assert final.status is RunStatus.DONE
    assert query(rig, "SELECT delay_ms FROM limiter_state") == [(4000,)]  # the lesson is saved
    rig.delays.clear()

    await rig.runner(store, timing=WHOLE).run(
        await rig.begin(store, after_wait=True)
    )  # new process

    assert query(rig, "SELECT action, delay_ms, budget FROM limiter_lanes") == [
        ("forward", 4000, None)
    ]
    assert rig.delays == []  # nothing new to send, nothing to wait for


# ---- budget, sliding window, cap warning (phase 19d2) ----------------------------------------


def hour_after(clock: Clock, hours: int) -> datetime:
    """The first moment, ``hours`` hours on, that is the start of a UTC hour."""
    moment = clock.now + timedelta(hours=hours)
    return moment.replace(minute=0, second=0, microsecond=0)


async def test_a_short_flood_sets_no_budget_and_a_long_one_sets_the_lanes_alone(rig: Rig) -> None:
    rig.fill(30)
    store = await rig.store()
    run = await rig.begin(store, batch_size=10)
    copy, calls = rig.gw.copy_messages, 0

    async def third_batch_meets_a_quota(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise FloodWait(7200)  # more than an hour: the 24-hour window
        return await copy(*args, **kwargs)

    rig.gw.copy_messages = third_batch_meets_a_quota  # type: ignore[method-assign]

    with pytest.raises(FloodWait):
        await rig.runner(store, timing=WHOLE).run(run)

    # 20 sent by then: 80% is 16, below the floor of MIN_BUDGET messages, so the floor
    assert query(rig, "SELECT action, dst, budget, window_hours FROM limiter_lanes") == [
        ("forward", rig.dst.id, 20, 24)
    ]
    assert query(rig, "SELECT delay_ms FROM limiter_state") == [(2000,)]  # the delay did not double
    assert (
        "budget_learned",
        {"action": "forward", "budget": 20, "window": 24, "seconds": 7200},
    ) in (rig.recorder.notices)


async def test_a_long_slow_mode_or_premium_wait_is_not_a_quota(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(7200, slow_mode=True))

    with pytest.raises(FloodWait):
        await rig.runner(store, timing=WHOLE).run(run)

    # the lane's delay doubled as for any burst, and no budget was set from a wait that says
    # something about the chat's rules, not about how much the account may send
    assert query(rig, "SELECT action, delay_ms, budget FROM limiter_lanes") == [
        ("forward", 4000, None)
    ]


async def test_the_next_run_rests_until_the_window_lets_go_and_then_goes_on(rig: Rig) -> None:
    clock = Clock()
    rig.fill(60)
    store = await rig.store(clock=clock)
    run = await rig.begin(store, batch_size=10)
    copy, calls = rig.gw.copy_messages, 0

    async def fourth_batch_meets_a_quota(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise FloodWait(3600)  # up to an hour: the 1-hour window
        return await copy(*args, **kwargs)

    rig.gw.copy_messages = fourth_batch_meets_a_quota  # type: ignore[method-assign]
    with pytest.raises(FloodWait):
        await rig.runner(store, clock=clock, timing=WHOLE).run(run)
    # 30 sent in the hour: 80% = 24, a budget of 24 per 1 hour
    assert query(rig, "SELECT budget, window_hours FROM limiter_lanes") == [(24, 1)]

    with pytest.raises(BudgetReached) as stop:
        await rig.runner(store, clock=clock, timing=WHOLE).run(
            await rig.begin(store, after_wait=True)
        )

    assert (stop.value.sent, stop.value.budget, stop.value.window_hours) == (30, 24, 1)
    rested = await store.latest_run()
    assert rested is not None
    assert (rested.status, rested.fail_reason) == (RunStatus.WAITING_FLOOD, "daily_cap")
    assert rested.resume_at == stop.value.resume_at
    assert stop.value.resume_at > clock() and stop.value.resume_at <= clock() + timedelta(hours=2)
    assert len(rig.dst_texts) == 30 and rested.cursor_src_id == 30

    clock.now = stop.value.resume_at + timedelta(minutes=1)  # the window has let them go
    with pytest.raises(BudgetReached) as again:  # 24 per window: it takes two batches, then rests
        await rig.runner(store, clock=clock, timing=WHOLE).run(
            await rig.begin(store, after_wait=True)
        )

    assert (again.value.sent, again.value.budget) == (20, 24)
    assert len(rig.dst_texts) == 50
    assert again.value.resume_at > clock.now


async def test_going_over_the_daily_cap_only_warns_and_the_run_goes_on(rig: Rig) -> None:
    limits = LIMITS.model_copy(update={"daily_cap": 3})
    rig.fill(9)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)

    final = await rig.runner(store, limits=limits, timing=WHOLE).run(run)

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    warnings = [p for code, p in rig.recorder.notices if code == "cap_warning"]
    assert warnings == [{"sent": 3, "cap": 3}, {"sent": 6, "cap": 3}]  # at 1x and at twice


async def test_a_lane_that_never_met_a_long_flood_is_never_stopped_by_what_it_sent(
    rig: Rig,
) -> None:
    limits = LIMITS.model_copy(update={"daily_cap": 1})
    rig.fill(30)
    store = await rig.store()

    final = await rig.runner(store, limits=limits, timing=WHOLE).run(
        await rig.begin(store, batch_size=10)
    )

    assert final.status is RunStatus.DONE and len(rig.dst_texts) == 30


# ---- unchanged behaviour --------------------------------------------------------------------


def test_the_shared_test_limits_have_the_auto_wait_these_tests_rely_on() -> None:
    assert LIMITS.max_auto_wait == 60.0 and LIMITS.read_delay == 0.5 and LIMITS.jitter == 0.0


# ---- lanes, context and slow mode (phase 19d1) -----------------------------------------------


async def test_a_flood_is_logged_with_its_lane_and_what_the_lane_had_sent(rig: Rig) -> None:
    rig.fill(6)
    rig.gw.premium = True
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    copy, calls = rig.gw.copy_messages, 0

    async def second_batch_is_refused(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise FloodWait(40)
        return await copy(*args, **kwargs)

    rig.gw.copy_messages = second_batch_is_refused  # type: ignore[method-assign]

    await rig.runner(store, timing=WHOLE).run(run)

    assert query(
        rig, "SELECT kind, method, action, dst, sent_1h, sent_24h, premium FROM flood_log"
    ) == [("flood_wait", "copy_messages", "forward", rig.dst.id, 3, 3, 1)]  # 3 sent by then


async def test_a_read_flood_is_logged_against_its_read_lane_without_a_destination(
    rig: Rig,
) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("count", FloodWait(10))

    await rig.runner(store, timing=WHOLE).run(run)

    assert query(rig, "SELECT method, action, dst, sent_1h, sent_24h, premium FROM flood_log") == [
        ("count", "history", None, None, None, 0)
    ]
    lanes = query(rig, "SELECT action, dst, delay_ms FROM limiter_lanes ORDER BY action")
    assert lanes == [("history", 0, 4000)]  # the read lane backed off; no write lane was touched


async def test_a_premium_wait_is_logged_apart(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)
    rig.gw.fail_next("copy_messages", FloodWait(20, premium=True))

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    assert query(rig, "SELECT kind, seconds FROM flood_log") == [("premium_wait", 20)]


async def test_a_destination_with_slow_mode_is_never_posted_to_faster_than_it_allows(
    rig: Rig,
) -> None:
    rig.dst = rig.gw.add_channel("Group", kind=ChatKind.SUPERGROUP)
    rig.gw.slow_mode[rig.dst.id] = 45
    rig.fill(9)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)

    final = await rig.runner(store, timing=WHOLE).run(run)

    assert final.status is RunStatus.DONE and rig.dst_texts == rig.src_texts
    assert rig.copy_calls() == [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
    assert [d >= 45 for d in rig.delays] == [True, True]  # the gaps between the three posts
    assert ("slow_mode", {"seconds": 45}) in rig.recorder.notices


async def test_slow_mode_is_not_asked_of_a_broadcast_channel(rig: Rig) -> None:
    rig.fill(3)
    store = await rig.store()
    run = await rig.begin(store, batch_size=3)

    await rig.runner(store, timing=WHOLE).run(run)

    assert rig.gw.calls_to("slowmode_seconds") == []
    assert len(rig.gw.calls_to("is_premium")) == 1
