"""The safety net of ``tgmirror delete`` (Phase 20.2) end to end on ``FakeGateway`` and a real
SQLite file: the plan's frozen ids are backed up before anything is deleted, and the plan is only
applied if that backup finished (docs/07-inspect-transform.md section 6.3 point 3).
"""

import random
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeGateway
from tests.integration.test_runner import LIMITS
from tgmirror.core.gateway import ChannelInfo, MediaKind
from tgmirror.engine import backupdir
from tgmirror.engine.backup import BackupNeedsAcknowledgement, BackupWriter
from tgmirror.engine.ops import (
    DeletePlan,
    OpBackup,
    OpRunner,
    OpTiming,
    begin_op,
    begin_op_backup,
    plan_delete,
    settle_op_backup,
)
from tgmirror.engine.runner import RunControl
from tgmirror.filters.model import FilterSpec
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store
from tgmirror.store.ops import ItemStatus, Op, OpStatus
from tgmirror.store.runs import Control, RunStatus

ADS = FilterSpec.from_json('{"include": [{"hashtag": ["#ads"]}]}')


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.gw = FakeGateway()
        self.chat: ChannelInfo = self.gw.add_channel("Chat")
        self.path = tmp_path / "state.db"
        self.dir = tmp_path / "safety"
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

    def ids(self) -> list[int]:
        return [m.id for m in self.gw.messages[self.chat.id]]

    async def plan(self, store: Store) -> Op:
        plan: DeletePlan = await plan_delete(store, self.gw, self.chat, ADS, LIMITS, nap=self.sleep)
        assert plan.op is not None
        return plan.op

    async def back_up(
        self, store: Store, op: Op, *, control: RunControl | None = None, **kw: Any
    ) -> tuple[OpBackup, Backup]:
        started = await begin_op_backup(store, self.gw, op, self.dir, **kw)
        writer = BackupWriter(
            store, self.gw, LIMITS, control=control, sleep=self.sleep, rng=random.Random(0)
        )
        final = await writer.run(
            started.backup, started.directory, started.manifest, only_ids=started.ids
        )
        return started, final


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


async def test_backs_up_exactly_the_planned_messages_and_deletes_nothing(rig: Rig) -> None:
    rig.fill(ads=3, other=2)
    store = await rig.store()
    op = await rig.plan(store)

    started, final = await rig.back_up(store, op)

    assert final.status is RunStatus.DONE and final.done == 3
    assert [r.id for r in backupdir.iter_records(rig.dir)] == [1, 2, 3]  # not 4, 5
    assert started.ids == (1, 2, 3)
    assert rig.ids() == [1, 2, 3, 4, 5]  # nothing deleted
    assert await settle_op_backup(store, op, final)
    saved = await store.get_op(op.id)
    assert saved is not None
    assert saved.status is OpStatus.PLANNED
    assert saved.backup_dir == str(rig.dir) and saved.backup_done


async def test_what_is_backed_up_is_the_plan_not_what_the_filter_selects_now(rig: Rig) -> None:
    """A message edited after the plan no longer matches, but the plan froze it: it is saved."""
    rig.fill(ads=2)
    store = await rig.store()
    op = await rig.plan(store)
    history = rig.gw.messages[rig.chat.id]
    history[:] = [replace(m, hashtags=()) if m.id == 2 else m for m in history]

    _, final = await rig.back_up(store, op)

    assert final.status is RunStatus.DONE
    assert [r.id for r in backupdir.iter_records(rig.dir)] == [1, 2]


async def test_an_album_is_saved_whole(rig: Rig) -> None:
    group = 77
    for _ in range(3):
        rig.gw.add_message(
            rig.chat.id,
            "pic #ads",
            media=MediaKind.PHOTO,
            size=10,
            mime="image/jpeg",
            grouped_id=group,
            hashtags=("#ads",),
        )
    rig.gw.add_message(rig.chat.id, "unrelated")
    store = await rig.store()
    op = await rig.plan(store)

    _, final = await rig.back_up(store, op)

    assert final.done == 3
    assert [r.id for r in backupdir.iter_records(rig.dir)] == [1, 2, 3]
    assert {r.grouped_id for r in backupdir.iter_records(rig.dir)} == {group}


async def test_a_second_go_saves_nothing_twice(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    op = await rig.plan(store)
    await rig.back_up(store, op)

    _, again = await rig.back_up(store, op)

    assert again.status is RunStatus.DONE and again.done == 0
    assert [r.id for r in backupdir.iter_records(rig.dir)] == [1, 2, 3]


async def test_a_message_gone_before_its_backup_is_counted_not_fatal(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    op = await rig.plan(store)
    history = rig.gw.messages[rig.chat.id]
    history[:] = [m for m in history if m.id != 2]  # someone deleted it meanwhile

    _, final = await rig.back_up(store, op)

    assert final.status is RunStatus.DONE
    assert final.done == 2 and final.gone == 1
    assert [r.id for r in backupdir.iter_records(rig.dir)] == [1, 3]


async def test_a_stopped_backup_is_not_finished_and_the_plan_stays_untouched(rig: Rig) -> None:
    rig.fill(ads=3)
    store = await rig.store()
    op = await rig.plan(store)
    control = RunControl()
    control.request_stop()

    started, final = await rig.back_up(store, op, control=control)

    assert final.status is RunStatus.STOPPED
    assert not await settle_op_backup(store, op, final)
    saved = await store.get_op(op.id)
    assert saved is not None
    assert saved.status is OpStatus.PLANNED and not saved.backup_done
    assert saved.backup_dir == str(started.directory)  # the next go carries on there
    assert len(await store.op_items_in(op.id, ItemStatus.PLANNED)) == 3
    assert rig.ids() == [1, 2, 3]


async def test_a_protected_chat_needs_the_statement_for_the_backup(rig: Rig) -> None:
    rig.fill(ads=2)
    rig.gw.channels[rig.chat.id] = replace(rig.chat, noforwards=True)
    store = await rig.store()
    op = await rig.plan(store)

    with pytest.raises(BackupNeedsAcknowledgement):
        await begin_op_backup(store, rig.gw, op, rig.dir)
    assert not rig.dir.exists()  # nothing was written

    _, final = await rig.back_up(store, op, ack=True)

    assert final.status is RunStatus.DONE
    manifest = backupdir.read_manifest(rig.dir)
    assert manifest is not None and manifest.protected_ack


async def test_the_backup_then_the_delete_leaves_a_restorable_copy(rig: Rig) -> None:
    rig.fill(ads=3, other=2)
    store = await rig.store()
    op = await rig.plan(store)
    _, final = await rig.back_up(store, op)
    assert await settle_op_backup(store, op, final)

    running = await begin_op(store, op.id)
    done = await OpRunner(
        store,
        rig.gw,
        LIMITS,
        control=RunControl(),
        sleep=rig.sleep,
        rng=random.Random(0),
        timing=OpTiming(poll_interval=0.1, heartbeat_interval=3600),
    ).run(running)

    assert done.status is OpStatus.DONE and done.done == 3
    assert rig.ids() == [4, 5]
    assert [r.id for r in backupdir.iter_records(rig.dir)] == [1, 2, 3]  # the way back
    assert done.backup_dir == str(rig.dir) and done.backup_done
    assert await store.read_op_control(op.id) is Control.NONE
