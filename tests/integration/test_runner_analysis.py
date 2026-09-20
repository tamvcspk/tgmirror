"""The count a run makes before it starts (``Runner._analyze``): how many messages it has to look
at, recorded on the run so progress and ``status`` can say "x of y"."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.integration.test_runner import LIMITS, Rig
from tgmirror.core.errors import DailyCapReached, FloodWait, GatewayError
from tgmirror.core.gateway import NO_FILTER, MediaKind, ServerFilter
from tgmirror.filters.model import FilterSpec

PHOTO, VIDEO = MediaKind.PHOTO, MediaKind.VIDEO


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


def photos_only() -> str:
    return FilterSpec.from_data({"include": [{"media": ["photo"]}]}).to_json()


def notices(rig: Rig, code: str) -> list[dict[str, object]]:
    return [params for c, params in rig.recorder.notices if c == code]


def photos_and_videos(rig: Rig, count: int = 6) -> None:
    for i in range(count):
        rig.gw.add_message(rig.src.id, f"m{i}", media=PHOTO if i % 2 else VIDEO)


async def test_the_run_counts_what_it_has_to_look_at_and_records_it(rig: Rig) -> None:
    rig.fill(7)
    store = await rig.store()

    final = await rig.runner(store).run(await rig.begin(store, batch_size=3))

    assert final.options.total_items == 7
    assert notices(rig, "analyzed") == [{"total": 7}]
    assert [c.args[1:] for c in rig.gw.calls_to("count")] == [(0, ServerFilter())]
    # every progress report knows the total, and what was handled adds up to it at the end
    assert {r.options.total_items for r in rig.recorder.runs} == {7}
    assert [r.handled for r in rig.recorder.runs] == [3, 6, 7]


async def test_a_delta_run_counts_only_what_is_new(rig: Rig) -> None:
    rig.fill(5)
    store = await rig.store()
    await rig.runner(store).run(await rig.begin(store))
    rig.fill(3, start=6)

    final = await rig.runner(store).run(await rig.begin(store))

    assert final.options.total_items == 3 and final.done == 3


async def test_the_count_is_capped_by_the_id_span_when_telegram_says_more(rig: Rig) -> None:
    """Telegram may count messages outside the range: the ids seen at the start bound the total."""
    rig.fill(7)
    store = await rig.store()

    async def generous(src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int:
        return 100

    rig.gw.count = generous  # type: ignore[method-assign]
    final = await rig.runner(store).run(await rig.begin(store))

    assert final.options.total_items == 7


async def test_a_filter_telegram_applies_narrows_the_count(rig: Rig) -> None:
    photos_and_videos(rig)
    store = await rig.store()

    final = await rig.runner(store).run(await rig.begin(store, filters_json=photos_only()))

    assert final.options.total_items == 3 and final.done == 3


async def test_a_filter_the_client_applies_leaves_an_upper_bound_that_the_run_meets(
    rig: Rig,
) -> None:
    """Without pushdown the count is everything; what the filter drops is counted as it goes, so
    progress still ends at exactly 100%."""
    photos_and_videos(rig)
    store = await rig.store()

    final = await rig.runner(store).run(
        await rig.begin(store, filters_json=photos_only(), pushdown=False)
    )

    assert final.options.total_items == 6
    assert (final.done, final.skipped_filter, final.handled) == (3, 3, 6)


async def test_a_changed_filter_counts_what_the_pair_already_has_as_handled(rig: Rig) -> None:
    photos_and_videos(rig)
    store = await rig.store()
    await rig.runner(store).run(await rig.begin(store, filters_json=photos_only(), pushdown=False))
    both = FilterSpec.from_data({"include": [{"media": ["photo", "video"]}]}).to_json()

    final = await rig.runner(store).run(await rig.begin(store, filters_json=both, pushdown=False))

    # read again from the start: 6 messages, 3 of them already in the destination
    assert final.options.total_items == 6
    assert (final.done, final.already_done, final.handled) == (3, 3, 6)


async def test_a_daily_cap_that_will_cost_days_is_said_up_front(rig: Rig) -> None:
    rig.fill(7)
    store = await rig.store()
    limits = LIMITS.model_copy(update={"daily_cap": 3})

    with pytest.raises(DailyCapReached):
        await rig.runner(store, limits=limits).run(await rig.begin(store, batch_size=3))

    # 3 today, then 3 and 1 on the next two days
    assert notices(rig, "cap_days") == [{"total": 7, "cap": 3, "days": 2}]


async def test_a_run_that_fits_in_the_day_says_nothing_about_the_cap(rig: Rig) -> None:
    rig.fill(7)
    store = await rig.store()

    await rig.runner(store).run(await rig.begin(store))

    assert notices(rig, "cap_days") == []


async def test_an_error_of_the_analysis_is_not_the_runs(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    rig.gw.fail_next("count", GatewayError("Telegram error: SEARCH_QUERY_EMPTY"))

    final = await rig.runner(store).run(await rig.begin(store))

    assert final.done == 4 and final.options.total_items == 0
    assert notices(rig, "analyzed") == []


async def test_a_flood_wait_on_the_count_is_sat_out_like_any_other(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    rig.gw.fail_next("count", FloodWait(5))

    final = await rig.runner(store).run(await rig.begin(store))

    assert final.options.total_items == 4 and final.done == 4
    assert sum(rig.delays) >= 5  # it waited what Telegram asked (in slices)
    assert len(rig.gw.calls_to("count")) == 2
