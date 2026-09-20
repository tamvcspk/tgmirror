"""``tgmirror retry`` end to end on ``FakeGateway`` and a real SQLite file: no network.

A retry is a run that sends only the messages a run left ``failed`` (docs/04-state-checkpoint.md,
"Retry"). What matters here: it sends exactly those, once, without scanning the source or moving
the cursor; an album stays whole; a message deleted meanwhile is set aside; and a retry that is
killed or refused never loses a failed message (they are below the cursor, so nothing else would
read them again).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import Crash, Rig
from tgmirror.core.errors import FloodWait
from tgmirror.core.gateway import MediaKind
from tgmirror.engine.runner import RunControl, RunnerTiming
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store.db import Store, utc_now
from tgmirror.store.runs import Run, RunStatus


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


async def clone_with_failures(rig: Rig, store: Store, count: int = 5, *bad: int) -> Run:
    """A first run over ``count`` messages in which the ``bad`` ones fail (Telegram refuses)."""
    rig.fill(count)
    for msg_id in bad:
        rig.gw.poison(rig.src.id, msg_id)
    return await rig.runner(store).run(await rig.begin(store, batch_size=count))


async def begin_retry(
    rig: Rig, store: Store, of: Run, *, force: bool = False, after_wait: bool = False
) -> Run:
    request = RunRequest(batch_size=rig.batch_size, retry_of=of.id, force=force)
    clock = (lambda: datetime(2100, 1, 1, tzinfo=UTC)) if after_wait else utc_now
    return (await begin_run(store, rig.gw, rig.src, rig.dst, request, clock=clock)).run


def heal(rig: Rig, *ids: int) -> None:
    for msg_id in ids:
        rig.gw.heal(rig.src.id, msg_id)


# ---- what a retry sends ---------------------------------------------------------------------


async def test_a_retry_sends_only_what_failed_and_does_not_scan_the_source(rig: Rig) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 5, 3)
    assert rig.dst_texts == ["m1", "m2", "m4", "m5"] and first.stats == {"done": 4, "failed": 1}
    heal(rig, 3)
    calls_before = len(rig.gw.calls)  # the retry starts here: begin_run is part of it

    retry = await begin_retry(rig, store, first)
    final = await rig.runner(store).run(retry)

    assert rig.dst_texts == ["m1", "m2", "m4", "m5", "m3"]  # nothing twice; the late one at the end
    # the pair is known, so begin_run reads nothing but the failed ids and one copy call
    assert [c.method for c in rig.gw.calls[calls_before:]] == ["get_messages", "copy_messages"]
    assert rig.gw.calls_to("get_messages")[0].args[1] == [3]
    assert (final.status, final.stats) == (RunStatus.DONE, {"done": 1, "failed": 0})
    assert final.options.retry_of == first.id
    assert (final.cursor_from, final.cursor_src_id) == (5, 5)  # the source cursor did not move
    assert await store.count_failed(first.id) == 0 and await store.count_failed(final.id) == 0


async def test_a_retry_of_a_run_that_failed_nothing_is_a_run_with_nothing_to_send(
    rig: Rig,
) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 3)

    final = await rig.runner(store).run(await begin_retry(rig, store, first))

    assert final.status is RunStatus.DONE and final.stats == {}
    assert len(rig.gw.calls_to("copy_messages")) == 1  # only the first run's


async def test_a_message_that_fails_again_belongs_to_the_retry_and_can_be_retried_again(
    rig: Rig,
) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 4, 2)

    second = await rig.runner(store).run(await begin_retry(rig, store, first))  # still refused

    assert second.stats == {"done": 0, "failed": 1}
    assert await store.count_failed(first.id) == 0 and await store.count_failed(second.id) == 1
    heal(rig, 2)
    third = await rig.runner(store).run(await begin_retry(rig, store, second))

    assert third.stats == {"done": 1, "failed": 0}
    assert rig.dst_texts == ["m1", "m3", "m4", "m2"]


async def test_a_retried_album_goes_out_whole_and_stays_an_album(rig: Rig) -> None:
    gw = rig.gw
    gw.add_message(rig.src.id, "a")  # 1
    gw.add_album(rig.src.id, [MediaKind.PHOTO] * 3, caption="album")  # 2 3 4
    gw.add_message(rig.src.id, "b")  # 5
    gw.poison(rig.src.id, 3)
    store = await rig.store()
    first = await rig.runner(store).run(await rig.begin(store, batch_size=5))
    assert first.stats == {"done": 2, "failed": 3}  # the album failed as a whole
    heal(rig, 3)

    await rig.runner(store).run(await begin_retry(rig, store, first))

    assert rig.copy_calls()[-1] == [2, 3, 4]
    groups = [m.grouped_id for m in gw.messages[rig.dst.id]][-3:]
    assert groups[0] == groups[1] == groups[2] is not None


async def test_a_retry_does_not_apply_the_filter_of_the_pair(rig: Rig) -> None:
    """The failed messages passed the filter when first read; the filter may differ now."""
    store = await rig.store()
    first = await clone_with_failures(rig, store, 3, 2)
    heal(rig, 2)
    await rig.begin(store, filters_json='{"include": [{"contains": ["nothing matches this"]}]}')

    retry = await begin_retry(rig, store, first, force=True)  # the filter run above is left open
    final = await rig.runner(store).run(retry)

    assert final.stats == {"done": 1, "failed": 0} and rig.dst_texts[-1] == "m2"


# ---- deleted meanwhile ----------------------------------------------------------------------


async def test_a_failed_message_deleted_from_the_source_is_set_aside_not_retried(rig: Rig) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 4, 2, 3)
    rig.gw.delete_message(rig.src.id, 2)
    heal(rig, 2, 3)

    final = await rig.runner(store).run(await begin_retry(rig, store, first))

    assert final.status is RunStatus.DONE
    assert final.stats == {"done": 1, "failed": 0, "gone": 1}
    assert rig.copy_calls()[-1] == [3]  # only the message that still exists
    assert await store.count_failed(first.id) == 0  # 2 is settled: nothing left to retry
    assert await store.run_failures(final.id) == []


# ---- a retry that does not finish loses nothing ----------------------------------------------


@pytest.mark.parametrize("crash_at", ["before_copy", "after_copy"])
async def test_a_retry_killed_mid_batch_is_repaired_by_the_next_run(
    rig: Rig, crash_at: str
) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 5, 3, 4)
    heal(rig, 3, 4)
    retry = await begin_retry(rig, store, first)

    if crash_at == "before_copy":

        async def die(ids: list[int], n: int) -> None:
            raise Crash

        rig.wrap_copy(die)
    else:

        async def die_on_commit(*args: Any, **kwargs: Any) -> Run:
            raise Crash  # after Telegram made the copies, before the result is saved

        store.commit_batch = die_on_commit  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await rig.runner(store).run(retry)
    await store.close()
    if crash_at == "before_copy":
        rig.unwrap_copy()

    resumed = await rig.store()
    assert len(await resumed.pending_rows(retry.id)) == 2  # the two failed messages, in flight
    final = await rig.runner(resumed).run(await begin_retry(rig, resumed, first, force=True))

    assert await resumed.pending_rows(retry.id) == []
    assert rig.dst_texts.count("m3") == 1 and rig.dst_texts.count("m4") == 1  # once each
    assert await resumed.count_failed(first.id) == 0
    assert final.status is RunStatus.DONE


async def test_a_retry_refused_by_a_long_flood_keeps_the_messages_failed(rig: Rig) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 4, 2)
    heal(rig, 2)
    rig.gw.fail_next("copy_messages", FloodWait(10_000))  # longer than max_auto_wait
    retry = await begin_retry(rig, store, first)

    with pytest.raises(FloodWait):
        await rig.runner(store).run(retry)

    parked = await store.get_run(retry.id)
    assert parked is not None and parked.status is RunStatus.WAITING_FLOOD
    assert await store.pending_rows(retry.id) == []
    assert await store.count_failed(first.id) == 1  # not forgotten, not copied: still to retry
    assert rig.dst_texts == ["m1", "m3", "m4"]

    after = await begin_retry(rig, store, first, after_wait=True)  # first's failure is still there
    final = await rig.runner(store).run(after)
    assert final.stats == {"done": 1, "failed": 0} and rig.dst_texts[-1] == "m2"


async def test_stopping_a_retry_before_it_sends_leaves_the_failures_alone(rig: Rig) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 4, 2)
    heal(rig, 2)
    retry = await begin_retry(rig, store, first)
    control = RunControl()
    control.request_stop()

    final = await rig.runner(store, control=control).run(retry)

    assert final.status is RunStatus.STOPPED and final.stats == {}
    assert await store.count_failed(first.id) == 1


# ---- reading the failed messages is rate limited like any read -------------------------------


async def test_a_flood_while_reading_by_id_is_waited_out_and_the_read_repeated(rig: Rig) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 3, 2)
    heal(rig, 2)
    rig.gw.fail_next("get_messages", FloodWait(5))
    retry = await begin_retry(rig, store, first)

    final = await rig.runner(store).run(retry)

    assert final.stats == {"done": 1, "failed": 0}
    assert len(rig.gw.calls_to("get_messages")) == 2
    assert "flood_waiting" in rig.recorder.codes
    assert [e.method for e in await store.flood_events(retry.id)] == ["get_messages"]


# ---- an ordinary run knows the size of the source --------------------------------------------


async def test_a_run_records_the_newest_source_id_and_a_retry_does_not_read_it(rig: Rig) -> None:
    store = await rig.store()
    first = await clone_with_failures(rig, store, 6, 2)
    assert first.options.src_last_id == 6
    heal(rig, 2)
    reads = [c.args for c in rig.gw.calls_to("last_message_id")]

    retry = await begin_retry(rig, store, first)

    assert [c.args for c in rig.gw.calls_to("last_message_id")] == reads  # no extra read
    assert retry.options.src_last_id == 0


async def test_more_failures_than_one_read_holds_are_read_in_paced_chunks(rig: Rig) -> None:
    """101 ids need two reads; an album across the boundary stays whole; reads are spaced."""
    gw = rig.gw
    for _ in range(99):
        gw.add_message(rig.src.id, "x")  # 1..99
    gw.add_album(rig.src.id, [MediaKind.PHOTO] * 3)  # 100 101 102: split by the 100-id read
    gw.add_message(rig.src.id, "y")  # 103
    for msg_id in range(1, 104):
        gw.poison(rig.src.id, msg_id)
    store = await rig.store()
    first = await rig.runner(store).run(await rig.begin(store, batch_size=20))
    assert first.stats == {"done": 0, "failed": 103}
    for msg_id in range(1, 104):
        heal(rig, msg_id)
    pauses_before = len(rig.delays)

    whole_sleeps = RunnerTiming(poll_interval=100.0, heartbeat_interval=3600)  # not sliced
    final = await rig.runner(store, timing=whole_sleeps).run(await begin_retry(rig, store, first))

    assert final.stats == {"done": 103, "failed": 0}
    assert [len(c.args[1]) for c in gw.calls_to("get_messages")] == [100, 3]  # type: ignore[arg-type]
    assert [100, 101, 102] in rig.copy_calls()  # the album, in one call
    assert rig.delays[pauses_before:].count(0.5) == 1  # only the second read waited (read_delay)
