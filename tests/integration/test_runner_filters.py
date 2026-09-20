"""The runner with a filter: only matching units are copied, and the state stays exact.

Same rig as ``test_runner.py`` (``FakeGateway`` and a real SQLite file). What matters here: the
cursor passes filtered-out messages, ``skipped_filter`` counts them once, a kill at any step still
resumes without a gap or a duplicate, pause/stop get through a long stretch without matches, and
``--refilter`` backfills without copying anything twice.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import Crash, Rig
from tgmirror.core.errors import JobBusy
from tgmirror.core.gateway import MediaKind, ServerFilter
from tgmirror.engine.batcher import FLUSH_AFTER
from tgmirror.engine.endpoints import Endpoints
from tgmirror.engine.jobs import NewJob, create_job
from tgmirror.engine.runner import StopSignal
from tgmirror.filters.model import FilterError, FilterSpec
from tgmirror.store.db import Store
from tgmirror.store.jobs import Job, JobSpec, JobStatus

PHOTO, VIDEO = MediaKind.PHOTO, MediaKind.VIDEO


def spec(**data: Any) -> FilterSpec:
    return FilterSpec.from_data(data)


class FilterRig(Rig):
    async def job(  # type: ignore[override]
        self,
        store: Store,
        batch_size: int = 3,
        *,
        filters: FilterSpec | None = None,
        pushdown: bool = True,
    ) -> Job:
        endpoints = Endpoints(self.src, self.dst, created=False)
        new = NewJob(batch_size=batch_size, filters=filters or FilterSpec(), pushdown=pushdown)
        return await create_job(store, self.gw, endpoints, new)

    def tagged(self, count: int) -> None:
        """``count`` messages; the odd ones (1, 3, 5, ...) carry the hashtag ``#k``."""
        for i in range(1, count + 1):
            tag = i % 2 == 1
            self.gw.add_message(
                self.src.id, f"m{i} #k" if tag else f"m{i}", hashtags=("#k",) if tag else ()
            )


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[FilterRig]:
    r = FilterRig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


HASHTAG = spec(include=[{"hashtag": ["#k"]}])


# ---- what gets copied -----------------------------------------------------------------------


@pytest.mark.parametrize("pushdown", [True, False])
async def test_only_matching_messages_are_copied_with_or_without_pushdown(
    rig: FilterRig, pushdown: bool
) -> None:
    rig.tagged(6)
    store = await rig.store()
    job = await rig.job(store, filters=HASHTAG, pushdown=pushdown)

    final = await rig.runner(store).run(job.id)

    assert rig.dst_texts == ["m1 #k", "m3 #k", "m5 #k"]
    assert final.status is JobStatus.DONE and final.done == 3


async def test_a_client_side_scan_counts_what_it_dropped_and_moves_the_cursor_to_the_end(
    rig: FilterRig,
) -> None:
    rig.tagged(6)
    store = await rig.store()
    job = await rig.job(store, filters=HASHTAG, pushdown=False)

    final = await rig.runner(store).run(job.id)

    assert final.stats == {"done": 3, "failed": 0, "skipped_filter": 3}
    assert final.cursor_src_id == 6  # past the trailing message the filter dropped
    assert rig.copy_calls() == [[1, 3, 5]]  # one batch: the dropped ones did not split it


async def test_pushdown_lets_telegram_drop_them_so_they_are_not_counted(rig: FilterRig) -> None:
    rig.tagged(6)
    store = await rig.store()
    job = await rig.job(store, filters=HASHTAG)

    final = await rig.runner(store).run(job.id)

    assert final.stats == {"done": 3, "failed": 0} and final.cursor_src_id == 5
    first_read = rig.gw.calls_to("iter_messages")[0]
    assert first_read.args[2] == ServerFilter(search="#k")


async def test_nothing_matching_finishes_without_sending_or_waiting(rig: FilterRig) -> None:
    for i in range(5):
        rig.gw.add_message(rig.src.id, f"plain {i}")
    store = await rig.store()
    job = await rig.job(store, filters=spec(include=[{"regex": "nope"}]))

    final = await rig.runner(store).run(job.id)

    assert final.status is JobStatus.DONE
    assert (final.done, final.skipped_filter, final.cursor_src_id) == (0, 5, 5)
    assert rig.copy_calls() == [] and rig.delays == []


async def test_a_filtered_job_that_is_run_again_reads_only_what_is_new(rig: FilterRig) -> None:
    rig.tagged(4)
    store = await rig.store()
    job = await rig.job(store, filters=HASHTAG, pushdown=False)
    await rig.runner(store).run(job.id)
    rig.gw.add_message(rig.src.id, "m5 #k", hashtags=("#k",))
    rig.gw.add_message(rig.src.id, "m6")

    final = await rig.runner(store).run(job.id)

    assert rig.dst_texts == ["m1 #k", "m3 #k", "m5 #k"]
    assert (final.done, final.skipped_filter, final.cursor_src_id) == (3, 3, 6)


async def test_an_album_chosen_by_the_hashtag_on_its_first_member_is_copied_whole(
    rig: FilterRig,
) -> None:
    gw, src = rig.gw, rig.src.id
    gw.add_message(src, "intro")  # 1
    gid = gw._alloc_group()
    gw.add_message(src, "album #k", media=PHOTO, grouped_id=gid, hashtags=("#k",))  # 2
    gw.add_message(src, "", media=VIDEO, grouped_id=gid)  # 3: the server search leaves it out
    gw.add_message(src, "", media=PHOTO, grouped_id=gid)  # 4
    gw.add_message(src, "outro")  # 5
    store = await rig.store()
    job = await rig.job(store, filters=HASHTAG)

    await rig.runner(store).run(job.id)

    assert rig.copy_calls() == [[2, 3, 4]]
    groups = {m.grouped_id for m in gw.messages[rig.dst.id]}
    assert len(gw.messages[rig.dst.id]) == 3 and len(groups) == 1 and None not in groups


async def test_a_filter_error_at_run_time_fails_the_job_with_the_reason(rig: FilterRig) -> None:
    store = await rig.store()
    broken = await store.create_job(
        JobSpec(name="broken", src=rig.src, dst=rig.dst, filters_json="{not json")
    )

    with pytest.raises(FilterError):
        await rig.runner(store).run(broken.id)

    saved = await store.get_job(broken.id)
    assert saved is not None and saved.status is JobStatus.FAILED
    assert saved.fail_reason is not None and saved.fail_reason.startswith("FilterError")


async def test_a_message_the_server_rejects_does_not_lose_the_skip_count(rig: FilterRig) -> None:
    """The PerMessage path resends unit by unit: the filter count must still be committed once."""
    rig.tagged(6)  # tagged: 1 3 5, dropped: 2 4 6
    rig.gw.poison(rig.src.id, 3)
    store = await rig.store()
    job = await rig.job(store, filters=HASHTAG, pushdown=False)

    final = await rig.runner(store).run(job.id)

    assert rig.dst_texts == ["m1 #k", "m5 #k"]
    assert (final.done, final.failed, final.skipped_filter) == (2, 1, 3)
    assert final.cursor_src_id == 6


# ---- kill the runner --------------------------------------------------------------------------


@pytest.mark.parametrize("crash_at", ["before_copy", "after_copy"])
@pytest.mark.parametrize("batch_number", [1, 2])
async def test_kill_then_resume_with_a_filter_leaves_no_gap_and_no_duplicate(
    rig: FilterRig, crash_at: str, batch_number: int
) -> None:
    rig.tagged(12)  # six tagged messages, batches of two units: three batches
    store = await rig.store()
    job = await rig.job(store, batch_size=2, filters=HASHTAG, pushdown=False)

    if crash_at == "before_copy":

        async def die(ids: list[int], n: int) -> None:
            if n == batch_number:
                raise Crash

        rig.wrap_copy(die)
    else:
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

    final = await rig.runner(await rig.store()).run(job.id, force_takeover=True)

    assert final.status is JobStatus.DONE
    assert rig.dst_texts == [f"m{i} #k" for i in (1, 3, 5, 7, 9, 11)]  # each exactly once, in order
    assert final.done == 6 and final.cursor_src_id == 12


# ---- pause and stop ---------------------------------------------------------------------------


async def test_pause_gets_through_a_long_stretch_without_matches_and_resume_counts_once(
    rig: FilterRig,
) -> None:
    noise = 2 * FLUSH_AFTER + 200
    for _ in range(noise):
        rig.gw.add_message(rig.src.id, "noise")
    rig.gw.add_message(rig.src.id, "keep this")
    store = await rig.store()
    job = await rig.job(store, filters=spec(include=[{"regex": "keep"}]))
    stop = StopSignal()
    original = store.advance_cursor

    async def stop_after_the_first_flush(*args: Any, **kwargs: Any) -> Job:
        result = await original(*args, **kwargs)
        stop.request()
        return result

    store.advance_cursor = stop_after_the_first_flush  # type: ignore[method-assign]
    paused = await rig.runner(store, stop=stop).run(job.id)

    # it did not read to the end before noticing: the batcher flushes every FLUSH_AFTER skips
    assert paused.status is JobStatus.STOPPED
    assert (paused.cursor_src_id, paused.skipped_filter) == (FLUSH_AFTER, FLUSH_AFTER)
    assert rig.dst_texts == []

    del store.advance_cursor
    final = await rig.runner(store).run(job.id)

    assert rig.dst_texts == ["keep this"]
    assert final.skipped_filter == noise  # counted once, not again for the rescanned part
    assert final.cursor_src_id == noise + 1


# ---- changing the filter (run --refilter) -----------------------------------------------------


async def test_a_new_filter_backfills_without_copying_anything_twice(rig: FilterRig) -> None:
    for i in range(1, 7):
        rig.gw.add_message(rig.src.id, f"m{i}", media=PHOTO if i % 2 else VIDEO)
    store = await rig.store()
    job = await rig.job(store, filters=spec(include=[{"media": ["photo"]}]), pushdown=False)
    first = await rig.runner(store).run(job.id)
    assert rig.dst_texts == ["m1", "m3", "m5"] and first.skipped_filter == 3

    reset = await store.replace_filters(
        job.id, spec(include=[{"media": ["photo", "video"]}]).to_json()
    )
    assert (reset.cursor_src_id, reset.skipped_filter, reset.done) == (0, 0, 3)

    final = await rig.runner(store).run(job.id)

    assert rig.dst_texts == ["m1", "m3", "m5", "m2", "m4", "m6"]  # the backfill goes at the end
    assert (final.done, final.skipped_filter, final.cursor_src_id) == (6, 0, 6)


async def test_the_filter_of_a_running_job_cannot_be_replaced(rig: FilterRig) -> None:
    store = await rig.store()
    job = await rig.job(store)
    await store.claim(job.id)  # another process holds it, heartbeat fresh

    with pytest.raises(JobBusy):
        await store.replace_filters(job.id, FilterSpec().to_json())
