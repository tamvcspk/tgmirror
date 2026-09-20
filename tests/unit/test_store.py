"""The SQLite state: schema/migrations, jobs, write-ahead batches, ownership and control."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.errors import JobBusy, SchemaTooNew, StoreError
from tgmirror.core.gateway import MediaKind, SrcMessage, Unit
from tgmirror.core.limiter import LimiterState
from tgmirror.store.db import HEARTBEAT_TIMEOUT, Store, default_migrations
from tgmirror.store.jobs import Control, JobOptions, JobSpec, JobStatus
from tgmirror.store.msgmap import MessageResult

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


def spec(**kw: object) -> JobSpec:
    gw = FakeGateway()
    src, dst = gw.add_channel("Src"), gw.add_channel("Dst")
    return JobSpec(name="Src → Dst", src=src, dst=dst, **kw)  # type: ignore[arg-type]


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
    assert {"jobs", "msg_map", "topic_map", "flood_log", "limiter_state"} <= tables
    assert await rows(path, "PRAGMA user_version") == [(1,)]
    assert await rows(path, "PRAGMA journal_mode") == [("wal",)]


async def test_reopening_keeps_the_data(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with await Store.open(path) as first:
        job = await first.create_job(spec())

    async with await Store.open(path) as second:
        assert (await second.get_job(job.id)) == job


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
        job = await old.create_job(spec())

    v2 = (*v1, "ALTER TABLE jobs ADD COLUMN note TEXT;")
    async with await Store.open(path, migrations=v2) as upgraded:
        assert (await upgraded.get_job(job.id)) == job  # data survived

    assert await rows(path, "PRAGMA user_version") == [(2,)]
    assert "note" in [r[1] for r in await rows(path, "PRAGMA table_info(jobs)")]


async def test_a_failing_migration_leaves_the_old_version_intact(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    v1 = default_migrations()
    async with await Store.open(path, migrations=v1):
        pass

    with pytest.raises(sqlite3.OperationalError):
        await Store.open(path, migrations=(*v1, "CREATE TABLE ok(x); NOT VALID SQL;"))

    assert await rows(path, "PRAGMA user_version") == [(1,)]
    assert "ok" not in {r[0] for r in await rows(path, "SELECT name FROM sqlite_master")}


# ---- jobs -----------------------------------------------------------------------------------


async def test_create_and_find_jobs(store: Store) -> None:
    job = await store.create_job(spec(options=JobOptions(batch_size=7, dst_base_id=42)))

    assert job.status is JobStatus.CREATED and job.control is Control.NONE
    assert (job.cursor_src_id, job.stats, job.options) == (0, {}, JobOptions(7, 42))
    assert (await store.find_jobs_by_name("Src → Dst")) == [job]
    assert (await store.find_job_for_pair(job.src_id, job.dst_id)) == job
    assert (await store.find_job_for_pair(job.dst_id, job.src_id)) is None
    assert (await store.list_jobs()) == [job]
    assert (await store.get_job(999)) is None


def test_unknown_option_keys_from_a_newer_version_are_ignored() -> None:
    assert JobOptions.from_json('{"batch_size": 5, "from_the_future": true}') == JobOptions(5, 0)


# ---- batches: write-ahead and one transaction per result ------------------------------------


async def test_write_ahead_then_commit_moves_rows_cursor_and_stats_together(
    store: Store, tmp_path: Path
) -> None:
    job = await store.create_job(spec())
    batch_id = await store.begin_batch(job.id, [unit(1), unit(2, 3, group=9)])

    # before Telegram is called: durable, pending, cursor untouched
    assert [(r.src_msg_id, r.grouped_id) for r in await store.pending_rows(job.id)] == [
        (1, None),
        (2, 9),
        (3, 9),
    ]
    assert (await store.get_job(job.id)).cursor_src_id == 0  # type: ignore[union-attr]

    after = await store.commit_batch(
        job.id,
        batch_id,
        [MessageResult(1, 101), MessageResult(2, 102), MessageResult(3, None, "boom")],
        cursor=3,
    )

    assert (after.cursor_src_id, after.stats) == (3, {"done": 2, "failed": 1})
    assert await store.pending_rows(job.id) == []
    assert await rows(
        tmp_path / "state.db", "SELECT src_msg_id, dst_msg_id, status, reason FROM msg_map"
    ) == [(1, 101, "done", None), (2, 102, "done", None), (3, None, "failed", "boom")]


async def test_a_commit_that_does_not_cover_the_batch_changes_nothing(store: Store) -> None:
    job = await store.create_job(spec())
    batch_id = await store.begin_batch(job.id, [unit(1), unit(2)])

    with pytest.raises(StoreError):
        await store.commit_batch(job.id, batch_id, [MessageResult(1, 101)], cursor=2)

    assert [r.src_msg_id for r in await store.pending_rows(job.id)] == [1, 2]
    assert (await store.get_job(job.id)).cursor_src_id == 0  # type: ignore[union-attr]


async def test_the_cursor_never_moves_backwards(store: Store) -> None:
    job = await store.create_job(spec())
    first = await store.begin_batch(job.id, [unit(5)])
    await store.commit_batch(job.id, first, [MessageResult(5, 50)], cursor=5)
    second = await store.begin_batch(job.id, [unit(3)])

    after = await store.commit_batch(job.id, second, [MessageResult(3, 30)], cursor=3)

    assert after.cursor_src_id == 5


async def test_the_cursor_does_not_pass_a_batch_that_is_still_pending(store: Store) -> None:
    job = await store.create_job(spec())
    old = await store.begin_batch(job.id, [unit(1)])  # never settled
    new = await store.begin_batch(job.id, [unit(2)])

    with pytest.raises(StoreError):
        await store.commit_batch(job.id, new, [MessageResult(2, 20)], cursor=2)

    assert old != new
    assert (await store.get_job(job.id)).cursor_src_id == 0  # type: ignore[union-attr]


async def test_a_failed_row_is_reused_by_a_later_attempt_but_a_done_row_never(
    store: Store, tmp_path: Path
) -> None:
    job = await store.create_job(spec())
    first = await store.begin_batch(job.id, [unit(1), unit(2)])
    await store.commit_batch(job.id, first, [MessageResult(1, 10), MessageResult(2, None, "x")], 2)

    again = await store.begin_batch(job.id, [unit(1), unit(2)])

    assert [r.src_msg_id for r in await store.pending_rows(job.id)] == [2]  # 1 stays done
    assert again == first + 1
    assert await store.done_ids(job.id, [1, 2, 3]) == {1}


async def test_discard_and_confirm_pending(store: Store) -> None:
    job = await store.create_job(spec())
    await store.begin_batch(job.id, [unit(1), unit(2)])

    await store.confirm_pending(job.id, {1: 11, 2: 12})

    after = await store.get_job(job.id)
    assert after is not None and (after.cursor_src_id, after.stats) == (2, {"done": 2})
    assert await store.last_done_dst_id(job.id) == 12

    await store.begin_batch(job.id, [unit(3)])
    await store.discard_pending(job.id)
    assert await store.pending_rows(job.id) == []
    with pytest.raises(StoreError):  # a mapping must cover exactly the pending rows
        await store.begin_batch(job.id, [unit(4)])
        await store.confirm_pending(job.id, {5: 15})


async def test_advance_cursor_and_flood_log(store: Store, tmp_path: Path) -> None:
    job = await store.create_job(spec())

    await store.advance_cursor(job.id, 40)
    await store.log_flood(
        job.id, kind="flood_wait", seconds=30, method="copy_messages", delay_ms=2000, batch_size=20
    )

    assert (await store.get_job(job.id)).cursor_src_id == 40  # type: ignore[union-attr]
    assert await rows(tmp_path / "state.db", "SELECT job_id, kind, seconds FROM flood_log") == [
        (job.id, "flood_wait", 30)
    ]


async def test_deleting_a_job_cascades_to_its_rows(store: Store, tmp_path: Path) -> None:
    job = await store.create_job(spec())
    await store.begin_batch(job.id, [unit(1)])
    con = sqlite3.connect(tmp_path / "state.db")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
    con.commit()
    con.close()

    assert await rows(tmp_path / "state.db", "SELECT count(*) FROM msg_map") == [(0,)]


# ---- ownership, control ---------------------------------------------------------------------


async def test_claim_marks_running_and_clears_stale_control(store: Store) -> None:
    job = await store.create_job(spec())

    claimed = await store.claim(job.id)

    assert (claimed.status, claimed.control) == (JobStatus.RUNNING, Control.NONE)
    assert (await store.get_job(job.id)).status is JobStatus.RUNNING  # type: ignore[union-attr]


async def test_a_second_runner_is_refused_while_the_heartbeat_is_fresh(
    store: Store, clock: Clock
) -> None:
    job = await store.create_job(spec())
    await store.claim(job.id)
    clock.advance(seconds=HEARTBEAT_TIMEOUT.total_seconds() - 1)

    with pytest.raises(JobBusy):
        await store.claim(job.id)


async def test_a_stale_heartbeat_can_be_taken_over(store: Store, clock: Clock) -> None:
    job = await store.create_job(spec())
    await store.claim(job.id)
    clock.advance(seconds=HEARTBEAT_TIMEOUT.total_seconds() + 1)

    assert (await store.claim(job.id)).status is JobStatus.RUNNING


async def test_a_heartbeat_keeps_the_lock_alive(store: Store, clock: Clock) -> None:
    job = await store.create_job(spec())
    await store.claim(job.id)
    clock.advance(seconds=90)
    await store.heartbeat(job.id)
    clock.advance(seconds=90)

    with pytest.raises(JobBusy):
        await store.claim(job.id)


async def test_force_takeover_overrides_a_fresh_lock(store: Store) -> None:
    job = await store.create_job(spec())
    await store.claim(job.id)

    assert (await store.claim(job.id, force=True)).status is JobStatus.RUNNING


async def test_control_is_only_accepted_for_a_running_job(store: Store) -> None:
    job = await store.create_job(spec())

    assert await store.set_control(job.id, Control.PAUSE) is False  # not running: nothing to ask
    assert await store.read_control(job.id) is Control.NONE

    await store.claim(job.id)
    assert await store.set_control(job.id, Control.PAUSE) is True
    assert await store.read_control(job.id) is Control.PAUSE


async def test_finish_sets_status_and_clears_control(store: Store, clock: Clock) -> None:
    job = await store.create_job(spec())
    await store.claim(job.id)
    await store.set_control(job.id, Control.STOP)
    resume = clock() + timedelta(minutes=5)

    await store.finish(job.id, JobStatus.WAITING_FLOOD, resume_at=resume)

    after = await store.get_job(job.id)
    assert after is not None
    assert (after.status, after.control, after.resume_at) == (
        JobStatus.WAITING_FLOOD,
        Control.NONE,
        resume,
    )


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
    job = await store.create_job(spec())
    batch = await store.begin_batch(job.id, [unit(1)])

    await store.commit_batch(job.id, batch, [MessageResult(1, 11)], 1, limiter=LEARNED)

    assert await store.load_limiter_state(job.account) == LEARNED


async def test_a_refused_commit_leaves_the_limiter_state_alone(store: Store) -> None:
    job = await store.create_job(spec())
    first = await store.begin_batch(job.id, [unit(1)])
    await store.begin_batch(job.id, [unit(2)])  # still pending: the cursor may not pass it

    with pytest.raises(StoreError):
        await store.commit_batch(job.id, first, [MessageResult(1, 11)], 1, limiter=LEARNED)

    assert await store.load_limiter_state(job.account) is None
