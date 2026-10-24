"""``engine/ops.py``'s ``edit`` and ``undo`` (Phase 20.3) end to end on ``FakeGateway`` and a real
SQLite file.

What the slice is judged by (docs/07-inspect-transform.md sections 6 and 11.4): the plan freezes
only the messages whose text would change, with the text before and after, and touches nothing;
applying edits those and only those; a message somebody else changed since is left alone; a cut
connection mid-edit is settled by reading the texts, so a second go neither skips nor edits twice;
``undo`` puts the originals back, once.
"""

import random
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeGateway
from tests.integration.test_runner import LIMITS
from tgmirror.core.errors import FloodWait, NoPermission, Transient
from tgmirror.core.gateway import CaptionPolicy, ChannelInfo, MediaKind, RemoveHashtags
from tgmirror.engine.ops import (
    EditPlan,
    NotAdmin,
    OpRunner,
    OpTiming,
    UndoError,
    begin_op,
    plan_edit,
    plan_undo,
)
from tgmirror.engine.runner import RunControl
from tgmirror.filters.model import FilterSpec
from tgmirror.store.db import Store
from tgmirror.store.ops import ItemStatus, Op, OpKind, OpStatus

ADS = FilterSpec.from_json('{"include": [{"hashtag": ["#ads"]}]}')
STRIP = CaptionPolicy(ops=(RemoveHashtags(("#ads",)),), text_too=True)


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.gw = FakeGateway()
        self.chat: ChannelInfo = self.gw.add_channel("Chat")
        self.path = tmp_path / "state.db"
        self._stores: list[Store] = []

    async def store(self) -> Store:
        store = await Store.open(self.path)
        self._stores.append(store)
        return store

    async def sleep(self, seconds: float) -> None:
        pass

    def fill(self, ads: int, other: int = 0) -> None:
        for i in range(ads):
            self.gw.add_message(self.chat.id, f"ad {i} #ads", hashtags=("#ads",))
        for i in range(other):
            self.gw.add_message(self.chat.id, f"keep {i}")

    def texts(self) -> dict[int, str]:
        return {m.id: m.text for m in self.gw.messages[self.chat.id]}

    def edit(self, msg_id: int, **changes: Any) -> None:
        history = self.gw.messages[self.chat.id]
        history[:] = [replace(m, **changes) if m.id == msg_id else m for m in history]

    def runner(self, store: Store, control: RunControl | None = None) -> OpRunner:
        return OpRunner(
            store,
            self.gw,
            LIMITS,
            control=control,
            sleep=self.sleep,
            rng=random.Random(0),
            timing=OpTiming(poll_interval=0.1, heartbeat_interval=3600),
        )

    async def plan(self, store: Store, policy: CaptionPolicy = STRIP) -> EditPlan:
        return await plan_edit(store, self.gw, self.chat, ADS, policy, "{}", LIMITS, nap=self.sleep)

    async def apply(self, store: Store, op: Op) -> Op:
        running = await begin_op(store, op.id)
        return await self.runner(store).run(running)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


async def test_plan_freezes_what_would_change_with_both_texts_and_edits_nothing(rig: Rig) -> None:
    rig.fill(ads=3, other=2)
    before = rig.texts()
    store = await rig.store()

    plan = await rig.plan(store)

    assert plan.op is not None and plan.op.kind is OpKind.EDIT
    assert plan.op.status is OpStatus.PLANNED and plan.op.planned == 3
    assert plan.summary.messages == 3 and plan.summary.unchanged == 0
    assert [s.after for s in plan.summary.first] == ["ad 0", "ad 1", "ad 2"]
    assert rig.texts() == before  # nothing edited
    assert rig.gw.calls_to("edit_message") == []
    items = await store.op_items_in(plan.op.id, ItemStatus.PLANNED)
    assert items[0].texts == ("ad 0 #ads", "ad 0")


async def test_a_message_the_transform_leaves_as_it_is_is_counted_not_planned(rig: Rig) -> None:
    rig.fill(ads=2)
    rig.gw.add_message(rig.chat.id, "photo #ads", media=MediaKind.PHOTO, hashtags=("#ads",))
    store = await rig.store()

    plan = await rig.plan(store, CaptionPolicy(ops=(RemoveHashtags(("#other",)),), text_too=True))

    assert plan.op is None  # no row for a plan that would change nothing
    assert plan.summary.unchanged == 3


async def test_plain_text_is_only_edited_when_the_transform_says_so(rig: Rig) -> None:
    rig.fill(ads=2)
    rig.gw.add_message(rig.chat.id, "photo #ads", media=MediaKind.PHOTO, hashtags=("#ads",))
    store = await rig.store()

    plan = await rig.plan(store, CaptionPolicy(ops=(RemoveHashtags(("#ads",)),)))  # no text_too

    assert plan.op is not None and plan.op.planned == 1  # only the photo's caption
    assert plan.summary.unchanged == 2


async def test_applying_edits_exactly_the_planned_messages(rig: Rig) -> None:
    rig.fill(ads=3, other=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 3
    assert rig.texts() == {1: "ad 0", 2: "ad 1", 3: "ad 2", 4: "keep 0", 5: "keep 1"}
    assert len(rig.gw.calls_to("edit_message")) == 3


async def test_a_message_changed_since_the_plan_is_left_alone(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.edit(2, text="ad 1 #ads (edited by someone)")

    final = await rig.apply(store, plan.op)

    assert final.done == 2 and final.skipped == 1
    assert rig.texts()[2] == "ad 1 #ads (edited by someone)"


async def test_a_message_gone_since_the_plan_is_counted(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    history = rig.gw.messages[rig.chat.id]
    history[:] = [m for m in history if m.id != 3]

    final = await rig.apply(store, plan.op)

    assert final.done == 2 and final.gone == 1


async def test_a_message_telegram_will_not_let_us_edit_fails_alone(rig: Rig) -> None:
    rig.fill(ads=3)
    rig.gw.uneditable.add((rig.chat.id, 2))
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 2 and final.failed == 1
    failed = await store.op_items_in(plan.op.id, ItemStatus.FAILED)
    assert [i.msg_id for i in failed] == [2] and failed[0].reason == "MESSAGE_AUTHOR_REQUIRED"


async def test_formatting_that_cannot_be_written_back_is_never_edited(rig: Rig) -> None:
    rig.fill(ads=3)
    rig.gw.lossy.add((rig.chat.id, 2))
    store = await rig.store()

    plan = await rig.plan(store)

    assert plan.op is not None and plan.op.planned == 2
    assert plan.summary.lossy == 1
    items = await store.op_items_in(plan.op.id, ItemStatus.PLANNED)
    assert [i.msg_id for i in items] == [1, 3]


async def test_the_account_must_administer_the_chat(rig: Rig) -> None:
    rig.fill(ads=2)
    rig.gw.channels[rig.chat.id] = replace(rig.chat, is_admin=False)
    store = await rig.store()

    with pytest.raises(NotAdmin):
        await rig.plan(store)


async def test_a_cut_connection_mid_edit_is_settled_by_reading_the_texts(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("edit_message", Transient("cut"))
    running = await begin_op(store, plan.op.id)

    with pytest.raises(Transient):
        await rig.runner(store).run(running)

    pending = await store.op_items_in(plan.op.id, ItemStatus.PENDING)
    assert [i.msg_id for i in pending] == [1]  # the call may or may not have gone through
    final = await rig.apply(store, (await store.get_op(plan.op.id)))  # type: ignore[arg-type]
    assert final.status is OpStatus.DONE and final.done == 3
    assert rig.texts() == {1: "ad 0", 2: "ad 1", 3: "ad 2"}
    assert len(rig.gw.calls_to("edit_message")) == 4  # one failed call, then all three


async def test_an_edit_that_went_through_before_the_cut_is_not_made_twice(rig: Rig) -> None:
    rig.fill(ads=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    running = await begin_op(store, plan.op.id)
    await store.mark_op_items(running.id, [1], ItemStatus.PENDING)  # written ahead ...
    await rig.gw.edit_message(rig.chat.id, 1, "ad 0")  # ... and the call went through

    final = await rig.runner(store).run(running)

    assert final.status is OpStatus.DONE and final.done == 2
    assert len(rig.gw.calls_to("edit_message")) == 2  # the manual one, plus message 2 only


async def test_a_flood_wait_parks_the_edit_and_nothing_is_lost(rig: Rig) -> None:
    rig.fill(ads=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("edit_message", FloodWait(7200))
    running = await begin_op(store, plan.op.id)

    with pytest.raises(FloodWait):
        await rig.runner(store).run(running)

    parked = await store.get_op(plan.op.id)
    assert parked is not None and parked.status is OpStatus.WAITING_FLOOD
    assert len(await store.op_items_in(plan.op.id, ItemStatus.PLANNED)) == 2
    final = await rig.apply(store, parked)
    assert final.done == 2


async def test_stop_before_the_first_batch_edits_nothing(rig: Rig) -> None:
    rig.fill(ads=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    control = RunControl()
    control.request_stop()
    running = await begin_op(store, plan.op.id)

    final = await rig.runner(store, control).run(running)

    assert final.status is OpStatus.STOPPED and rig.texts() == {1: "ad 0 #ads", 2: "ad 1 #ads"}


async def test_no_permission_stops_the_edit_with_the_plan_intact(rig: Rig) -> None:
    rig.fill(ads=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("edit_message", NoPermission("CHAT_ADMIN_REQUIRED"))
    running = await begin_op(store, plan.op.id)

    with pytest.raises(NoPermission):
        await rig.runner(store).run(running)

    assert len(await store.op_items_in(plan.op.id, ItemStatus.PLANNED)) == 2


# ---- undo ----------------------------------------------------------------------------------


async def test_undo_puts_the_originals_back_and_only_once(rig: Rig) -> None:
    rig.fill(ads=3, other=1)
    original = rig.texts()
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    edited = await rig.apply(store, plan.op)

    undo = await plan_undo(store, rig.gw, edited)

    assert undo.op.kind is OpKind.UNDO and undo.op.undo_of == edited.id and undo.op.planned == 3
    assert rig.texts() != original  # planning changed nothing
    final = await rig.apply(store, undo.op)
    assert final.status is OpStatus.DONE and final.done == 3
    assert rig.texts() == original
    with pytest.raises(UndoError) as again:
        await plan_undo(store, rig.gw, edited)
    assert again.value.key == "undo.already"


async def test_undo_leaves_alone_what_somebody_edited_after_the_edit(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    edited = await rig.apply(store, plan.op)
    rig.edit(2, text="rewritten by hand")

    undo = await plan_undo(store, rig.gw, edited)
    final = await rig.apply(store, undo.op)

    assert final.done == 2 and final.skipped == 1
    assert rig.texts() == {1: "ad 0 #ads", 2: "rewritten by hand", 3: "ad 2 #ads"}


async def test_only_an_edit_that_was_applied_can_be_undone(rig: Rig) -> None:
    rig.fill(ads=2)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    with pytest.raises(UndoError) as planned:
        await plan_undo(store, rig.gw, plan.op)
    assert planned.value.key == "undo.never_applied"

    from tgmirror.engine.ops import plan_delete

    deleted = await plan_delete(store, rig.gw, rig.chat, ADS, LIMITS, nap=rig.sleep)
    assert deleted.op is not None
    with pytest.raises(UndoError) as wrong:
        await plan_undo(store, rig.gw, deleted.op)
    assert wrong.value.key == "undo.not_an_edit"


async def test_an_edit_that_changed_nothing_has_nothing_to_undo(rig: Rig) -> None:
    rig.fill(ads=2)
    rig.gw.uneditable.update({(rig.chat.id, 1), (rig.chat.id, 2)})
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    edited = await rig.apply(store, plan.op)
    assert edited.failed == 2

    with pytest.raises(UndoError) as nothing:
        await plan_undo(store, rig.gw, edited)
    assert nothing.value.key == "undo.nothing"
