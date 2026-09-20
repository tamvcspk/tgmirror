"""The AIMD limiter with a fake clock and a recording sleep: no real waiting anywhere."""

import random
from datetime import date, datetime, timedelta

import pytest

from tgmirror.core.config import Limits
from tgmirror.core.errors import DailyCapReached
from tgmirror.core.limiter import DECAY_AFTER, FLOOD_WINDOW, Limiter, LimiterState


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


async def test_reads_slow_down_together_with_writes() -> None:
    limits = Limits(min_delay=2.0, read_delay=0.5, jitter=0.0)
    limiter, sleeps, _ = make(limits)
    await limiter.acquire(1, "read")
    limiter.on_flood()
    limiter.on_flood()  # delay 8.0: four times the minimum

    await limiter.acquire(1, "read")

    assert sleeps.calls == [2.0]


# ---- daily cap ------------------------------------------------------------------------------


async def test_the_daily_cap_stops_a_write_that_would_pass_it() -> None:
    clock = FakeClock(datetime(2026, 3, 10, 23, 30).astimezone())
    limiter, _, _ = make(Limits(daily_cap=10, jitter=0.0), clock=clock)
    await write(limiter, cost=6)

    with pytest.raises(DailyCapReached) as stop:
        await limiter.acquire(5)  # 6 + 5 > 10

    assert (stop.value.sent_today, stop.value.cap) == (6, 10)
    assert stop.value.resume_at == datetime(2026, 3, 11, 0, 0).astimezone()
    await limiter.acquire(4)  # exactly the cap is fine


async def test_a_new_local_day_starts_again_from_zero() -> None:
    clock = FakeClock(datetime(2026, 3, 10, 23, 59).astimezone())
    limiter, _, _ = make(Limits(daily_cap=10, jitter=0.0), clock=clock)
    await write(limiter, cost=10)
    with pytest.raises(DailyCapReached):
        await limiter.acquire(1)

    clock.advance(minutes=2)  # past midnight
    await limiter.acquire(10)

    assert limiter.state.day == date(2026, 3, 11) and limiter.state.sent_today == 0


async def test_a_first_batch_bigger_than_the_cap_is_not_blocked_for_ever() -> None:
    limiter, _, _ = make(Limits(daily_cap=3, jitter=0.0))

    await write(limiter, cost=5)  # nothing sent yet today: it goes

    with pytest.raises(DailyCapReached):
        await limiter.acquire(1)


async def test_the_daily_cap_does_not_apply_to_reads() -> None:
    limiter, _, _ = make(Limits(daily_cap=1, jitter=0.0))
    await write(limiter, cost=1)

    await limiter.acquire(1, "read")
    await limiter.acquire(1, "read")


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

    assert again.delay == 4.0 and again.state.sent_today == 7
    with pytest.raises(DailyCapReached):
        await again.acquire(4)
    await again.acquire(3)
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


# ---- checking the cap without spending anything -------------------------------------------------


async def test_the_cap_can_be_checked_before_the_work_and_costs_nothing() -> None:
    limits = Limits(min_delay=2.0, jitter=0.0, daily_cap=5, long_pause_every=10_000)
    limiter, sleeps, _ = make(limits)
    await write(limiter, cost=4)

    limiter.check_cap(1)  # 4 + 1 fits
    with pytest.raises(DailyCapReached):
        limiter.check_cap(2)  # 4 + 2 does not

    assert limiter.state.sent_today == 4 and sleeps.calls == []  # nothing was recorded or slept


async def test_a_first_batch_bigger_than_the_cap_passes_the_check_too() -> None:
    limits = Limits(min_delay=2.0, jitter=0.0, daily_cap=5, long_pause_every=10_000)
    limiter, _, _ = make(limits)

    limiter.check_cap(9)  # nothing sent today: never blocked for ever
