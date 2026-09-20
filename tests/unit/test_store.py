"""The SQLite state: schema and migrations, runs and mirrors, batches, ownership, control."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.errors import RunBusy, SchemaTooNew, StoreError
from tgmirror.core.gateway import ChannelInfo, MediaKind, SrcMessage, Unit
from tgmirror.core.limiter import LimiterState
from tgmirror.store.db import HEARTBEAT_TIMEOUT, Store, default_migrations
from tgmirror.store.msgmap import MessageResult
from tgmirror.store.runs import Control, FilterChange, RunOptions, RunSpec, RunStatus

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def store(tmp_path: Path, clock: Clock):  # type: ignore[no-untyped-def]
    async with await Store.open(tmp_path / "state.db", clock=clock) as s:
        yield s


def pair() -> tuple[ChannelInfo, ChannelInfo]:
    gw = FakeGateway()
    return gw.add_channel("Src"), gw.add_channel("Dst")


def spec(**kw: object) -> RunSpec:
    src, dst = pair()
    return RunSpec(src=src, dst=dst, **kw)  # type: ignore[arg-type]


def unit(*ids: int, group: int | None = None) -> Unit:
    return Unit(tuple(SrcMessage(i, T0, media=MediaKind.PHOTO, grouped_id=group) for i in ids))


async def rows(path: Path, sql: str, *args: object) -> list[tuple[object, ...]]:
    con = sqlite3.connect(path)
    try:
        return [tuple(r) for r in con.execute(sql, args)]
    finally:
        con.close()


# ---- schema and migrations ------------------------------------------------------------------


async def test_a_new_database_gets_the_schema_and_wal(tmp_path: Path) -> None:
    path = tmp_path / "state.db"

    async with await Store.open(path):
        pass

    tables = {r[0] for r in await rows(path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "mirrors", "msg_map", "topic_map", "flood_log", "limiter_state"} <= tables
    assert "jobs" not in tables
    assert await rows(path, "PRAGMA user_version") == [(1,)]
    assert await rows(path, "PRAGMA journal_mode") == [("wal",)]


async def test_reopening_keeps_the_data(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with await Store.open(path) as first:
        run = (await first.start_run(spec())).run

    async with await Store.open(path) as second:
        assert (await second.get_run(run.id)) == run


async def test_a_database_from_a_newer_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with await Store.open(path):
        pass
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 99")
    con.close()

    with pytest.raises(SchemaTooNew):
        await Store.open(path)


async def test_upgrading_a_database_made_by_the_previous_version(tmp_path: Path) -> None:
    """Skill checkpoint-state, rule 6: every migration is tested from the version before it."""
    path = tmp_path / "state.db"
    v1 = default_migrations()
    async with await Store.open(path, migrations=v1) as old:
        run = (await old.start_run(spec())).run

    v2 = (*v1, "ALTER TABLE runs ADD COLUMN note TEXT;")
    async with await Store.open(path, migrations=v2) as upgraded:
        assert (await upgraded.get_run(run.id)) == run  # data survived

    assert await rows(path, "PRAGMA user_version") == [(2,)]
    assert "note" in [r[1] for r in await rows(path, "PRAGMA table_info(runs)")]


async def test_a_failing_migration_leaves_the_old_version_intact(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    current = default_migrations()
    async with await Store.open(path, migrations=current):
        pass

    with pytest.raises(sqlite3.OperationalError):
        await Store.open(path, migrations=(*current, "CREATE TABLE ok(x); NOT VALID SQL;"))

    assert await rows(path, "PRAGMA user_version") == [(1,)]
    assert "ok" not in {r[0] for r in await rows(path, "SELECT name FROM sqlite_master")}


# ---- runs and mirrors -----------------------------------------------------------------------


async def test_the_first_run_of_a_pair_creates_its_mirror(store: Store) -> None:
    started = await store.start_run(spec(options=RunOptions(batch_size=7, dst_base_id=42)))
    run = started.run

    assert started.filters is FilterChange.NEW
    assert (run.status, run.control) == (RunStatus.RUNNING, Control.NONE)
    assert (run.cursor_from, run.cursor_src_id, run.stats) == (0, 0, {})
    assert run.options == RunOptions(7, 42) and run.filters_json == "{}"
    mirror = await store.find_mirror(run.src_id, run.dst_id)
    assert mirror is not None and mirror.id == run.mirror_id
    assert (mirror.cursor_src_id, mirror.options.dst_base_id) == (0, 42)
    assert await store.find_mirror(run.dst_id, run.src_id) is None  # pairs are directed
    assert await store.get_run(999) is None


async def test_a_pair_seen_before_continues_from_its_cursor(store: Store) -> None:
    first = (await store.start_run(spec(options=RunOptions(dst_base_id=42)))).run
    await store.advance_cursor(first.id, 40)
    await store.finish(first.id, RunStatus.DONE)

    second = await store.start_run(
        RunSpec(src=ChannelInfo(first.src_id, "Src"), dst=ChannelInfo(first.dst_id, "Dst"))
    )

    assert second.filters is FilterChange.SAME
    assert second.run.mirror_id == first.mirror_id and second.run.id != first.id
    assert (second.run.cursor_from, second.run.stats) == (40, {})  # its own counters
    assert second.run.options.dst_base_id == 42  # recorded once, for the pair
    assert (await store.get_run(first.id)).stats == {}  # type: ignore[union-attr]


async def test_the_latest_run_of_a_pair_and_the_newest_overall(store: Store) -> None:
    a = (await store.start_run(spec())).run
    await store.finish(a.id, RunStatus.DONE)
    b = (await store.start_run(RunSpec(ChannelInfo(-5, "X"), ChannelInfo(-6, "Y")))).run
    await store.finish(b.id, RunStatus.DONE)
    again = (
        await store.start_run(RunSpec(ChannelInfo(a.src_id, "Src"), ChannelInfo(a.dst_id, "Dst")))
    ).run

    assert (await store.latest_run()) is not None and (await store.latest_run()).id == again.id  # type: ignore[union-attr]
    assert (await store.latest_run(a.src_id, a.dst_id)).id == again.id  # type: ignore[union-attr]
    assert (await store.latest_run(b.src_id, b.dst_id)).id == b.id  # type: ignore[union-attr]
    assert (await store.latest_run(1, 2)) is None
    assert [r.id for r in await store.list_runs(2)] == [again.id, b.id]


async def test_the_filter_is_remembered_and_a_different_one_restarts_the_read(store: Store) -> None:
    base = spec(filters_json='{"media": ["video"]}')
    first = (await store.start_run(base)).run
    await store.advance_cursor(first.id, 40)
    await store.finish(first.id, RunStatus.DONE)
    src, dst = ChannelInfo(first.src_id, "Src"), ChannelInfo(first.dst_id, "Dst")

    same = await store.start_run(RunSpec(src, dst))  # no filter given: the remembered one
    await store.finish(same.run.id, RunStatus.DONE)
    given_again = await store.start_run(RunSpec(src, dst, filters_json='{"media": ["video"]}'))
    await store.finish(given_again.run.id, RunStatus.DONE)
    changed = await store.start_run(RunSpec(src, dst, filters_json='{"media": ["photo"]}'))

    assert (same.filters, same.run.filters_json, same.run.cursor_from) == (
        FilterChange.SAME,
        '{"media": ["video"]}',
        40,
    )
    assert (given_again.filters, given_again.run.cursor_from) == (FilterChange.SAME, 40)
    assert (changed.filters, changed.run.filters_json) == (
        FilterChange.CHANGED,
        '{"media": ["photo"]}',
    )
    assert (changed.run.cursor_from, changed.run.stats) == (0, {})  # read again from the start
    mirror = await store.find_mirror(first.src_id, first.dst_id)
    assert mirror is not None and mirror.filters_json == '{"media": ["photo"]}'


async def test_an_empty_filter_clears_a_remembered_one(store: Store) -> None:
    first = (await store.start_run(spec(filters_json='{"media": ["video"]}'))).run
    await store.finish(first.id, RunStatus.DONE)

    cleared = await store.start_run(
        RunSpec(
            ChannelInfo(first.src_id, "Src"), ChannelInfo(first.dst_id, "Dst"), filters_json="{}"
        )
    )

    assert (cleared.filters, cleared.run.filters_json, cleared.run.cursor_from) == (
        FilterChange.CHANGED,
        "{}",
        0,
    )


def test_unknown_option_keys_from_a_newer_version_are_ignored() -> None:
    assert RunOptions.from_json('{"batch_size": 5, "from_the_future": true}') == RunOptions(5, 0)


# ---- batches: write-ahead and one transaction per result ------------------------------------


async def test_write_ahead_then_commit_moves_rows_cursor_and_stats_together(
    store: Store, tmp_path: Path
) -> None:
    run = (await store.start_run(spec())).run
    batch_id = await store.begin_batch(run.id, [unit(1), unit(2, 3, group=9)])

    # before Telegram is called: durable, pending, cursor untouched
    assert [(r.src_msg_id, r.grouped_id) for r in await store.pending_rows(run.id)] == [
        (1, None),
        (2, 9),
        (3, 9),
    ]
    assert (await store.get_run(run.id)).cursor_src_id == 0  # type: ignore[union-attr]

    after = await store.commit_batch(
        run.id,
        batch_id,
        [MessageResult(1, 101), MessageResult(2, 102), MessageResult(3, None, "boom")],
        cursor=3,
    )

    assert (after.cursor_src_id, after.stats) == (3, {"done": 2, "failed": 1})
    assert (await store.find_mirror(run.src_id, run.dst_id)).cursor_src_id == 3  # type: ignore[union-attr]
    assert await store.pending_rows(run.id) == []
    assert await rows(
        tmp_path / "state.db", "SELECT src_msg_id, dst_msg_id, status, reason FROM msg_map"
    ) == [(1, 101, "done", None), (2, 102, "done", None), (3, None, "failed", "boom")]


async def test_a_commit_that_does_not_cover_the_batch_changes_nothing(store: Store) -> None:
    run = (await store.start_run(spec())).run
    batch_id = await store.begin_batch(run.id, [unit(1), unit(2)])

    with pytest.raises(StoreError):
        await store.commit_batch(run.id, batch_id, [MessageResult(1, 101)], cursor=2)

    assert [r.src_msg_id for r in await store.pending_rows(run.id)] == [1, 2]
    assert (await store.get_run(run.id)).cursor_src_id == 0  # type: ignore[union-attr]


async def test_the_cursor_never_moves_backwards(store: Store) -> None:
    run = (await store.start_run(spec())).run
    first = await store.begin_batch(run.id, [unit(5)])
    await store.commit_batch(run.id, first, [MessageResult(5, 50)], cursor=5)
    second = await store.begin_batch(run.id, [unit(3)])

    after = await store.commit_batch(run.id, second, [MessageResult(3, 30)], cursor=3)

    assert after.cursor_src_id == 5


async def test_the_cursor_does_not_pass_a_batch_that_is_still_pending(store: Store) -> None:
    run = (await store.start_run(spec())).run
    old = await store.begin_batch(run.id, [unit(1)])  # never settled
    new = await store.begin_batch(run.id, [unit(2)])

    with pytest.raises(StoreError):
        await store.commit_batch(run.id, new, [MessageResult(2, 20)], cursor=2)

    assert old != new
    assert (await store.get_run(run.id)).cursor_src_id == 0  # type: ignore[union-attr]


async def test_a_failed_row_is_reused_by_a_later_attempt_but_a_done_row_never(
    store: Store,
) -> None:
    run = (await store.start_run(spec())).run
    first = await store.begin_batch(run.id, [unit(1), unit(2)])
    await store.commit_batch(run.id, first, [MessageResult(1, 10), MessageResult(2, None, "x")], 2)

    again = await store.begin_batch(run.id, [unit(1), unit(2)])

    assert [r.src_msg_id for r in await store.pending_rows(run.id)] == [2]  # 1 stays done
    assert again == first + 1
    assert await store.done_ids(run.id, [1, 2, 3]) == {1}


async def test_what_one_run_copied_is_known_to_the_next_run_of_the_pair(store: Store) -> None:
    first = (await store.start_run(spec())).run
    batch = await store.begin_batch(first.id, [unit(1), unit(2)])
    await store.commit_batch(
        first.id, batch, [MessageResult(1, 10), MessageResult(2, None, "x")], 2
    )
    await store.finish(first.id, RunStatus.DONE)

    second = (
        await store.start_run(
            RunSpec(ChannelInfo(first.src_id, "Src"), ChannelInfo(first.dst_id, "Dst"))
        )
    ).run

    assert await store.done_ids(second.id, [1, 2]) == {1}
    assert await store.last_done_dst_id(second.id) == 10
    assert [f.src_msg_id for f in await store.run_failures(first.id)] == [2]
    assert await store.run_failures(second.id) == []


async def test_discard_and_confirm_pending(store: Store) -> None:
    run = (await store.start_run(spec())).run
    await store.begin_batch(run.id, [unit(1), unit(2)])

    await store.confirm_pending(run.id, {1: 11, 2: 12})

    after = await store.get_run(run.id)
    assert after is not None and (after.cursor_src_id, after.stats) == (2, {"done": 2})
    assert await store.last_done_dst_id(run.id) == 12

    await store.begin_batch(run.id, [unit(3)])
    await store.discard_pending(run.id)
    assert await store.pending_rows(run.id) == []
    with pytest.raises(StoreError):  # a mapping must cover exactly the pending rows
        await store.begin_batch(run.id, [unit(4)])
        await store.confirm_pending(run.id, {5: 15})


async def test_advance_cursor_and_flood_log(store: Store, tmp_path: Path) -> None:
    run = (await store.start_run(spec())).run

    await store.advance_cursor(run.id, 40)
    await store.log_flood(
        run.id, kind="flood_wait", seconds=30, method="copy_messages", delay_ms=2000, batch_size=20
    )

    assert (await store.get_run(run.id)).cursor_src_id == 40  # type: ignore[union-attr]
    assert await rows(tmp_path / "state.db", "SELECT run_id, kind, seconds FROM flood_log") == [
        (run.id, "flood_wait", 30)
    ]
    events = await store.flood_events(run.id)
    assert [(e.kind, e.seconds, e.method) for e in events] == [("flood_wait", 30, "copy_messages")]


async def test_deleting_a_mirror_cascades_to_its_rows(store: Store, tmp_path: Path) -> None:
    run = (await store.start_run(spec())).run
    await store.begin_batch(run.id, [unit(1)])
    con = sqlite3.connect(tmp_path / "state.db")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("DELETE FROM mirrors WHERE id = ?", (run.mirror_id,))
    con.commit()
    con.close()

    assert await rows(tmp_path / "state.db", "SELECT count(*) FROM msg_map") == [(0,)]
    assert await rows(tmp_path / "state.db", "SELECT count(*) FROM runs") == [(0,)]


# ---- ownership, control ---------------------------------------------------------------------


def again(run) -> RunSpec:  # type: ignore[no-untyped-def]
    return RunSpec(ChannelInfo(run.src_id, "Src"), ChannelInfo(run.dst_id, "Dst"))


async def test_a_second_run_is_refused_while_the_heartbeat_is_fresh(
    store: Store, clock: Clock
) -> None:
    run = (await store.start_run(spec())).run
    clock.advance(seconds=HEARTBEAT_TIMEOUT.total_seconds() - 1)

    with pytest.raises(RunBusy) as busy:
        await store.start_run(again(run))

    assert busy.value.run_id == run.id


async def test_a_stale_heartbeat_can_be_taken_over_and_is_logged_as_interrupted(
    store: Store, clock: Clock
) -> None:
    run = (await store.start_run(spec())).run
    clock.advance(seconds=HEARTBEAT_TIMEOUT.total_seconds() + 1)

    second = (await store.start_run(again(run))).run

    assert second.status is RunStatus.RUNNING
    dead = await store.get_run(run.id)
    assert dead is not None
    assert (dead.status, dead.fail_reason, dead.ended_at) == (RunStatus.FAILED, "interrupted", T0)


async def test_a_heartbeat_keeps_the_lock_alive(store: Store, clock: Clock) -> None:
    run = (await store.start_run(spec())).run
    clock.advance(seconds=90)
    await store.heartbeat(run.id)
    clock.advance(seconds=90)

    with pytest.raises(RunBusy):
        await store.start_run(again(run))


async def test_force_takeover_overrides_a_fresh_lock(store: Store) -> None:
    run = (await store.start_run(spec())).run

    second = (await store.start_run(again(run), force=True)).run

    assert second.status is RunStatus.RUNNING
    taken = await store.get_run(run.id)
    assert taken is not None and (taken.status, taken.fail_reason) == (
        RunStatus.FAILED,
        "taken_over",
    )


async def test_a_paused_run_still_holds_its_clone(store: Store) -> None:
    run = (await store.start_run(spec())).run
    await store.set_status(run.id, RunStatus.PAUSED)

    with pytest.raises(RunBusy):
        await store.start_run(again(run))


async def test_the_active_run_is_the_live_one_with_a_fresh_heartbeat(
    store: Store, clock: Clock
) -> None:
    assert await store.active_run() is None
    run = (await store.start_run(spec())).run

    active = await store.active_run()
    assert active is not None and active.id == run.id

    clock.advance(seconds=HEARTBEAT_TIMEOUT.total_seconds() + 1)
    assert await store.active_run() is None  # nobody is beating: it died

    await store.heartbeat(run.id)
    await store.finish(run.id, RunStatus.DONE)
    assert await store.active_run() is None


async def test_control_is_only_accepted_for_a_live_run(store: Store) -> None:
    run = (await store.start_run(spec())).run
    await store.finish(run.id, RunStatus.STOPPED)

    assert await store.set_control(run.id, Control.PAUSE) is False  # finished: nothing to ask
    assert await store.read_control(run.id) is Control.NONE

    live = (await store.start_run(again(run))).run
    assert await store.set_control(live.id, Control.PAUSE) is True
    assert await store.read_control(live.id) is Control.PAUSE
    await store.set_status(live.id, RunStatus.PAUSED)
    assert await store.set_control(live.id, Control.NONE) is True  # a paused run can be resumed


async def test_set_status_only_toggles_a_live_run(store: Store) -> None:
    run = (await store.start_run(spec())).run

    await store.set_status(run.id, RunStatus.PAUSED)
    assert (await store.get_run(run.id)).status is RunStatus.PAUSED  # type: ignore[union-attr]
    await store.set_status(run.id, RunStatus.RUNNING)
    assert (await store.get_run(run.id)).status is RunStatus.RUNNING  # type: ignore[union-attr]

    await store.finish(run.id, RunStatus.DONE)
    await store.set_status(run.id, RunStatus.PAUSED)  # too late: it is over
    assert (await store.get_run(run.id)).status is RunStatus.DONE  # type: ignore[union-attr]


async def test_finish_sets_status_clears_control_and_closes_the_log(
    store: Store, clock: Clock
) -> None:
    run = (await store.start_run(spec())).run
    await store.set_control(run.id, Control.STOP)
    resume = clock() + timedelta(minutes=5)
    clock.advance(seconds=30)

    await store.finish(run.id, RunStatus.WAITING_FLOOD, resume_at=resume)

    after = await store.get_run(run.id)
    assert after is not None
    assert (after.status, after.control, after.resume_at) == (
        RunStatus.WAITING_FLOOD,
        Control.NONE,
        resume,
    )
    assert after.ended_at == T0 + timedelta(seconds=30) and after.started_at == T0


# ---- limiter state --------------------------------------------------------------------------

LEARNED = LimiterState(delay=4.0, day=date(2026, 1, 1), sent_today=7)


async def test_limiter_state_is_kept_per_account_and_overwritten(store: Store) -> None:
    assert await store.load_limiter_state("default") is None

    await store.save_limiter_state("default", LimiterState(2.0, date(2026, 1, 1), 1))
    await store.save_limiter_state("other", LimiterState(9.0, date(2026, 1, 1), 5))
    await store.save_limiter_state("default", LEARNED)

    assert await store.load_limiter_state("default") == LEARNED
    other = await store.load_limiter_state("other")
    assert other is not None and other.delay == 9.0


async def test_a_batch_commit_saves_the_limiter_state_with_it(store: Store) -> None:
    run = (await store.start_run(spec())).run
    batch = await store.begin_batch(run.id, [unit(1)])

    await store.commit_batch(run.id, batch, [MessageResult(1, 11)], 1, limiter=LEARNED)

    assert await store.load_limiter_state(run.account) == LEARNED


async def test_a_refused_commit_leaves_the_limiter_state_alone(store: Store) -> None:
    run = (await store.start_run(spec())).run
    first = await store.begin_batch(run.id, [unit(1)])
    await store.begin_batch(run.id, [unit(2)])  # still pending: the cursor may not pass it

    with pytest.raises(StoreError):
        await store.commit_batch(run.id, first, [MessageResult(1, 11)], 1, limiter=LEARNED)

    assert await store.load_limiter_state(run.account) is None
