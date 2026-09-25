"""``tgmirror status``: progress, speed and ETA (pure), and what is gathered from the store."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.gateway import ChatKind, MediaKind, SrcMessage, Unit
from tgmirror.core.limiter import LimiterState
from tgmirror.engine.status import MIN_SAMPLE, build_report, cap_days, estimate
from tgmirror.store.db import HEARTBEAT_TIMEOUT, Store
from tgmirror.store.msgmap import MessageResult
from tgmirror.store.runs import Control, Run, RunOptions, RunSpec, RunStatus

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_run(**changes: object) -> Run:
    """A run that began 100 s ago, copied 250 messages and is at id 250 of a source of 1000."""
    base = Run(
        id=1,
        mirror_id=1,
        account="default",
        src_id=-1001,
        src_title="Source",
        src_kind=ChatKind.BROADCAST,
        dst_kind=ChatKind.BROADCAST,
        dst_id=-1002,
        dst_title="Copy",
        mode="auto",
        filters_json="{}",
        options=RunOptions(src_last_id=1000),
        status=RunStatus.RUNNING,
        control=Control.NONE,
        cursor_from=0,
        cursor_src_id=250,
        resume_at=None,
        fail_reason=None,
        stats={"done": 250},
        started_at=NOW - timedelta(seconds=100),
        ended_at=None,
        updated_at=NOW,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


# ---- an ordinary run with a count: by the messages dealt with -----------------------------------


def counted(**changes: object) -> Run:
    """400 messages to look at (the source's newest id says 1000: the count is what is used)."""
    return make_run(options=RunOptions(src_last_id=1000, total_items=400), **changes)


def test_progress_is_what_was_dealt_with_against_the_count() -> None:
    run = counted(stats={"done": 100, "failed": 20, "skipped_filter": 60, "already_done": 20})

    est = estimate(run, NOW, live=True)

    assert est.fraction == 0.5  # 200 of 400, whichever way each was settled
    assert est.eta == timedelta(seconds=100)  # the other 200 at the pace of the first 200


def test_what_the_filter_and_strategy_b_leave_out_counts_as_progress_too() -> None:
    run = counted(stats={"done": 10, "skipped_unsupported": 30, "gone": 60})

    assert run.handled == 100 and estimate(run, NOW, live=True).fraction == 0.25


def test_a_count_that_turns_out_too_small_stops_at_100_percent() -> None:
    est = estimate(counted(stats={"done": 450}), NOW, live=True)

    assert est.fraction == 1.0 and est.eta is None


def test_a_finished_run_is_100_percent_however_much_the_filter_dropped() -> None:
    """The count is an upper bound: a client-side filter may drop what it never subtracted."""
    est = estimate(counted(status=RunStatus.DONE, stats={"done": 40}), NOW, live=False)

    assert est.fraction == 1.0


def test_a_run_without_a_count_still_uses_the_source_id() -> None:
    old = make_run(options=RunOptions(src_last_id=1000, total_items=0))

    assert estimate(old, NOW, live=True).fraction == 0.25


# ---- the daily cap -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "cap", "sent_today", "days"),
    [
        (100, 5000, 0, 0),  # fits in what is left of today
        (5000, 5000, 0, 0),  # exactly today's allowance
        (5001, 5000, 0, 1),  # one message over: tomorrow
        (12000, 5000, 0, 2),  # 5000 today, then 5000 and 2000 on the next two days
        (7000, 5000, 4000, 2),  # 1000 left today, then 5000 and 1000
        (500, 5000, 6000, 1),  # today is used up (a first batch may have overshot)
    ],
)
def test_days_of_rest_the_daily_cap_costs(left: int, cap: int, sent_today: int, days: int) -> None:
    assert cap_days(left, cap, sent_today) == days


# ---- an ordinary run: by source message id ---------------------------------------------------


def test_progress_speed_and_eta_of_a_running_run() -> None:
    est = estimate(make_run(), NOW, live=True)

    assert est.fraction == 0.25
    assert est.speed == 2.5  # 250 messages in 100 s
    assert est.eta == timedelta(seconds=300)  # the other 750 ids at the pace of the first 250


def test_progress_of_a_delta_run_counts_only_from_where_it_began() -> None:
    run = make_run(cursor_from=500, cursor_src_id=750, stats={"done": 250})

    assert estimate(run, NOW, live=True).fraction == 0.5  # 250 of the 500 new ids


def test_failed_messages_count_as_handled_for_the_speed() -> None:
    run = make_run(stats={"done": 200, "failed": 50, "skipped_filter": 900})

    assert estimate(run, NOW, live=True).speed == 2.5  # copied or failed, not filtered out


def test_the_cursor_past_the_recorded_total_stops_at_100_percent() -> None:
    """Messages were posted while the run went on: the total is where the run began."""
    est = estimate(make_run(cursor_src_id=1200), NOW, live=True)

    assert est.fraction == 1.0 and est.eta is None


def test_nothing_new_since_the_run_began_has_no_progress_to_show_until_it_is_done() -> None:
    running = make_run(cursor_from=1000, cursor_src_id=1000, stats={})
    assert estimate(running, NOW, live=True).fraction is None
    assert estimate(replace(running, status=RunStatus.DONE), NOW, live=True).fraction == 1.0


def test_a_run_from_before_the_total_was_recorded_has_no_fraction() -> None:
    old = make_run(options=RunOptions())

    est = estimate(old, NOW, live=True)

    assert est.fraction is None and est.eta is None and est.speed == 2.5


def test_too_early_to_say_how_fast() -> None:
    fresh = make_run(started_at=NOW - timedelta(seconds=MIN_SAMPLE - 1))

    est = estimate(fresh, NOW, live=True)

    assert est.speed is None and est.eta is None and est.fraction == 0.25


@pytest.mark.parametrize("status", [RunStatus.PAUSED, RunStatus.WAITING_FLOOD, RunStatus.STOPPED])
def test_only_a_running_run_gets_an_eta(status: RunStatus) -> None:
    est = estimate(make_run(status=status), NOW, live=status is RunStatus.PAUSED)

    assert est.eta is None and est.fraction == 0.25


def test_a_finished_run_is_complete_and_its_speed_stops_at_its_end() -> None:
    run = make_run(status=RunStatus.DONE, ended_at=NOW - timedelta(seconds=50), cursor_src_id=1000)

    est = estimate(run, NOW, live=False)

    assert est.fraction == 1.0 and est.eta is None
    assert est.speed == 5.0  # 250 in the 50 s between its start and its end, not 100 s till now


def test_a_run_that_died_measures_up_to_its_last_sign_of_life() -> None:
    run = make_run(updated_at=NOW - timedelta(seconds=50))  # still says running, nobody beats

    est = estimate(run, NOW, live=False)

    assert est.speed == 5.0 and est.eta is None


# ---- a retry: exact, by the failed messages left ---------------------------------------------


def test_a_retry_is_measured_against_the_failed_messages_still_waiting() -> None:
    run = make_run(options=RunOptions(retry_of=1), stats={"done": 2, "failed": 1})

    est = estimate(run, NOW, live=True, retry_left=7)

    assert est.fraction == pytest.approx(0.3)  # 3 of 10
    assert est.eta == timedelta(seconds=100 * 7 / 3)


def test_messages_found_deleted_count_as_handled_by_a_retry() -> None:
    run = make_run(options=RunOptions(retry_of=1), stats={"done": 1, "gone": 3})

    assert estimate(run, NOW, live=True, retry_left=4).fraction == 0.5  # 4 of 8


# ---- what is gathered from the store ---------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
async def clocked(tmp_path: Path) -> tuple[Store, Clock]:
    clock = Clock()
    store = await Store.open(tmp_path / "state.db", clock=clock)
    try:
        yield store, clock  # type: ignore[misc]
    finally:
        await store.close()


def pair_spec(**options: object) -> RunSpec:
    gw = FakeGateway()
    return RunSpec(gw.add_channel("Src"), gw.add_channel("Dst"), options=RunOptions(**options))  # type: ignore[arg-type]


async def test_the_report_of_a_live_run(clocked: tuple[Store, Clock]) -> None:
    store, clock = clocked
    run = (await store.start_run(pair_spec(src_last_id=100))).run
    batch = await store.begin_batch(
        run.id, [Unit((SrcMessage(1, NOW, media=MediaKind.TEXT),)), Unit((SrcMessage(2, NOW),))]
    )
    await store.commit_batch(run.id, batch, [MessageResult(1, 11), MessageResult(2, None, "x")], 50)
    clock.now = NOW + timedelta(seconds=10)
    await store.save_limiter_state("default", LimiterState(2.4, clock.now.astimezone().date(), 120))
    await store.log_flood(
        run.id, kind="flood_wait", seconds=30, method="m", delay_ms=1, batch_size=1
    )
    current = await store.get_run(run.id)
    assert current is not None

    report = await build_report(store, current, now=clock.now, daily_cap=5000)

    assert (report.live, report.abandoned) == (True, False)
    assert report.estimate.fraction == 0.5 and report.failed_now == 1 and report.retry_left is None
    assert (report.delay, report.sent_today, report.daily_cap) == (2.4, 120, 5000)
    assert report.floods_24h == 1 and report.last_flood is not None
    assert report.last_flood.kind == "flood_wait"


async def test_the_report_tells_how_much_is_left_and_what_the_cap_costs(
    clocked: tuple[Store, Clock],
) -> None:
    store, clock = clocked
    run = (await store.start_run(pair_spec(total_items=12000))).run
    batch = await store.begin_batch(run.id, [Unit((SrcMessage(1, NOW, media=MediaKind.TEXT),))])
    current = await store.commit_batch(run.id, batch, [MessageResult(1, 11)], 1)
    clock.now = NOW + timedelta(seconds=10)

    report = await build_report(store, current, now=clock.now, daily_cap=5000)

    assert report.left == 11999
    assert report.cap_days == 2  # 5000 today, 5000 and 1999 on the next two days


async def test_a_finished_run_has_nothing_left_to_cost_days(clocked: tuple[Store, Clock]) -> None:
    store, clock = clocked
    run = (await store.start_run(pair_spec(total_items=12000))).run
    await store.finish(run.id, RunStatus.STOPPED)
    finished = await store.get_run(run.id)
    assert finished is not None

    report = await build_report(store, finished, now=clock.now, daily_cap=5000)

    assert report.cap_days == 0  # it is not going to run again by itself


async def test_the_total_is_recorded_on_the_run_and_stays_off_the_pairs_memory(
    clocked: tuple[Store, Clock],
) -> None:
    store, _ = clocked
    run = (await store.start_run(pair_spec(src_last_id=9))).run

    updated = await store.set_total(run.id, 250)

    assert updated.options.total_items == 250 and updated.options.src_last_id == 9
    mirror = await store.find_mirror(run.src_id, run.dst_id)
    assert mirror is not None and mirror.options.total_items == 0  # it belongs to this run only


async def test_a_run_nobody_holds_is_abandoned_not_live(clocked: tuple[Store, Clock]) -> None:
    store, clock = clocked
    run = (await store.start_run(pair_spec())).run
    clock.now = NOW + HEARTBEAT_TIMEOUT + timedelta(seconds=1)

    report = await build_report(store, run, now=clock.now, daily_cap=5000)

    assert (report.live, report.abandoned) == (False, True)


async def test_a_finished_run_is_neither(clocked: tuple[Store, Clock]) -> None:
    store, clock = clocked
    run = (await store.start_run(pair_spec())).run
    await store.finish(run.id, RunStatus.STOPPED)
    finished = await store.get_run(run.id)
    assert finished is not None

    report = await build_report(store, finished, now=clock.now + timedelta(hours=1), daily_cap=1)

    assert (report.live, report.abandoned, report.delay) == (False, False, None)


async def test_yesterdays_count_of_sent_messages_does_not_show_today(
    clocked: tuple[Store, Clock],
) -> None:
    store, clock = clocked
    run = (await store.start_run(pair_spec())).run
    yesterday = (clock.now - timedelta(days=1)).astimezone().date()
    await store.save_limiter_state("default", LimiterState(3.0, yesterday, 4999))

    report = await build_report(store, run, now=clock.now, daily_cap=5000)

    assert (report.delay, report.sent_today) == (3.0, 0)


async def test_floods_older_than_a_day_are_not_counted(clocked: tuple[Store, Clock]) -> None:
    store, clock = clocked
    run = (await store.start_run(pair_spec())).run
    await store.log_flood(
        run.id, kind="flood_wait", seconds=1, method="m", delay_ms=1, batch_size=1
    )
    later = NOW + timedelta(hours=25)

    report = await build_report(store, run, now=later, daily_cap=1)

    assert report.floods_24h == 0 and report.last_flood is not None  # the run's own, still known


async def test_a_retry_report_knows_how_many_failures_are_left(
    clocked: tuple[Store, Clock],
) -> None:
    store, clock = clocked
    first = (await store.start_run(pair_spec())).run
    batch = await store.begin_batch(first.id, [Unit((SrcMessage(i, NOW),)) for i in (1, 2, 3)])
    results = [MessageResult(i, None, "x") for i in (1, 2, 3)]
    await store.commit_batch(first.id, batch, results, 3)
    await store.finish(first.id, RunStatus.DONE)
    retry = (await store.start_run(pair_spec(retry_of=first.id))).run
    again = await store.begin_batch(retry.id, [Unit((SrcMessage(1, NOW),))])
    await store.commit_batch(retry.id, again, [MessageResult(1, 10)], 3)
    current = await store.get_run(retry.id)
    assert current is not None

    report = await build_report(store, current, now=clock.now, daily_cap=1)

    assert report.retry_left == 2 and report.estimate.fraction == pytest.approx(1 / 3)
