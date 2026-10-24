"""``engine/ops.py``'s ``repost`` (Phase 20.4) end to end on ``FakeGateway`` and a real SQLite file.

What the slice is judged by (docs/07-inspect-transform.md sections 6 and 11.3): every unit is
downloaded and uploaded again at the end of the chat as a new message with a new file id; with
``delete_original`` an original goes only after its copy exists, and a unit that could not be posted
again is never deleted; a crash anywhere leaves no gap and no second copy (the chat's tail is read
to find out); decision D3 gates a chat that restricts saving content.
"""

import random
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest

from tests.fakes import FakeGateway
from tests.integration.test_runner import LIMITS
from tgmirror.core.errors import FloodWait, Transient
from tgmirror.core.gateway import CaptionPolicy, ChannelInfo, MediaKind
from tgmirror.engine.ops import (
    DeletePlan,
    OpRunner,
    OpTiming,
    RepostNeedsAcknowledgement,
    begin_op,
    plan_repost,
)
from tgmirror.engine.runner import RunControl
from tgmirror.filters.model import FilterSpec
from tgmirror.store.db import Store
from tgmirror.store.ops import ItemStatus, Op, OpStatus

ADS = FilterSpec.from_json('{"include": [{"hashtag": ["#ads"]}]}')


class Recorder:
    def __init__(self) -> None:
        self.notices: list[tuple[str, dict[str, object]]] = []

    def notice(self, code: str, **params: object) -> None:
        self.notices.append((code, params))

    def progress(self, op: Op) -> None:
        pass

    @property
    def codes(self) -> list[str]:
        return [c for c, _ in self.notices]


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.gw = FakeGateway()
        self.chat: ChannelInfo = self.gw.add_channel("Chat")
        self.path = tmp_path / "state.db"
        self.tmp = tmp_path / "tmp"
        self.recorder = Recorder()
        self._stores: list[Store] = []

    async def store(self) -> Store:
        store = await Store.open(self.path)
        self._stores.append(store)
        return store

    async def sleep(self, seconds: float) -> None:
        pass

    def add(self, text: str, **kw: object) -> int:
        tags = ("#ads",) if "#ads" in text else ()
        return self.gw.add_message(self.chat.id, text, hashtags=tags, **kw).id  # type: ignore[arg-type]

    def photo(self, text: str, key: str) -> int:
        return self.add(text, media=MediaKind.PHOTO, size=10, mime="image/jpeg", file_key=key)

    def texts(self) -> dict[int, str]:
        return {m.id: m.text for m in self.gw.messages[self.chat.id]}

    def runner(self, store: Store, control: RunControl | None = None) -> OpRunner:
        return OpRunner(
            store,
            self.gw,
            LIMITS,
            reporter=self.recorder,
            control=control,
            sleep=self.sleep,
            rng=random.Random(0),
            timing=OpTiming(poll_interval=0.1, heartbeat_interval=3600),
            tmp_dir=self.tmp,
        )

    async def plan(self, store: Store, **kw: bool) -> DeletePlan:
        return await plan_repost(store, self.gw, self.chat, ADS, LIMITS, nap=self.sleep, **kw)

    async def apply(self, store: Store, op: Op, *, ack: bool = False) -> Op:
        running = await begin_op(store, op.id, gateway=self.gw, ack=ack)
        return await self.runner(store).run(running)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


async def test_a_repost_posts_new_messages_at_the_end_and_keeps_the_originals(rig: Rig) -> None:
    rig.add("ad 0 #ads")
    rig.add("keep")
    rig.add("ad 1 #ads")
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 2
    assert rig.texts() == {
        1: "ad 0 #ads",
        2: "keep",
        3: "ad 1 #ads",
        4: "ad 0 #ads",
        5: "ad 1 #ads",
    }
    done = await store.op_items_in(plan.op.id, ItemStatus.DONE)
    assert {i.msg_id: i.new_msg_id for i in done} == {1: 4, 3: 5}


async def test_it_is_silent_unless_told_otherwise(rig: Rig) -> None:
    rig.add("ad #ads")
    store = await rig.store()
    quiet = await rig.plan(store)
    assert quiet.op is not None and quiet.op.silent
    await rig.apply(store, quiet.op)
    loud = await rig.plan(store, silent=False)
    assert loud.op is not None and not loud.op.silent
    await rig.apply(store, loud.op)

    # (the second plan also holds the first copy, which carries the same hashtag)
    assert rig.gw.silent_sends == [True, False, False]


async def test_the_files_get_new_ids_and_nothing_is_left_in_the_download_folder(rig: Rig) -> None:
    rig.photo("pic #ads", "d100")
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    final = await rig.apply(store, plan.op)

    copies = [m for m in rig.gw.messages[rig.chat.id] if m.id != 1]
    assert [m.file_key for m in copies] == ["d2"]  # not "d100"
    assert final.same_file == 0 and "same_file" not in rig.recorder.codes
    assert not list(rig.tmp.glob("*"))  # the downloads were removed


async def test_a_file_id_telegram_reused_is_reported_not_hidden(rig: Rig) -> None:
    rig.photo("pic #ads", "d100")
    rig.gw.keep_file_ids = True
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    final = await rig.apply(store, plan.op)

    assert final.same_file == 1
    assert ("same_file", {"count": 1}) in rig.recorder.notices


async def test_a_forwarded_message_comes_back_as_a_message_of_the_chat(rig: Rig) -> None:
    rig.add("news #ads", is_forward=True, fwd_from_id=-5, fwd_from_name="Elsewhere")
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    await rig.apply(store, plan.op)

    copy = rig.gw.messages[rig.chat.id][-1]
    assert not copy.is_forward and copy.fwd_from_name is None


async def test_an_album_is_posted_again_whole(rig: Rig) -> None:
    for i in range(3):
        rig.add(f"pic {i} #ads" if i == 0 else "", media=MediaKind.PHOTO, grouped_id=77, size=5)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    final = await rig.apply(store, plan.op)

    assert final.done == 3
    copies = rig.gw.messages[rig.chat.id][3:]
    assert len(copies) == 3 and len({c.grouped_id for c in copies}) == 1
    assert copies[0].grouped_id != 77


async def test_delete_original_removes_each_original_after_its_copy_exists(rig: Rig) -> None:
    rig.add("ad 0 #ads")
    rig.add("keep")
    rig.add("ad 1 #ads")
    store = await rig.store()
    plan = await rig.plan(store, delete_original=True)
    assert plan.op is not None and plan.op.delete_original

    final = await rig.apply(store, plan.op)

    assert final.status is OpStatus.DONE and final.done == 2
    assert rig.texts() == {2: "keep", 4: "ad 0 #ads", 5: "ad 1 #ads"}
    order = [c.method for c in rig.gw.calls if c.method in ("send_prepared", "delete_messages")]
    assert order == ["send_prepared", "send_prepared", "delete_messages"]


async def test_what_cannot_be_posted_again_is_left_alone_and_never_deleted(rig: Rig) -> None:
    rig.add("ad 0 #ads")
    rig.add("poll #ads", media=MediaKind.POLL, poll_options=("a", "b"))
    store = await rig.store()
    plan = await rig.plan(store, delete_original=True)
    assert plan.op is not None

    final = await rig.apply(store, plan.op)

    assert final.done == 1 and final.unsupported == 1 and final.skipped == 0
    assert "poll #ads" in rig.texts().values()  # the original poll is still there
    assert "ad 0 #ads" in rig.texts().values()  # (and only its copy, the original is gone)
    assert list(rig.texts().values()).count("ad 0 #ads") == 1


async def test_a_message_that_changed_since_the_plan_is_left_alone(rig: Rig) -> None:
    rig.add("ad 0 #ads")
    rig.add("ad 1 #ads")
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    history = rig.gw.messages[rig.chat.id]
    history[:] = [replace(m, hashtags=()) if m.id == 2 else m for m in history]

    final = await rig.apply(store, plan.op)

    assert final.done == 1 and final.skipped == 1


async def test_the_topic_of_a_unit_is_kept(rig: Rig) -> None:
    rig.add("ad #ads", topic_id=5)
    rig.add("ad too #ads")
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    await rig.apply(store, plan.op)

    topics = [m.topic_id for m in rig.gw.messages[rig.chat.id][2:]]
    assert topics == [5, None]


async def test_a_chat_that_restricts_saving_content_needs_the_statement(rig: Rig) -> None:
    rig.add("ad #ads")
    rig.gw.channels[rig.chat.id] = replace(rig.chat, noforwards=True)
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None

    with pytest.raises(RepostNeedsAcknowledgement):
        await begin_op(store, plan.op.id, gateway=rig.gw)
    assert rig.gw.calls_to("send_prepared") == []

    final = await rig.apply(store, plan.op, ack=True)
    assert final.done == 1


# ---- crashes -------------------------------------------------------------------------------


async def test_a_send_cut_before_it_went_through_is_posted_again_once(rig: Rig) -> None:
    rig.add("ad #ads")
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("send_prepared", Transient("cut"))
    running = await begin_op(store, plan.op.id, gateway=rig.gw)

    with pytest.raises(Transient):
        await rig.runner(store).run(running)
    assert [i.msg_id for i in await store.op_items_in(plan.op.id, ItemStatus.PENDING)] == [1]

    final = await rig.apply(store, (await store.get_op(plan.op.id)))  # type: ignore[arg-type]

    assert final.status is OpStatus.DONE and final.done == 1
    assert list(rig.texts().values()).count("ad #ads") == 2  # the original and one copy


async def test_a_send_that_went_through_before_the_cut_is_not_posted_twice(rig: Rig) -> None:
    rig.add("ad #ads")
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    running = await begin_op(store, plan.op.id, gateway=rig.gw)
    tail = await rig.gw.last_message_id(rig.chat.id)
    note = f'{{"topic": null, "tail": {tail}}}'
    await store.mark_op_items(running.id, [1], ItemStatus.PENDING, before_json=note)
    unit = (await rig.gw.get_messages(rig.chat.id, [1]))[0]
    from tgmirror.core.gateway import Unit

    prepared = await rig.gw.prepare(rig.chat.id, Unit((unit,)), rig.tmp)
    await rig.gw.send_prepared(rig.chat.id, prepared, CaptionPolicy())  # it went through

    final = await rig.runner(store).run(running)

    assert final.status is OpStatus.DONE and final.done == 1
    assert list(rig.texts().values()).count("ad #ads") == 2  # not 3
    done = await store.op_items_in(plan.op.id, ItemStatus.DONE)
    assert done[0].new_msg_id == 2


async def test_a_crash_between_the_copy_and_the_delete_deletes_without_posting_again(
    rig: Rig,
) -> None:
    rig.add("ad #ads")
    store = await rig.store()
    plan = await rig.plan(store, delete_original=True)
    assert plan.op is not None
    running = await begin_op(store, plan.op.id, gateway=rig.gw)
    copy_id = rig.add("ad #ads")  # the copy exists ...
    await store.mark_op_items(running.id, [1], ItemStatus.REPOSTED, new_ids={1: copy_id})

    final = await rig.runner(store).run(running)  # ... the original was not deleted yet

    assert final.status is OpStatus.DONE and final.done == 1
    assert rig.texts() == {copy_id: "ad #ads"}
    assert rig.gw.calls_to("send_prepared") == []


async def test_something_unclear_in_the_tail_is_left_alone(rig: Rig) -> None:
    rig.add("ad #ads")
    store = await rig.store()
    plan = await rig.plan(store, delete_original=True)
    assert plan.op is not None
    running = await begin_op(store, plan.op.id, gateway=rig.gw)
    tail = await rig.gw.last_message_id(rig.chat.id)
    await store.mark_op_items(
        running.id, [1], ItemStatus.PENDING, before_json=f'{{"topic": null, "tail": {tail}}}'
    )
    rig.add("somebody else wrote this", media=MediaKind.PHOTO, size=3)  # not a copy of it

    final = await rig.runner(store).run(running)

    assert final.skipped == 1 and final.done == 0
    assert "ad #ads" in rig.texts().values()  # the original is untouched
    assert rig.gw.calls_to("send_prepared") == []


async def test_a_flood_wait_parks_the_repost_with_the_plan_intact(rig: Rig) -> None:
    rig.add("ad 0 #ads")
    rig.add("ad 1 #ads")
    store = await rig.store()
    plan = await rig.plan(store)
    assert plan.op is not None
    rig.gw.fail_next("send_prepared", FloodWait(7200))
    running = await begin_op(store, plan.op.id, gateway=rig.gw)

    with pytest.raises(FloodWait):
        await rig.runner(store).run(running)

    parked = await store.get_op(plan.op.id)
    assert parked is not None and parked.status is OpStatus.WAITING_FLOOD
    assert len(await store.op_items_in(plan.op.id, ItemStatus.PLANNED)) == 2
    assert not list(rig.tmp.glob("*"))  # the download was removed even though the send failed
    final = await rig.apply(store, parked)
    assert final.done == 2


async def test_a_stop_right_after_the_send_leaves_the_copy_recorded_not_pending(rig: Rig) -> None:
    """The checks that follow a send (the file ids) read the chat and so can be interrupted: the
    copy must already be written down by then, or it would exist while its item says ``pending``."""
    from tgmirror.engine.flood import Interrupted

    class StopsAfterTheSend(OpRunner):
        async def _same_file_ids(self, *args: object, **kwargs: object) -> int:
            raise Interrupted

    rig.photo("pic #ads", "d100")
    store = await rig.store()
    plan = await rig.plan(store, delete_original=True)
    assert plan.op is not None
    running = await begin_op(store, plan.op.id, gateway=rig.gw)
    runner = StopsAfterTheSend(
        store, rig.gw, LIMITS, sleep=rig.sleep, rng=random.Random(0), tmp_dir=rig.tmp
    )

    stopped = await runner.run(running)

    assert stopped.status is OpStatus.STOPPED
    assert await store.op_items_in(plan.op.id, ItemStatus.PENDING) == []
    assert [i.msg_id for i in await store.op_items_in(plan.op.id, ItemStatus.REPOSTED)] == [1]
    final = await rig.apply(store, stopped)  # the original goes, nothing is posted again
    assert final.status is OpStatus.DONE and final.done == 1
    assert len(rig.gw.calls_to("send_prepared")) == 1
