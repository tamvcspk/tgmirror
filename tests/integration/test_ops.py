"""``engine/ops.py`` (``tgmirror delete``) end to end on ``FakeGateway`` and a real SQLite file.

What Phase 20 is judged by (docs/06-lo-trinh.md, docs/07-inspect-transform.md section 6): the plan
freezes exactly the messages the filter selects and touches nothing; applying it deletes them and
only them, an album never split; a message that changed since the plan is left alone; a run killed
or cut off in the middle of a batch is settled by reading the chat, so running it again leaves
nothing out and deletes nothing twice.
"""

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeGateway
from tests.integration.test_runner import LIMITS
from tgmirror.core.errors import FloodWait, NoPermission, OpBusy, Transient
from tgmirror.core.gateway import ChannelInfo, MediaKind
from tgmirror.engine.ops import (
    DeletePlan,
    NotAdmin,
    OpNotFound,
    OpRunner,
    OpTiming,
    begin_op,
    plan_delete,
    resolve_op,
)
from tgmirror.engine.runner import RunControl
from tgmirror.filters.model import FilterSpec
from tgmirror.store.db import Store
from tgmirror.store.ops import ItemStatus, Op, OpStatus
from tgmirror.store.runs import Control


class Recorder:
    def __init__(self) -> None:
        self.notices: list[tuple[str, dict[str, object]]] = []
        self.ops: list[Op] = []

    def notice(self, code: str, **params: object) -> None:
        self.notices.append((code, params))

    def progress(self, op: Op) -> None:
        self.ops.append(op)

    @property
    def codes(self) -> list[str]:
        return [c for c, _ in self.notices]


ADS = FilterSpec.from_json('{"include": [{"hashtag": ["#ads"]}]}')


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.gw = FakeGateway()
        self.chat: ChannelInfo = self.gw.add_channel("Chat")
        self.path = tmp_path / "state.db"
        self.recorder = Recorder()
        self._stores: list[Store] = []

    async def store(self) -> Store:
        store = await Store.open(self.path)
        self._stores.append(store)
        return store

    async def sleep(self, seconds: float) -> None:
        pass

    def ids(self) -> list[int]:
        return [m.id for m in self.gw.messages[self.chat.id]]

    def fill(self, ads: int, other: int = 0) -> None:
        for i in range(ads):
            self.gw.add_message(self.chat.id, f"ad {i} #ads", hashtags=("#ads",))
        for i in range(other):
            self.gw.add_message(self.chat.id, f"keep {i}")

    def edit(self, msg_id: int, **changes: Any) -> None:
        """What the chat holds for ``msg_id`` changes (an edit by someone else)."""
        history = self.gw.messages[self.chat.id]
        history[:] = [replace(m, **changes) if m.id == msg_id else m for m in history]

    def runner(
        self,
        store: Store,
        *,
        control: RunControl | None = None,
        wait: bool = False,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> OpRunner:
        return OpRunner(
            store,
            self.gw,
            LIMITS,
            reporter=self.recorder,
            control=control,
            sleep=sleep or self.sleep,
            rng=random.Random(0),
            timing=OpTiming(poll_interval=0.1, heartbeat_interval=3600),
            wait=wait,
        )

    async def plan(self, store: Store, spec: FilterSpec = ADS) -> DeletePlan:
        return await plan_delete(store, self.gw, self.chat, spec, LIMITS, nap=self.sleep)

    async def apply(self, store: Store, op: Op, **kw: Any) -> Op:
        running = await begin_op(store, op.id)
        return await self.runner(store, **kw).run(running)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


# ---- the plan ------------------------------------------------------------------------------


async def test_plan_freezes_what_the_filter_selects_and_touches_nothing(rig: Rig) -> None:
    rig.fill(ads=3, other=2)
    store = await rig.store()

    plan = await rig.plan(store)

    assert plan.op is not None and plan.op.status is OpStatus.PLANNED
    assert plan.op.planned == 3
    assert plan.summary.messages == 3
    assert plan.summary.by_media == {"text": 3}
    assert rig.ids() == [1, 2, 3, 4, 5]  # nothing deleted
    assert rig.gw.calls_to("delete_messages") == []
    planned = await store.op_items_in(plan.op.id, ItemStatus.PLANNED)
    assert [i.msg_id for i in planned] == [1, 2, 3]


async def test_plan_keeps_an_album_whole_under_one_unit(rig: Rig) -> None:
    rig.gw.add_message(rig.chat.id, "x #ads", hashtags=("#ads",))
    album = rig.gw.add_album(rig.chat.id, [MediaKind.PHOTO] * 3, "album #ads")
    store = await rig.store()

    plan = await rig.plan(store, FilterSpec.from_json('{"include": [{"contains": ["#ads"]}]}'))

    assert plan.op is not None
    assert plan.summary.units == 2 and plan.summary.albums == 1
    assert plan.summary.messages == 4
    units = await store.op_units(plan.op.id)
    assert [[i.msg_id for i in unit] for unit in units] == [[1], [m.id for m in album]]


async def test_a_filter_selecting_nothing_makes_no_plan(rig: Rig) -> None:
    rig.fill(ads=0, other=3)
    store = await rig.store()

    plan = await rig.plan(store)

    assert plan.op is None and plan.summary.messages == 0
    assert await store.latest_op() is None


async def test_plan_refuses_a_chat_the_account_does_not_administer(rig: Rig) -> None:
    rig.fill(ads=1)
    rig.gw.channels[rig.chat.id] = replace(rig.chat, is_admin=False)
    store = await rig.store()

    with pytest.raises(NotAdmin):
        await rig.plan(store)

    assert await store.latest_op() is None


async def test_resolve_op_by_number_with_or_without_the_letter(rig: Rig) -> None:
    rig.fill(ads=1)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    assert (await resolve_op(store, str(plan.op.id))).id == plan.op.id
    assert (await resolve_op(store, f"O{plan.op.id}")).id == plan.op.id
    assert (await resolve_op(store, None)).id == plan.op.id
    with pytest.raises(OpNotFound):
        await resolve_op(store, "O99")


# ---- applying ------------------------------------------------------------------------------


async def test_apply_deletes_the_plan_and_only_the_plan(rig: Rig) -> None:
    rig.fill(ads=3, other=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE
    assert final.done == 3 and final.skipped == 0 and final.gone == 0
    assert rig.ids() == [4, 5]
    counts = await store.op_counts(final.id)
    assert counts == {ItemStatus.DONE: 3}


async def test_batches_hold_at_most_100_messages_and_never_split_an_album(rig: Rig) -> None:
    rig.fill(ads=99)
    album = rig.gw.add_album(rig.chat.id, [MediaKind.PHOTO] * 3, "#ads")
    rig.fill(ads=1)
    store = await rig.store()
    plan = await rig.plan(store, FilterSpec.from_json('{"include": [{"contains": ["#ads"]}]}'))
    assert plan.op is not None and plan.op.planned == 103

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and rig.ids() == []
    calls = [list(c.args[1]) for c in rig.gw.calls_to("delete_messages")]  # type: ignore[call-overload]
    # the album did not fit in the first batch: it moved whole to the next one
    assert [len(c) for c in calls] == [99, 3, 1]
    assert calls[1] == [m.id for m in album]


async def test_a_message_that_changed_since_the_plan_is_left_alone(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.edit(2, text="no longer an ad", hashtags=())  # edited after the plan was made

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE
    assert final.done == 2 and final.skipped == 1
    assert rig.ids() == [2]
    skipped = await store.op_items_in(final.id, ItemStatus.SKIPPED)
    assert [(i.msg_id, i.reason) for i in skipped] == [(2, "changed")]


async def test_a_message_deleted_by_someone_else_counts_as_gone(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.delete_message(rig.chat.id, 2)

    final = await rig.apply(store, plan.op)

    assert final.done == 2 and final.gone == 1
    assert rig.ids() == []
    skipped = await store.op_items_in(final.id, ItemStatus.SKIPPED)
    assert [(i.msg_id, i.reason) for i in skipped] == [(2, "gone")]


async def test_an_empty_filter_with_all_selects_every_message(rig: Rig) -> None:
    rig.fill(ads=1, other=2)
    store = await rig.store()

    plan = await rig.plan(store, FilterSpec())

    assert plan.op is not None and plan.op.planned == 3
    final = await rig.apply(store, plan.op)
    assert final.done == 3 and rig.ids() == []


# ---- crash safety --------------------------------------------------------------------------


async def test_a_cut_connection_leaves_the_batch_pending_and_the_next_run_settles_it(
    rig: Rig,
) -> None:
    rig.fill(ads=3, other=1)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    real = rig.gw.delete_messages

    async def deleted_but_cut_off(chat: int, ids: list[int]) -> int:
        await real(chat, ids)  # Telegram did it, the answer never arrived
        raise Transient("connection lost")

    rig.gw.delete_messages = deleted_but_cut_off  # type: ignore[method-assign]
    with pytest.raises(Transient):
        await rig.apply(store, plan.op)
    interrupted = await store.get_op(plan.op.id)
    assert interrupted is not None
    assert interrupted.status is OpStatus.FAILED and interrupted.fail_reason == "transient"
    assert (await store.op_counts(plan.op.id)) == {ItemStatus.PENDING: 3}
    assert rig.ids() == [4]

    rig.gw.delete_messages = real  # type: ignore[method-assign]
    before = len(rig.gw.calls_to("delete_messages"))
    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 3
    assert "settled" in rig.recorder.codes
    assert len(rig.gw.calls_to("delete_messages")) == before  # nothing was deleted twice
    assert rig.ids() == [4]


async def test_a_pending_batch_whose_call_never_went_through_is_deleted_by_the_next_run(
    rig: Rig,
) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("delete_messages", Transient("connection lost"))  # before any side effect

    with pytest.raises(Transient):
        await rig.apply(store, plan.op)
    assert (await store.op_counts(plan.op.id)) == {ItemStatus.PENDING: 3}
    assert rig.ids() == [1, 2, 3]

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 3 and rig.ids() == []
    settled = [p for c, p in rig.recorder.notices if c == "settled"]
    assert settled == [{"deleted": 0, "again": 3}]


async def test_a_flood_wait_that_is_too_long_parks_the_operation_with_nothing_pending(
    rig: Rig,
) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("delete_messages", FloodWait(int(LIMITS.max_auto_wait) + 600))

    with pytest.raises(FloodWait):
        await rig.apply(store, plan.op)

    parked = await store.get_op(plan.op.id)
    assert parked is not None and parked.status is OpStatus.WAITING_FLOOD
    assert parked.resume_at is not None
    assert (await store.op_counts(plan.op.id)) == {ItemStatus.PLANNED: 3}
    events = await store.op_flood_events(plan.op.id)
    assert [(e.kind, e.action) for e in events] == [("flood_wait", "delete")]

    final = await rig.apply(store, plan.op)
    assert final.status is OpStatus.DONE and rig.ids() == []


async def test_a_short_flood_wait_is_slept_out_and_the_call_repeated(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("delete_messages", FloodWait(5))

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 3
    assert "flood_waiting" in rig.recorder.codes
    assert len(rig.gw.calls_to("delete_messages")) == 2  # refused once, then done


async def test_losing_the_rights_midway_fails_the_operation_and_deletes_nothing_more(
    rig: Rig,
) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.channels[rig.chat.id] = replace(rig.chat, is_admin=False)

    with pytest.raises(NoPermission):
        await rig.apply(store, plan.op)

    failed = await store.get_op(plan.op.id)
    assert failed is not None and failed.status is OpStatus.FAILED
    assert (await store.op_counts(plan.op.id)) == {ItemStatus.PLANNED: 3}
    assert rig.ids() == [1, 2, 3]


# ---- control -------------------------------------------------------------------------------


async def test_a_stop_from_the_store_ends_before_the_next_batch_and_resumes_later(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    running = await begin_op(store, plan.op.id)
    await store.set_op_control(running.id, Control.STOP)

    stopped = await rig.runner(store).run(running)

    assert stopped.status is OpStatus.STOPPED and stopped.done == 0 and rig.ids() == [1, 2, 3]

    final = await rig.apply(store, plan.op)
    assert final.status is OpStatus.DONE and rig.ids() == []


async def test_pause_from_the_store_holds_then_resumes(rig: Rig) -> None:
    rig.fill(ads=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    running = await begin_op(store, plan.op.id)
    await store.set_op_control(running.id, Control.PAUSE)

    task = asyncio.create_task(rig.runner(store, control=RunControl()).run(running))
    await asyncio.sleep(0.05)
    live = await store.get_op(running.id)
    assert live is not None and live.status is OpStatus.PAUSED

    await store.set_op_control(running.id, Control.NONE)
    final = await asyncio.wait_for(task, 5)

    assert final.status is OpStatus.DONE
    assert "paused" in rig.recorder.codes and "resumed" in rig.recorder.codes


async def test_a_crash_closes_the_operation_failed_instead_of_leaving_it_running(rig: Rig) -> None:
    class Crash(BaseException):
        pass

    rig.fill(ads=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("delete_messages", Crash())  # type: ignore[arg-type]

    with pytest.raises(Crash):
        await rig.apply(store, plan.op)

    closed = await store.get_op(plan.op.id)
    assert closed is not None and closed.status is OpStatus.FAILED
    assert closed.fail_reason is not None and closed.fail_reason.startswith("crashed")


# ---- one process per chat ------------------------------------------------------------------


async def test_a_second_process_on_the_same_chat_is_refused_until_taken_over(rig: Rig) -> None:
    rig.fill(ads=2)
    store = await rig.store()
    first = (await rig.plan(store)).op
    assert first is not None
    await begin_op(store, first.id)  # a live, beating row

    second = (await rig.plan(store)).op
    assert second is not None
    with pytest.raises(OpBusy):
        await begin_op(store, second.id)

    taken = await begin_op(store, second.id, force=True)
    assert taken.status is OpStatus.RUNNING
    closed = await store.get_op(first.id)
    assert closed is not None and closed.fail_reason == "taken_over"


async def test_a_finished_operation_cannot_be_taken_up_again(rig: Rig) -> None:
    rig.fill(ads=1)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    await rig.apply(store, plan.op)

    with pytest.raises(Exception, match="cannot be taken up"):
        await begin_op(store, plan.op.id)
