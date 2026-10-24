"""``engine/topicops.py`` and ``engine/ops.py``'s ``move`` (Phase 20b) end to end on ``FakeGateway``
and a real SQLite file: a split or merge survives a crash anywhere without a gap, a second copy, a
second topic, or the deletion of a message that has no copy; a topic goes only when it is empty.
"""

import random
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.fakes import FakeGateway
from tests.integration.test_runner import LIMITS
from tgmirror.core.errors import Transient
from tgmirror.core.gateway import CaptionPolicy, ChannelInfo, ChatKind, Unit
from tgmirror.engine.ops import OpRunner, OpTiming, begin_op
from tgmirror.engine.topicops import plan_merge, plan_split
from tgmirror.engine.transform import Transform
from tgmirror.engine.transform import load as transform_load
from tgmirror.store.db import Store
from tgmirror.store.ops import ItemStatus, Op, OpKind, OpStatus


def route_to(tmp_path: Path, title: str) -> Transform:
    """Every message with the hashtag ``#b`` goes to the topic ``title``."""
    path = tmp_path / "route.yaml"
    path.write_text(
        f"topics:\n  route:\n    - {{when: {{hashtag: ['#b']}}, to: '{title}'}}\n",
        encoding="utf-8",
    )
    return transform_load(path)


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.gw = FakeGateway()
        self.chat: ChannelInfo = self.gw.add_channel("Forum", kind=ChatKind.FORUM)
        self.misc = self.gw.add_topic(self.chat.id, "Misc").id
        self.old = self.gw.add_topic(self.chat.id, "Old").id
        self.path = tmp_path / "state.db"
        self.tmp = tmp_path / "tmp"
        self._stores: list[Store] = []

    async def store(self) -> Store:
        store = await Store.open(self.path)
        self._stores.append(store)
        return store

    async def sleep(self, seconds: float) -> None:
        pass

    def add(self, text: str, topic: int) -> int:
        tags = ("#b",) if "#b" in text else ()
        return self.gw.add_message(self.chat.id, text, topic_id=topic, hashtags=tags).id

    def texts(self, topic: int | None) -> list[str]:
        return [m.text for m in self.gw.messages[self.chat.id] if m.topic_id == topic]

    def topic(self, title: str) -> int | None:
        return next((t.id for t in self.gw.topics[self.chat.id] if t.title == title), None)

    def runner(self, store: Store) -> OpRunner:
        return OpRunner(
            store,
            self.gw,
            LIMITS,
            sleep=self.sleep,
            rng=random.Random(0),
            timing=OpTiming(poll_interval=0.1, heartbeat_interval=3600),
            tmp_dir=self.tmp,
        )

    async def apply(self, store: Store, op: Op) -> Op:
        running = await begin_op(store, op.id, gateway=self.gw)
        return await self.runner(store).run(running)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


async def test_a_send_cut_after_the_topic_was_made_reuses_that_topic(
    rig: Rig, tmp_path: Path
) -> None:
    rig.add("m1", rig.misc)
    rig.add("b1 #b", rig.misc)
    store = await rig.store()
    plan = await plan_split(
        store, rig.gw, rig.chat, topic_of(rig, "Misc"), route_to(tmp_path, "B"), LIMITS
    )
    assert plan.op is not None
    rig.gw.fail_next("send_prepared", Transient("cut"))
    running = await begin_op(store, plan.op.id, gateway=rig.gw)

    with pytest.raises(Transient):
        await rig.runner(store).run(running)
    assert rig.topic("B") is not None  # made before the send that was cut
    assert [i.msg_id for i in await store.op_items_in(plan.op.id, ItemStatus.PENDING)] == [2]

    again = await store.get_op(plan.op.id)
    assert again is not None
    final = await rig.apply(store, again)

    assert final.status is OpStatus.DONE and final.done == 1
    assert [t.title for t in rig.gw.topics[rig.chat.id]].count("B") == 1
    assert rig.texts(rig.topic("B")) == ["b1 #b"]
    assert rig.texts(rig.misc) == ["m1"]


async def test_a_send_that_went_through_before_the_cut_is_not_posted_twice_nor_lost(
    rig: Rig,
) -> None:
    rig.add("o1", rig.old)
    rig.add("o2", rig.old)
    rig.add("m1", rig.misc)
    store = await rig.store()
    plan = await plan_merge(store, rig.gw, rig.chat, ["Misc", "Old"], LIMITS)
    assert plan.op is not None and plan.op.kind is OpKind.MOVE
    running = await begin_op(store, plan.op.id, gateway=rig.gw)
    # the first unit went out (into Misc) but the process died before it wrote that down
    tail = await rig.gw.last_message_id(rig.chat.id)
    note = f'{{"topic": {rig.misc}, "tail": {tail}, "dest": "Misc", "dest_id": {rig.misc}}}'
    await store.mark_op_items(running.id, [1], ItemStatus.PENDING, before_json=note)
    unit = (await rig.gw.get_messages(rig.chat.id, [1]))[0]
    prepared = await rig.gw.prepare(rig.chat.id, Unit((unit,)), rig.tmp)
    await rig.gw.send_prepared(rig.chat.id, prepared, CaptionPolicy(), topic=rig.misc)

    final = await rig.runner(store).run(running)

    assert final.status is OpStatus.DONE and final.done == 2
    assert rig.texts(rig.misc) == ["m1", "o1", "o2"]  # o1 once, in order
    assert rig.topic("Old") is None  # emptied, so deleted


async def test_a_crash_after_the_last_move_still_deletes_the_emptied_topic(rig: Rig) -> None:
    rig.add("o1", rig.old)
    store = await rig.store()
    plan = await plan_merge(store, rig.gw, rig.chat, ["Misc", "Old"], LIMITS)
    assert plan.op is not None
    rig.gw.fail_next("delete_topic", Transient("cut"))
    running = await begin_op(store, plan.op.id, gateway=rig.gw)

    with pytest.raises(Transient):
        await rig.runner(store).run(running)
    assert (
        rig.texts(rig.misc) == ["o1"] and rig.topic("Old") is not None
    )  # moved, topic still there

    again = await store.get_op(plan.op.id)
    assert again is not None
    final = await rig.apply(store, again)

    assert final.status is OpStatus.DONE and final.topics_dropped == 1
    assert rig.topic("Old") is None and rig.texts(rig.misc) == ["o1"]  # not posted again


async def test_a_message_posted_into_the_topic_meanwhile_keeps_it(rig: Rig) -> None:
    rig.add("o1", rig.old)
    store = await rig.store()
    plan = await plan_merge(store, rig.gw, rig.chat, ["Misc", "Old"], LIMITS)
    assert plan.op is not None
    rig.add("late", rig.old)  # not in the plan

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 1
    assert final.topics_kept == 1 and final.topics_dropped == 0
    assert rig.texts(rig.old) == ["late"] and rig.topic("Old") is not None


async def test_albums_move_whole_and_in_order(rig: Rig) -> None:
    from tgmirror.core.gateway import MediaKind

    rig.add("o1", rig.old)
    album = rig.gw.add_album(rig.chat.id, [MediaKind.PHOTO] * 3, "cap", topic_id=rig.old)
    rig.add("o2", rig.old)
    store = await rig.store()
    plan = await plan_merge(store, rig.gw, rig.chat, ["Misc", "Old"], LIMITS)
    assert plan.op is not None and plan.summary.albums == 1

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 5
    moved = [m for m in rig.gw.messages[rig.chat.id] if m.topic_id == rig.misc]
    assert [m.text for m in moved] == ["o1", "cap", "", "", "o2"]
    assert len({m.grouped_id for m in moved[1:4]}) == 1 and moved[1].grouped_id is not None
    assert album[0].id not in [m.id for m in rig.gw.messages[rig.chat.id]]


def topic_of(rig: Rig, title: str):  # noqa: ANN201 - a TopicInfo of the fake forum
    return next(t for t in rig.gw.topics[rig.chat.id] if t.title == title)


async def test_status_of_a_planned_move_is_planned_and_nothing_moved(rig: Rig) -> None:
    rig.add("o1", rig.old)
    store = await rig.store()
    plan = await plan_merge(store, rig.gw, rig.chat, ["Misc", "Old"], LIMITS)

    assert plan.op is not None and plan.op.status is OpStatus.PLANNED
    assert rig.texts(rig.old) == ["o1"] and rig.texts(rig.misc) == []
