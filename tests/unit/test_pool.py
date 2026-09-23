"""The request budget and the part scheduler (``core/pool.py``): no Telegram, no real waiting."""

import asyncio
import random

import pytest

from tgmirror.core.errors import FloodWait, PerMessage, Transient, TransportPressure
from tgmirror.core.pool import TRANSPORT_WAIT, RequestBudget, run_parts


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def settles(coro: object, seconds: float = 0.05) -> bool:
    task = asyncio.ensure_future(coro)  # type: ignore[arg-type]
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()
    return bool(done)


# ---- the budget -------------------------------------------------------------------------------


def test_a_budget_needs_a_start_within_its_maximum() -> None:
    with pytest.raises(ValueError):
        RequestBudget(start=5, maximum=4)
    with pytest.raises(ValueError):
        RequestBudget(start=0, maximum=4)


async def test_only_as_many_requests_as_the_limit_are_in_flight() -> None:
    budget = RequestBudget(start=2, maximum=8)
    await budget.acquire()
    await budget.acquire()

    assert budget.in_flight == 2 and not await settles(budget.acquire())
    budget.release()
    assert await settles(budget.acquire())


async def test_the_lowest_priority_number_is_served_first_and_equals_in_order() -> None:
    budget = RequestBudget(start=1, maximum=4)
    await budget.acquire()
    order: list[str] = []

    async def want(name: str, priority: int) -> None:
        await budget.acquire(priority)
        order.append(name)
        budget.release()

    tasks = [
        asyncio.create_task(want("download-1", 1)),
        asyncio.create_task(want("upload", 0)),
        asyncio.create_task(want("download-2", 1)),
    ]
    await asyncio.sleep(0.01)
    budget.release()
    await asyncio.gather(*tasks)

    assert order == ["upload", "download-1", "download-2"]


async def test_a_waiter_that_gives_up_leaves_no_slot_taken_and_no_one_stuck() -> None:
    budget = RequestBudget(start=1, maximum=2)
    await budget.acquire()
    assert not await settles(budget.acquire())  # cancelled after waiting
    budget.release()

    assert budget.in_flight == 0
    assert await settles(budget.acquire())  # a new one is not stuck behind the abandoned one


async def test_a_waiter_cancelled_as_its_slot_arrives_hands_the_slot_back() -> None:
    budget = RequestBudget(start=1, maximum=2)
    await budget.acquire()
    waiter = asyncio.create_task(budget.acquire())
    await asyncio.sleep(0.01)

    budget.release()  # the slot goes to the waiter...
    waiter.cancel()  # ...which is cancelled before it runs
    await asyncio.gather(waiter, return_exceptions=True)

    assert budget.in_flight == 0


async def test_the_limit_grows_a_step_after_enough_parts_without_pushback() -> None:
    clock = Clock()
    budget = RequestBudget(start=2, maximum=3, grow_after=3, cooldown=30.0, clock=clock)

    for _ in range(2):
        budget.succeeded()
    assert budget.limit == 2
    budget.succeeded()
    assert budget.limit == 3
    for _ in range(9):
        budget.succeeded()
    assert budget.limit == 3  # never past the maximum


async def test_pushback_halves_the_limit_and_stops_growth_for_a_while() -> None:
    clock = Clock()
    budget = RequestBudget(start=8, maximum=8, grow_after=2, cooldown=30.0, clock=clock)

    budget.pressure()
    assert budget.limit == 4
    budget.pressure()
    budget.pressure()
    budget.pressure()
    assert budget.limit == 1  # never below one

    for _ in range(4):
        budget.succeeded()
    assert budget.limit == 1  # within the cooldown: no growth however well it goes
    clock.now += 31
    for _ in range(2):
        budget.succeeded()
    assert budget.limit == 2


async def test_a_bigger_limit_wakes_the_ones_waiting() -> None:
    budget = RequestBudget(start=1, maximum=4, grow_after=1)
    await budget.acquire()
    waiter = asyncio.create_task(budget.acquire())
    await asyncio.sleep(0.01)
    assert not waiter.done()

    budget.succeeded()  # limit 2
    await asyncio.wait_for(waiter, 1)

    assert budget.in_flight == 2


# ---- the scheduler ----------------------------------------------------------------------------


class Sleeper:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        await asyncio.sleep(0)


async def test_every_part_is_done_exactly_once() -> None:
    done: list[int] = []

    async def work(index: int) -> None:
        await asyncio.sleep(0)
        done.append(index)

    await run_parts(20, work, RequestBudget(start=4, maximum=8))

    assert sorted(done) == list(range(20))


async def test_no_more_parts_are_in_flight_than_the_budget_allows() -> None:
    budget = RequestBudget(start=3, maximum=3)
    running = peak = 0

    async def work(index: int) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.005)
        running -= 1

    await run_parts(12, work, budget)

    assert peak == 3  # all three slots were used, and no more


async def test_a_part_that_meets_pushback_is_repeated_after_a_growing_wait() -> None:
    tries: dict[int, int] = {}
    sleeper = Sleeper()
    budget = RequestBudget(start=4, maximum=8)

    async def work(index: int) -> None:
        tries[index] = tries.get(index, 0) + 1
        if index == 2 and tries[index] <= 2:
            raise TransportPressure("HTTP 429")

    await run_parts(5, work, budget, sleep=sleeper, backoff=1.0, rng=random.Random(0))

    assert tries == {0: 1, 1: 1, 2: 3, 3: 1, 4: 1}
    assert len(sleeper.waits) == 2 and 0.75 <= sleeper.waits[0] <= 1.25
    assert 1.5 <= sleeper.waits[1] <= 2.5  # doubled
    assert budget.limit == 1  # halved twice: 4 -> 2 -> 1


async def test_a_part_that_never_gets_through_ends_the_transfer_as_transient() -> None:
    async def work(index: int) -> None:
        raise TransportPressure("closed")

    with pytest.raises(Transient):
        await run_parts(3, work, RequestBudget(start=2, maximum=2), sleep=Sleeper(), attempts=3)


async def test_a_flood_wait_ends_everything_at_once_and_cancels_what_is_running() -> None:
    cancelled: list[int] = []
    budget = RequestBudget(start=4, maximum=4)

    async def work(index: int) -> None:
        if index == 1:
            await asyncio.sleep(0.01)
            raise FloodWait(30)
        try:
            await asyncio.sleep(10)  # would take long
        except asyncio.CancelledError:
            cancelled.append(index)
            raise

    with pytest.raises(FloodWait):
        await run_parts(4, work, budget)

    assert sorted(cancelled) == [0, 2, 3]
    assert budget.limit == 2 and budget.in_flight == 0  # halved, every slot given back


async def test_any_other_error_ends_the_transfer_and_frees_the_slots() -> None:
    budget = RequestBudget(start=3, maximum=3)

    async def work(index: int) -> None:
        if index == 0:
            raise PerMessage("upload_failed")
        await asyncio.sleep(10)

    with pytest.raises(PerMessage):
        await run_parts(6, work, budget)

    assert budget.in_flight == 0


async def test_cancelling_the_transfer_cancels_its_workers() -> None:
    budget = RequestBudget(start=3, maximum=3)
    started = asyncio.Event()

    async def work(index: int) -> None:
        started.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(run_parts(6, work, budget))
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled() and budget.in_flight == 0


async def test_nothing_to_move_is_nothing_to_do() -> None:
    async def work(index: int) -> None:
        raise AssertionError("no part exists")

    await run_parts(0, work, RequestBudget(start=1, maximum=1))


async def test_a_second_transfer_shares_the_budget_and_the_first_comes_first() -> None:
    """Two transfers sharing one budget (a generic capability of ``run_parts``/``RequestBudget``;
    ``core/telethon_gateway.py`` no longer shares one between download and upload, see its module
    docstring): the lower ``priority`` number's parts go first."""
    budget = RequestBudget(start=2, maximum=2)
    order: list[str] = []

    async def down(index: int) -> None:
        order.append("down")
        await asyncio.sleep(0.001)

    async def up(index: int) -> None:
        order.append("up")
        await asyncio.sleep(0.001)

    await asyncio.gather(
        run_parts(6, down, budget, priority=1), run_parts(6, up, budget, priority=0)
    )

    assert order.count("up") == 6 and order.count("down") == 6
    assert budget.in_flight == 0


# ---- a 429 is a flood, not something to retry in seconds ----------------------------------------


async def test_a_transport_flood_ends_the_transfer_as_a_flood_wait_not_a_retry() -> None:
    tries: dict[int, int] = {}
    cancelled: list[int] = []
    budget = RequestBudget(start=4, maximum=4)
    sleeper = Sleeper()

    async def work(index: int) -> None:
        tries[index] = tries.get(index, 0) + 1
        if index == 1:
            await asyncio.sleep(0.01)
            raise TransportPressure("HTTP 429", flood=True)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(index)
            raise

    with pytest.raises(FloodWait) as caught:
        await run_parts(4, work, budget, sleep=sleeper)

    assert caught.value.transport and caught.value.seconds == TRANSPORT_WAIT
    assert tries[1] == 1 and sleeper.waits == []  # the part was not tried again, nothing slept
    assert sorted(cancelled) == [0, 2, 3]
    assert budget.limit == 2 and budget.in_flight == 0  # halved once


async def test_pushback_that_is_not_a_flood_is_still_retried_in_place() -> None:
    tries = 0

    async def work(index: int) -> None:
        nonlocal tries
        tries += 1
        if tries == 1:
            raise TransportPressure("closed", flood=False)

    await run_parts(1, work, RequestBudget(start=1, maximum=1), sleep=Sleeper())

    assert tries == 2


# ---- backing off is said aloud ------------------------------------------------------------------


def test_pushback_is_a_warning_that_says_what_the_limit_became_and_why(
    caplog: pytest.LogCaptureFixture,
) -> None:
    budget = RequestBudget(start=4, maximum=8, grow_after=32)

    with caplog.at_level("WARNING", logger="tgmirror.core.pool"):
        budget.pressure("closed connection")

    (record,) = caplog.records
    assert record.levelname == "WARNING"
    text = record.getMessage()
    assert "4 -> 2" in text and "closed connection" in text and "32 clean parts" in text


async def test_the_reason_of_a_part_that_met_pushback_reaches_the_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tries = 0

    async def work(index: int) -> None:
        nonlocal tries
        tries += 1
        if tries == 1:
            raise TransportPressure("no answer in 30 s")

    with caplog.at_level("WARNING", logger="tgmirror.core.pool"):
        await run_parts(1, work, RequestBudget(start=2, maximum=2), sleep=Sleeper())

    assert any("no answer in 30 s" in r.getMessage() for r in caplog.records)
