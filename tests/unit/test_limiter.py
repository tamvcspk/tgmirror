"""The AIMD limiter with a fake clock and a recording sleep: no real waiting anywhere."""

import random
from datetime import date, datetime, timedelta

import pytest

from tgmirror.core.config import Limits
from tgmirror.core.errors import BudgetReached
from tgmirror.core.limiter import (
    DECAY_AFTER,
    FLOOD_WINDOW,
    MIN_BUDGET,
    LaneBudget,
    LaneState,
    Limiter,
    LimiterState,
    grow_budget,
)


class FakeClock:
    """Wall time, in the local zone (the daily cap counts local days)."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 3, 10, 12, 0).astimezone()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


NO_JITTER = Limits(min_delay=2.0, max_delay=60.0, jitter=0.0, long_pause_every=10_000)


def make(
    limits: Limits = NO_JITTER,
    *,
    state: LimiterState | None = None,
    clock: FakeClock | None = None,
) -> tuple[Limiter, Sleeps, FakeClock]:
    sleeps, clock = Sleeps(), clock or FakeClock()
    return Limiter(limits, sleeps, state=state, clock=clock, rng=random.Random(1)), sleeps, clock


async def write(limiter: Limiter, cost: int = 1) -> None:
    await limiter.acquire(cost)
    limiter.on_success(cost)


# ---- spacing of writes ----------------------------------------------------------------------


async def test_the_first_write_goes_out_at_once_and_the_next_waits_one_delay() -> None:
    limiter, sleeps, _ = make()

    await write(limiter)
    assert sleeps.calls == []
    await write(limiter)

    assert sleeps.calls == [2.0]


async def test_the_wait_is_the_delay_times_a_jitter_within_bounds() -> None:
    limiter, sleeps, _ = make(Limits(min_delay=2.0, jitter=0.3, long_pause_every=10_000))

    for _ in range(60):
        await write(limiter)

    assert len(sleeps.calls) == 59
    assert all(1.4 <= s <= 2.6 for s in sleeps.calls) and len(set(sleeps.calls)) > 1


async def test_a_long_pause_follows_every_so_many_messages() -> None:
    limits = Limits(min_delay=2.0, jitter=0.0, long_pause_every=4, long_pause_range=(10.0, 10.0))
    limiter, sleeps, _ = make(limits)

    for _ in range(3):
        await write(limiter, cost=2)  # 6 messages: the pause is due before the third call

    assert sleeps.calls == [2.0, 12.0]


# ---- AIMD -----------------------------------------------------------------------------------


async def test_a_flood_doubles_the_delay_up_to_the_maximum() -> None:
    limiter, sleeps, _ = make()
    await write(limiter)

    seen = []
    for _ in range(7):
        limiter.on_flood()
        seen.append(limiter.delay)

    assert seen == [4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]  # capped at max_delay
    await limiter.acquire(1)
    assert sleeps.calls == [60.0]


async def test_twenty_successes_shrink_the_delay_by_a_tenth() -> None:
    limiter, _, _ = make()
    limiter.on_flood()  # 4.0
    for _ in range(DECAY_AFTER - 1):
        limiter.on_success(1)
    assert limiter.delay == 4.0

    limiter.on_success(1)

    assert limiter.delay == pytest.approx(3.6)


async def test_a_flood_restarts_the_count_of_successes() -> None:
    limiter, _, _ = make()
    limiter.on_flood()
    for _ in range(DECAY_AFTER - 1):
        limiter.on_success(1)
    limiter.on_flood()  # 8.0, and the streak is gone

    limiter.on_success(1)

    assert limiter.delay == 8.0


async def test_the_delay_never_decays_below_the_minimum() -> None:
    limiter, _, _ = make()
    limiter.on_flood()  # 4.0

    for _ in range(DECAY_AFTER * 30):
        limiter.on_success(1)

    assert limiter.delay == 2.0


# ---- reads ----------------------------------------------------------------------------------


async def test_reads_have_their_own_bucket_and_the_first_one_is_free() -> None:
    limits = Limits(min_delay=2.0, read_delay=0.5, jitter=0.0)
    limiter, sleeps, _ = make(limits)

    await limiter.acquire(1, "read")
    await limiter.acquire(1, "read")
    await limiter.acquire(3, "read")  # three requests
    await limiter.acquire(1)  # a write is not slowed by the reads

    assert sleeps.calls == [0.5, 1.5]


async def test_a_read_lane_slows_down_with_its_own_floods_not_with_the_writes() -> None:
    limits = Limits(min_delay=2.0, read_delay=0.5, jitter=0.0)
    limiter, sleeps, _ = make(limits)
    await limiter.acquire(1, "read")
    limiter.on_flood("send", 7)
    limiter.on_flood("send", 7)  # the write lane is at 8.0: four times the minimum

    await limiter.acquire(1, "read")
    assert sleeps.calls == [0.5]  # reads are not pushed back by a write flood

    limiter.on_flood("history")
    limiter.on_flood("history")
    await limiter.acquire(1, "read")
    assert sleeps.calls == [0.5, 2.0]  # its own flood slows it by the same factor as before


# ---- budget, sliding window, cap warning (phase 19d2) --------------------------------------


async def put(limiter: Limiter, cost: int = 1, action: str = "forward", dst: int = 7) -> None:
    await limiter.acquire(cost, action=action, dst=dst)
    limiter.on_success(cost, action, dst)


async def test_a_lane_that_never_met_a_long_flood_is_never_stopped() -> None:
    limiter, _, _ = make(Limits(daily_cap=3, jitter=0.0))

    for _ in range(10):
        await put(limiter, 5)  # 50 messages, far over daily_cap: it only warns

    limiter.check_budget(100, "forward", 7)


async def test_a_long_flood_sets_the_budget_to_eighty_percent_of_what_the_lane_sent() -> None:
    limiter, _, _ = make()
    await put(limiter, 30)

    budget = limiter.on_flood("forward", 7, seconds=3600)

    assert budget is not None and (budget.amount, budget.window_hours) == (24, 1)
    assert budget.flood_seconds == 3600
    assert limiter.delay_of("forward", 7) == 2.0  # a quota is not a burst: the delay stays


async def test_a_wait_longer_than_an_hour_names_the_24_hour_window() -> None:
    limiter, _, _ = make()
    await put(limiter, 100)

    budget = limiter.on_flood("forward", 7, seconds=3601)

    assert budget is not None and (budget.amount, budget.window_hours) == (80, 24)


async def test_a_budget_never_falls_below_the_floor() -> None:
    limiter, _, _ = make()
    await put(limiter, 5)

    budget = limiter.on_flood("forward", 7, seconds=7200)

    assert budget is not None and budget.amount == MIN_BUDGET


async def test_a_second_long_flood_halves_the_budget_and_keeps_its_window() -> None:
    limiter, _, _ = make()
    await put(limiter, 100)
    limiter.on_flood("forward", 7, seconds=7200)  # 80 per 24 h

    budget = limiter.on_flood("forward", 7, seconds=900)  # shorter now, but the window is set

    assert budget is not None and (budget.amount, budget.window_hours) == (40, 24)


async def test_a_short_flood_sets_no_budget_and_doubles_the_delay() -> None:
    limiter, _, _ = make()
    await put(limiter, 100)

    budget = limiter.on_flood("forward", 7, seconds=30)

    assert budget is None and limiter.budget_of("forward", 7) is None
    assert limiter.delay_of("forward", 7) == 4.0


async def test_a_wait_that_is_not_a_quota_sets_no_budget_however_long() -> None:
    limiter, _, _ = make()
    await put(limiter, 100)

    budget = limiter.on_flood("forward", 7, seconds=7200, quota=False)  # slow mode, premium...

    assert budget is None and limiter.budget_of("forward", 7) is None
    assert limiter.delay_of("forward", 7) == 4.0


async def test_a_read_lane_never_gets_a_budget() -> None:
    limiter, _, _ = make()

    assert limiter.on_flood("history", seconds=7200) is None
    assert limiter.delay_of("history") == 4.0


async def test_a_write_that_would_pass_the_budget_stops_with_when_the_window_lets_go() -> None:
    limiter, _, clock = make()
    await put(limiter, 30)
    limiter.on_flood("forward", 7, seconds=3600)  # 24 per hour

    with pytest.raises(BudgetReached) as stop:
        await limiter.acquire(5, action="forward", dst=7)

    err = stop.value
    assert (err.sent, err.budget, err.window_hours) == (30, 24, 1)
    assert clock() < err.resume_at <= clock() + timedelta(hours=2)
    clock.now = err.resume_at
    await limiter.acquire(5, action="forward", dst=7)  # the window has let them go


async def test_another_destination_another_action_and_reads_are_not_held_by_it() -> None:
    limiter, _, _ = make()
    await put(limiter, 30)
    limiter.on_flood("forward", 7, seconds=3600)

    await limiter.acquire(5, action="forward", dst=8)
    await limiter.acquire(5, action="send", dst=7)
    await limiter.acquire(1, "read", action="history")


async def test_a_first_batch_bigger_than_the_budget_is_not_blocked_for_ever() -> None:
    limiter, _, _ = make()
    limiter.on_flood("forward", 7, seconds=3600)  # nothing sent: the floor of 20 per hour

    await limiter.acquire(25, action="forward", dst=7)  # nothing sent yet: it goes
    limiter.on_success(25, "forward", 7)
    with pytest.raises(BudgetReached):
        await limiter.acquire(1, action="forward", dst=7)


async def test_the_budget_grows_ten_percent_for_every_day_without_a_long_flood() -> None:
    limiter, _, clock = make()
    await put(limiter, 100)
    limiter.on_flood("forward", 7, seconds=3600)  # 80 per hour
    clock.advance(hours=23)
    assert limiter.budget_of("forward", 7).amount == 80  # type: ignore[union-attr]

    clock.advance(hours=2)  # a whole day since
    assert limiter.budget_of("forward", 7).amount == 88  # type: ignore[union-attr]

    clock.advance(days=1)  # 97 would pass 95% of the 100 that drew the flood: it stops there
    assert limiter.budget_of("forward", 7).amount == 95  # type: ignore[union-attr]


async def test_growth_by_the_clock_stops_at_95_percent_of_bad_and_stays_there() -> None:
    limiter, _, clock = make()
    await put(limiter, 1000)
    limiter.on_flood("forward", 7, seconds=3600)  # 800 per hour; 1000 drew the flood
    budget = limiter.budget_of("forward", 7)
    assert budget is not None and (budget.amount, budget.bad) == (800, 1000)

    for _ in range(10):  # ten days: 800 * 1.1**10 would be 2075
        clock.advance(days=1)
        grown = limiter.budget_of("forward", 7)
        assert grown is not None and grown.amount <= 950

    assert grown is not None and grown.amount == 950 and grown.bad == 1000


async def test_a_bad_older_than_probe_after_hours_no_longer_lets_the_budget_grow() -> None:
    limiter, _, clock = make(Limits(jitter=0.0, probe_after_hours=24))
    await put(limiter, 1000)
    limiter.on_flood("forward", 7, seconds=3600)  # 800; the day's growth would be 880

    clock.advance(days=2)  # ``bad`` is two days old, past the 24 hours it bounds growth for

    budget = limiter.budget_of("forward", 7)
    assert budget is not None and budget.amount == 800  # held where it was: not lowered, not grown


def test_a_budget_with_no_bad_does_not_grow() -> None:
    now = datetime(2026, 3, 10, 12, 0).astimezone()
    budget = LaneBudget(100, 1, now, now, 3600)

    assert grow_budget(budget, now + timedelta(days=5)) == budget


def test_a_budget_already_past_its_ceiling_is_never_lowered() -> None:
    now = datetime(2026, 3, 10, 12, 0).astimezone()
    budget = LaneBudget(99, 1, now, now, 3600, bad=100, bad_at=now)

    assert grow_budget(budget, now + timedelta(days=3)).amount == 99


def _lane_with(budget: LaneBudget, clock: FakeClock) -> Limiter:
    state = LimiterState(2.0, clock().date(), 0, (LaneState("forward", 7, 2.0, budget),))
    limiter, _, _ = make(state=state, clock=clock)
    return limiter


async def test_a_flood_above_ok_goes_back_to_ok_instead_of_halving() -> None:
    clock = FakeClock()
    now = clock()
    limiter = _lane_with(LaneBudget(88, 1, now, now, 3600, ok=80, bad=120, bad_at=now), clock)
    await put(limiter, 90)  # the lane ran 90 into ground that no run proved clean

    cut = limiter.on_flood("forward", 7, seconds=3600)

    assert cut is not None
    assert (cut.amount, cut.ok, cut.bad, cut.probe_fails) == (80, 80, 90, 1)
    assert cut.checked_at == clock()


async def test_a_flood_at_or_below_ok_halves_the_budget_and_forgets_ok() -> None:
    clock = FakeClock()
    now = clock()
    limiter = _lane_with(LaneBudget(80, 1, now, now, 3600, ok=80, bad=120, bad_at=now), clock)
    await put(limiter, 100)

    cut = limiter.on_flood("forward", 7, seconds=3600)

    assert cut is not None
    assert (cut.amount, cut.ok, cut.bad, cut.probe_fails) == (40, None, 100, 0)


async def test_bad_keeps_the_lowest_amount_that_ever_drew_a_flood() -> None:
    clock = FakeClock()
    now = clock()
    limiter = _lane_with(LaneBudget(80, 1, now, now, 3600, bad=70, bad_at=now), clock)
    await put(limiter, 100)

    cut = limiter.on_flood("forward", 7, seconds=3600)

    assert cut is not None and cut.bad == 70


async def test_forgetting_budgets_clears_only_the_lanes_of_that_destination() -> None:
    clock = FakeClock()
    now = clock()
    budget = LaneBudget(80, 1, now, now, 3600, ok=80, bad=100, bad_at=now)
    state = LimiterState(
        3.0,
        clock().date(),
        0,
        (
            LaneState("forward", 7, 3.0, budget),
            LaneState("send", 7, 3.0, budget),
            LaneState("forward", 8, 3.0, budget),
        ),
    )
    limiter, _, _ = make(state=state, clock=clock)

    assert limiter.forget_budgets(7) == 2

    assert limiter.budget_of("forward", 7) is None and limiter.budget_of("send", 7) is None
    assert limiter.budget_of("forward", 8) == budget
    assert limiter.delay_of("forward", 7) == 3.0  # the pace it learned stays
    limiter.check_budget(10_000, "forward", 7)  # nothing stops a lane that learned nothing yet
    assert limiter.forget_budgets() == 1


async def test_a_budget_survives_a_restart() -> None:
    limiter, _, clock = make()
    await put(limiter, 100)
    limiter.on_flood("forward", 7, seconds=7200)
    saved = limiter.state

    again, _, _ = make(state=saved, clock=clock)

    assert again.budget_of("forward", 7) == limiter.budget_of("forward", 7)
    assert again.sent_in("forward", 7, 24) == 100
    with pytest.raises(BudgetReached):
        await again.acquire(5, action="forward", dst=7)


async def test_the_daily_cap_only_warns_at_one_and_at_two_times_it() -> None:
    limiter, _, _ = make(Limits(daily_cap=10, jitter=0.0))
    results = []
    for cost in (9, 1, 5, 5, 100):
        await put(limiter, cost)
        results.append(limiter.cap_warning())

    assert results == [None, 10, None, 20, None]  # each level once; nothing was ever stopped


async def test_the_total_counts_every_write_lane_of_the_account() -> None:
    limiter, _, _ = make()
    await put(limiter, 3, "forward", 7)
    await put(limiter, 4, "send", 8)

    assert limiter.sent_total(24) == 7 and limiter.sent_in("send", 8, 1) == 4


async def test_deleting_has_a_lane_of_its_own_and_is_not_a_daily_cap_matter() -> None:
    """Phase 20: removing messages is paced and learns a budget like a post, per chat, but it is
    not something ``daily_cap`` reminds about (that counts what the account posted)."""
    limiter, _, _ = make(Limits(daily_cap=10, jitter=0.0))
    await put(limiter, 50, "delete", 7)
    await put(limiter, 4, "send", 8)

    assert limiter.sent_in("delete", 7, 1) == 50
    assert limiter.sent_total(24) == 4 and limiter.cap_warning() is None
    assert limiter.delay_of("delete", 7) == limiter.delay_of("send", 8)  # a lane like any


# ---- throttling -----------------------------------------------------------------------------


async def test_three_floods_within_ten_minutes_halve_the_batch_size() -> None:
    limiter, _, clock = make()
    assert limiter.batch_size(20) == 20

    for _ in range(2):
        limiter.on_flood()
        clock.advance(minutes=1)
    assert limiter.batch_size(20) == 20  # two are not enough
    limiter.on_flood()

    assert limiter.throttle == 1 and limiter.batch_size(20) == 10
    assert limiter.batch_size(1) == 1  # never below one message


async def test_floods_spread_over_more_than_ten_minutes_do_not_throttle() -> None:
    limiter, _, clock = make()

    for _ in range(6):
        limiter.on_flood()
        clock.advance(minutes=6)

    assert limiter.throttle == 0


async def test_another_three_floods_halve_it_again_and_quiet_lifts_it() -> None:
    limiter, _, clock = make()
    for _ in range(6):
        limiter.on_flood()
    assert limiter.batch_size(20) == 5  # 20 -> 10 -> 5

    clock.advance(seconds=FLOOD_WINDOW.total_seconds() - 1)
    assert limiter.batch_size(20) == 5
    clock.advance(seconds=1)  # ten quiet minutes

    assert limiter.throttle == 0 and limiter.batch_size(20) == 20


async def test_while_throttled_the_delay_does_not_decay_below_a_raised_floor() -> None:
    limiter, _, _ = make()
    for _ in range(3):
        limiter.on_flood()  # 16.0, throttle 1: floor 4.0

    for _ in range(DECAY_AFTER * 40):
        limiter.on_success(1)

    assert limiter.delay == 4.0  # min_delay * 2 while throttled


# ---- state ----------------------------------------------------------------------------------


async def test_the_state_carries_the_delay_and_todays_count_into_a_new_limiter() -> None:
    limiter, _, clock = make(Limits(daily_cap=10, jitter=0.0))
    limiter.on_flood()
    await write(limiter, cost=7)
    saved = limiter.state

    again, sleeps, _ = make(Limits(daily_cap=10, jitter=0.0), state=saved, clock=clock)

    assert again.delay == 4.0 and again.state.sent_today == 7 and again.sent_total(24) == 7
    await again.acquire(4)  # past daily_cap, which only warns: nothing stops it
    await again.acquire(1)  # the first write of a run is free, the second waits the saved delay
    assert sleeps.calls == [4.0]


async def test_yesterdays_count_is_forgotten_but_the_delay_is_kept() -> None:
    clock = FakeClock()
    old = LimiterState(
        delay=8.0, day=clock().astimezone().date() - timedelta(days=1), sent_today=99
    )

    limiter, _, _ = make(Limits(daily_cap=100, jitter=0.0), state=old, clock=clock)

    assert limiter.delay == 8.0 and limiter.state.sent_today == 0


@pytest.mark.parametrize(("saved", "expected"), [(0.5, 2.0), (500.0, 60.0), (7.0, 7.0)])
async def test_a_saved_delay_is_clamped_to_the_current_limits(
    saved: float, expected: float
) -> None:
    clock = FakeClock()
    state = LimiterState(saved, clock().astimezone().date(), 0)

    limiter, _, _ = make(state=state, clock=clock)

    assert limiter.delay == expected


async def test_an_interrupted_sleep_records_nothing() -> None:
    """The runner's sleep raises when a pause/stop arrives: that call must not count as sent."""

    class Cut(Exception):
        pass

    async def cut(seconds: float) -> None:
        raise Cut

    limiter = Limiter(NO_JITTER, cut, clock=FakeClock(), rng=random.Random(1))
    await write(limiter, cost=3)

    with pytest.raises(Cut):
        await limiter.acquire(5)

    assert limiter.state.sent_today == 3


# ---- credit: time already spent between two writes -------------------------------------------


async def test_time_already_spent_counts_towards_the_delay() -> None:
    limiter, sleeps, _ = make()
    await write(limiter)

    await limiter.acquire(1, "write", credit=0.5)

    assert sleeps.calls == [1.5]


async def test_a_credit_as_long_as_the_delay_means_no_wait_at_all() -> None:
    limiter, sleeps, _ = make()
    await write(limiter)

    await limiter.acquire(1, "write", credit=5.0)  # more than the 2 s: still no negative wait

    assert sleeps.calls == []
    limiter.on_success(1)
    await limiter.acquire(1, "write")  # the next one, without credit, waits the whole delay
    assert sleeps.calls == [2.0]


async def test_a_credit_never_shortens_a_long_pause() -> None:
    limits = Limits(min_delay=2.0, jitter=0.0, long_pause_every=2, long_pause_range=(10.0, 10.0))
    limiter, sleeps, _ = make(limits)
    await write(limiter, cost=2)  # a long pause is due before the next write

    await limiter.acquire(1, "write", credit=99.0)

    assert sleeps.calls == [10.0]


async def test_the_first_write_ignores_credit_and_still_goes_at_once() -> None:
    limiter, sleeps, _ = make()

    await limiter.acquire(1, "write", credit=1.0)

    assert sleeps.calls == []


# ---- checking the budget without spending anything -------------------------------------------


async def test_the_budget_can_be_checked_before_the_work_and_costs_nothing() -> None:
    limits = Limits(min_delay=2.0, jitter=0.0, long_pause_every=10_000)
    limiter, sleeps, _ = make(limits)
    await put(limiter, 30)
    limiter.on_flood("forward", 7, seconds=3600)  # 24 per hour

    with pytest.raises(BudgetReached):
        limiter.check_budget(1, "forward", 7)  # 30 sent: already past it

    assert limiter.state.sent_today == 30 and sleeps.calls == []  # nothing recorded or slept


# ---- lanes (phase 19d1) ---------------------------------------------------------------------


async def test_a_flood_backs_off_only_its_own_write_lane() -> None:
    limiter, sleeps, _ = make()
    await limiter.acquire(1, action="forward", dst=7)
    limiter.on_success(1, "forward", 7)
    limiter.on_flood("forward", 7)  # forward -> 7: 2 s becomes 4 s

    assert limiter.delay_of("forward", 7) == 4.0
    assert limiter.delay_of("send", 7) == 2.0  # another action
    assert limiter.delay_of("forward", 8) == 2.0  # another destination
    assert limiter.delay == 4.0  # what status reports: the highest write lane

    await limiter.acquire(1, action="send", dst=7)
    await limiter.acquire(1, action="forward", dst=7)

    assert sleeps.calls == [2.0, 4.0]  # each write waits the delay of its own lane


async def test_each_lane_decays_on_its_own_successes() -> None:
    limiter, _, _ = make()
    limiter.on_flood("forward", 7)
    limiter.on_flood("send", 7)  # both at 4 s (two floods: no throttle yet)

    for _ in range(DECAY_AFTER):
        limiter.on_success(1, "forward", 7)

    assert limiter.delay_of("forward", 7) == pytest.approx(3.6)
    assert limiter.delay_of("send", 7) == 4.0  # forwarding well taught the send lane nothing


async def test_slow_mode_is_a_hard_floor_for_posts_to_that_destination() -> None:
    limits = Limits(min_delay=2.0, jitter=0.3, long_pause_every=10_000)
    limiter, sleeps, _ = make(limits)
    limiter.set_slow_mode(7, 30)

    for _ in range(40):
        await limiter.acquire(1, action="send", dst=7)
        limiter.on_success(1, "send", 7)
    await limiter.acquire(1, action="send", dst=8)  # no slow mode there

    assert all(s >= 30 for s in sleeps.calls[:39]) and sleeps.calls[39] <= 2.6


async def test_slow_mode_counts_the_time_already_spent_uploading() -> None:
    limiter, sleeps, _ = make()
    limiter.set_slow_mode(7, 30)
    await limiter.acquire(1, action="send", dst=7)

    await limiter.acquire(1, credit=12.0, action="send", dst=7)

    assert sleeps.calls == [18.0]  # 30 s between the posts, 12 of them spent already


async def test_messages_sent_are_counted_per_lane_in_five_minute_buckets() -> None:
    limiter, _, clock = make(clock=FakeClock(datetime(2026, 3, 10, 12, 0).astimezone()))
    limiter.on_success(5, "forward", 7)
    clock.advance(minutes=30)
    limiter.on_success(3, "forward", 7)
    limiter.on_success(9, "send", 7)  # another lane

    assert limiter.sent_in("forward", 7, 1) == 8
    assert limiter.sent_in("send", 7, 1) == 9

    clock.advance(hours=2)  # the first two are now more than an hour, less than a day, old
    assert limiter.sent_in("forward", 7, 1) == 0
    assert limiter.sent_in("forward", 7, 24) == 8

    clock.advance(hours=23)
    assert limiter.sent_in("forward", 7, 24) == 0
    assert limiter.state.buckets == ()  # the old buckets are dropped


async def test_lanes_and_buckets_survive_a_restart() -> None:
    first, _, clock = make()
    first.on_flood("forward", 7)
    first.on_success(4, "forward", 7)
    saved = first.state

    second, _, _ = make(state=saved, clock=clock)

    assert second.delay_of("forward", 7) == 4.0
    assert second.delay_of("send", 7) == 2.0  # a lane with no row of its own: the base delay
    assert second.sent_in("forward", 7, 1) == 4
    assert second.state.lanes == saved.lanes and second.state.buckets == saved.buckets


async def test_a_database_from_before_the_lanes_seeds_every_lane_with_its_one_delay() -> None:
    old = LimiterState(delay=8.0, day=date(2026, 3, 10), sent_today=3)

    limiter, _, _ = make(state=old, clock=FakeClock(datetime(2026, 3, 10, 12, 0).astimezone()))

    assert limiter.delay_of("forward", 7) == limiter.delay_of("send", 9) == 8.0


async def test_a_one_hour_window_lets_go_within_five_minutes_of_the_hour_not_of_the_next_one() -> (
    None
):
    """The run of 2026-10-17: it stopped at 02:32 on a budget of 1506 per hour, having started at
    02:15, and the hourly buckets made it wait until 04:00 (1 h 45 min) instead of about 03:20."""
    limiter, _, clock = make(clock=FakeClock(datetime(2026, 10, 17, 2, 15).astimezone()))
    limiter.on_flood("forward", 7, seconds=3600)  # no history: the floor of 20 per hour
    limiter._budgets[("forward", 7)] = LaneBudget(
        1506, 1, clock(), clock(), 1737
    )  # what the real flood taught
    for _ in range(17):  # 17 minutes of sending, 90 messages a minute: five-minute buckets fill
        limiter.on_success(90, "forward", 7)
        clock.advance(minutes=1)
    clock.now = datetime(2026, 10, 17, 2, 32).astimezone()

    with pytest.raises(BudgetReached) as stop:
        await limiter.acquire(20, action="forward", dst=7)

    wait = stop.value.resume_at - clock()
    assert (
        timedelta(minutes=30) < wait <= timedelta(hours=1)
    )  # not the 1 h 28 min of hourly buckets
    clock.now = stop.value.resume_at
    await limiter.acquire(20, action="forward", dst=7)  # and then it really fits


async def test_a_message_leaves_the_window_at_most_five_minutes_after_the_hour_is_up() -> None:
    limiter, _, clock = make(clock=FakeClock(datetime(2026, 10, 17, 3, 2).astimezone()))
    limiter.on_success(10, "send", 7)
    clock.advance(minutes=50)
    assert limiter.sent_in("send", 7, 1) == 10  # the bucket (03:00-03:05) is wholly inside
    clock.advance(minutes=16)  # 66 minutes: past the bucket plus its five minutes
    assert limiter.sent_in("send", 7, 1) == 0
