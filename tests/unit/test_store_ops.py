"""The ``ops`` / ``op_items`` tables (phase 20): the frozen plan, write-ahead states, ownership."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tgmirror.core.errors import OpBusy, StoreError
from tgmirror.core.gateway import ChannelInfo
from tgmirror.store.db import HEARTBEAT_TIMEOUT, Store, default_migrations
from tgmirror.store.ops import ItemStatus, OpKind, OpSpec, OpStatus, PlannedItem
from tgmirror.store.runs import Control

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CHAT = ChannelInfo(id=-1001, title="Chat", is_admin=True)
OTHER = ChannelInfo(id=-1002, title="Other", is_admin=True)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def store(tmp_path: Path, clock: Clock):  # type: ignore[no-untyped-def]
    async with await Store.open(tmp_path / "state.db", clock=clock) as s:
        yield s


def items(*ids: int) -> list[PlannedItem]:
    return [PlannedItem(i, i) for i in ids]


async def test_v8_adds_the_operation_tables_and_the_flood_log_column(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with await Store.open(path, migrations=default_migrations()[:7]):
        pass

    async with await Store.open(path) as upgraded:
        assert await upgraded.schema_version() == len(default_migrations())
        op = await upgraded.create_op(OpSpec(OpKind.DELETE, CHAT), items(1, 2))
        await upgraded.log_flood(
            None, op_id=op.id, kind="flood_wait", seconds=5, method="delete_messages",
            delay_ms=2000, batch_size=1,
        )  # fmt: skip
        assert [e.kind for e in await upgraded.op_flood_events(op.id)] == ["flood_wait"]

    con = sqlite3.connect(path)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert {"ops", "op_items"} <= tables


async def test_create_op_freezes_the_plan_as_planned(store: Store) -> None:
    op = await store.create_op(OpSpec(OpKind.DELETE, CHAT, filters_json='{"x": 1}'), items(5, 6, 9))

    assert op.status is OpStatus.PLANNED and op.control is Control.NONE
    assert op.planned == 3 and op.done == 0 and op.chat_id == CHAT.id
    assert op.filters_json == '{"x": 1}'
    assert await store.op_counts(op.id) == {ItemStatus.PLANNED: 3}
    assert (await store.get_op(op.id)) == op
    assert (await store.latest_op()) == op


async def test_units_come_out_whole_and_in_order(store: Store) -> None:
    plan = [
        PlannedItem(1, 1),
        PlannedItem(2, 2, grouped_id=77),  # an album of 2, 3, 4: unit 2
        PlannedItem(3, 2, grouped_id=77),
        PlannedItem(4, 2, grouped_id=77),
        PlannedItem(9, 9),
    ]
    op = await store.create_op(OpSpec(OpKind.DELETE, CHAT), plan)

    first = await store.op_units(op.id, limit=2)
    assert [[i.msg_id for i in unit] for unit in first] == [[1], [2, 3, 4]]  # limit counts units
    everything = await store.op_units(op.id, limit=10)
    assert [[i.msg_id for i in unit] for unit in everything] == [[1], [2, 3, 4], [9]]


async def test_marking_items_moves_them_and_adds_to_the_counters(store: Store) -> None:
    op = await store.create_op(OpSpec(OpKind.DELETE, CHAT), items(1, 2, 3, 4))

    await store.mark_op_items(op.id, [1, 2], ItemStatus.PENDING)  # the write-ahead
    after = await store.mark_op_items(op.id, [1, 2], ItemStatus.DONE, stats={"done": 2})
    after = await store.mark_op_items(
        op.id, [3], ItemStatus.SKIPPED, reason="changed", stats={"skipped": 1}
    )

    assert after.done == 2 and after.skipped == 1 and after.handled == 3
    assert await store.op_counts(op.id) == {
        ItemStatus.DONE: 2,
        ItemStatus.SKIPPED: 1,
        ItemStatus.PLANNED: 1,
    }
    assert [i.msg_id for i in await store.op_items_in(op.id, ItemStatus.PLANNED)] == [4]
    skipped = await store.op_items_in(op.id, ItemStatus.SKIPPED)
    assert [(i.msg_id, i.reason) for i in skipped] == [(3, "changed")]


async def test_start_op_runs_it_and_a_finished_one_cannot_be_started(store: Store) -> None:
    op = await store.create_op(OpSpec(OpKind.DELETE, CHAT), items(1))

    running = await store.start_op(op.id)
    assert running.status is OpStatus.RUNNING
    assert (await store.active_op()) == running

    await store.finish_op(op.id, OpStatus.DONE)
    assert await store.active_op() is None
    with pytest.raises(StoreError, match="cannot be taken up"):
        await store.start_op(op.id)


async def test_only_one_live_operation_per_chat(store: Store, clock: Clock) -> None:
    first = await store.create_op(OpSpec(OpKind.DELETE, CHAT), items(1))
    second = await store.create_op(OpSpec(OpKind.DELETE, CHAT), items(2))
    elsewhere = await store.create_op(OpSpec(OpKind.DELETE, OTHER), items(3))
    await store.start_op(first.id)

    await store.start_op(elsewhere.id)  # another chat: no conflict
    with pytest.raises(OpBusy):
        await store.start_op(second.id)

    clock.now += HEARTBEAT_TIMEOUT + timedelta(seconds=1)  # the first one's process is dead
    await store.start_op(second.id)
    dead = await store.get_op(first.id)
    assert dead is not None
    assert dead.status is OpStatus.FAILED and dead.fail_reason == "interrupted"


async def test_control_reaches_only_a_live_operation(store: Store) -> None:
    op = await store.create_op(OpSpec(OpKind.DELETE, CHAT), items(1))

    assert not await store.set_op_control(op.id, Control.STOP)  # still only a plan
    await store.start_op(op.id)
    assert await store.set_op_control(op.id, Control.PAUSE)
    assert await store.read_op_control(op.id) is Control.PAUSE


async def test_cancel_only_drops_a_plan(store: Store) -> None:
    plan = await store.create_op(OpSpec(OpKind.DELETE, CHAT), items(1))
    running = await store.create_op(OpSpec(OpKind.DELETE, OTHER), items(2))
    await store.start_op(running.id)

    await store.cancel_op(plan.id)
    await store.cancel_op(running.id)

    assert (await store.get_op(plan.id)).status is OpStatus.CANCELLED  # type: ignore[union-attr]
    assert (await store.get_op(running.id)).status is OpStatus.RUNNING  # type: ignore[union-attr]


async def test_options_are_merged_and_read_back_as_the_plans_backup(store: Store) -> None:
    op = await store.create_op(OpSpec(OpKind.DELETE, CHAT), items(1))
    assert op.backup_dir is None and not op.backup_done

    await store.set_op_options(op.id, backup_dir="/safe/O1", backup_done=False)
    again = await store.set_op_options(op.id, backup_done=True)  # keeps what was there

    assert again.backup_dir == "/safe/O1" and again.backup_done
    assert (await store.get_op(op.id)) == again


async def test_options_of_a_missing_operation_are_an_error(store: Store) -> None:
    with pytest.raises(StoreError):
        await store.set_op_options(99, backup_done=True)
