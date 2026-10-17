"""``tgmirror limits``, ``limits reset`` and ``--reset-limits`` (phase 19d4).

No terminal and no network: ``FakeGateway`` and a SQLite file under tmp_path. The limiter state is
seeded straight into the store, as a run that met a long FloodWait would have left it.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeGateway
from tests.unit.test_cli_run import instant_runner, source_with_messages
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.limiter import LaneBudget, LaneState, LimiterState, SentBucket
from tgmirror.store.runs import RunSpec

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]

CLONE = ["clone", "--src", "Source", "--dst", "Copy", "--yes"]
NOW = datetime.now(UTC)
LEARNED = LaneBudget(1506, 1, NOW, NOW, 1800, ok=1506, bad=1882, bad_at=NOW, checked_at=NOW)


def state_for(*dsts: int) -> LimiterState:
    """Per destination: a forward lane that learned a limit and a send lane that did not."""
    lanes: list[LaneState] = []
    for dst in dsts:
        lanes += [LaneState("forward", dst, 2.0, LEARNED), LaneState("send", dst, 3.0)]
    buckets = tuple(SentBucket("forward", dst, int(NOW.timestamp() // 300), 120) for dst in dsts)
    return LimiterState(
        3.0,
        NOW.astimezone().date(),
        0,
        lanes=(*lanes, LaneState("history", 0, 0.5)),
        buckets=buckets,
    )


def seed(rt: Runtime, state: LimiterState) -> None:
    async def do() -> None:
        async with opened_store(rt) as store:
            await store.save_limiter_state("default", state)

    asyncio.run(do())


def lanes_of(rt: Runtime) -> dict[tuple[str, int], LaneState]:
    async def read() -> dict[tuple[str, int], LaneState]:
        async with opened_store(rt) as store:
            state = await store.load_limiter_state("default")
        assert state is not None
        return {(lane.action, lane.dst): lane for lane in state.lanes}

    return asyncio.run(read())


def cloned(make_runtime: MakeRuntime, gateway: FakeGateway, count: int = 3) -> tuple[Runtime, int]:
    """A runtime whose log already holds the pair Source -> Copy; returns it and the destination."""
    src = gateway.add_channel("Source")
    dst = gateway.add_channel("Copy")
    for i in range(1, count + 1):
        gateway.add_message(src.id, f"m{i}")
    rt = make_runtime(gateway=gateway)
    assert runner.invoke(app, CLONE, obj=rt).exit_code == 0
    return rt, dst.id


def runs_reset_flag(rt: Runtime) -> list[bool]:
    """``options.reset_limits`` of every run, the first one first."""

    async def read() -> list[bool]:
        async with opened_store(rt) as store:
            return [r.options.reset_limits for r in reversed(await store.list_runs(10))]

    return asyncio.run(read())


# ---- tgmirror limits --------------------------------------------------------------------------


def test_limits_says_so_when_nothing_was_ever_sent(make_runtime: MakeRuntime) -> None:
    result = runner.invoke(app, ["limits"], obj=make_runtime())

    assert result.exit_code == 0, result.output
    assert "No write lane is recorded yet" in result.output


def test_limits_shows_each_write_lane_by_destination(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, dst = cloned(make_runtime, gateway)
    seed(rt, state_for(dst))

    result = runner.invoke(app, ["limits"], obj=rt)

    assert result.exit_code == 0, result.output
    assert f"Destination {dst} (Copy)" in result.output
    assert "forward: 1506 messages per 1 h (120 sent); blocked at 1882, ran clean up to 1506" in (
        result.output
    )
    assert "1800s wait" in result.output
    assert "send: limit not learned" in result.output
    assert "history" not in result.output  # a read lane has no limit to learn


def test_limits_json_carries_what_the_text_says(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, dst = cloned(make_runtime, gateway)
    seed(rt, state_for(dst))

    result = runner.invoke(app, ["limits", "--json"], obj=rt)

    assert result.exit_code == 0, result.output
    forward, send = json.loads(result.output)
    assert forward["destination"] == {"id": dst, "title": "Copy"}
    assert (forward["action"], forward["budget"], forward["ok"], forward["bad"]) == (
        "forward",
        1506,
        1506,
        1882,
    )
    assert (forward["window_hours"], forward["sent"], forward["probe_fails"]) == (1, 120, 0)
    assert (send["action"], send["learned"], send["budget"], send["bad"]) == (
        "send",
        False,
        None,
        None,
    )


# ---- tgmirror limits reset --------------------------------------------------------------------


def test_reset_forgets_the_limits_of_one_destination_by_id_and_keeps_the_pace(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, dst = cloned(make_runtime, gateway)
    other = gateway.add_channel("Other").id
    seed(rt, state_for(dst, other))

    result = runner.invoke(app, ["limits", "reset", "--dst", str(dst)], obj=rt)

    assert result.exit_code == 0, result.output
    assert f"Forgot the limits of 1 write lanes into {dst}" in result.output
    lanes = lanes_of(rt)
    assert lanes["forward", dst].budget is None
    assert lanes["forward", dst].delay == 2.0  # the pace it learned stays
    assert lanes["forward", other].budget == LEARNED  # another destination is left alone


def test_reset_takes_a_title_too(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    rt, dst = cloned(make_runtime, gateway)
    seed(rt, state_for(dst))

    result = runner.invoke(app, ["limits", "reset", "--dst", "copy"], obj=rt)

    assert result.exit_code == 0, result.output
    assert lanes_of(rt)["forward", dst].budget is None


def test_reset_all_forgets_every_destination(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, dst = cloned(make_runtime, gateway)
    seed(rt, state_for(dst, dst - 1))

    result = runner.invoke(app, ["limits", "reset", "--all"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Forgot the limits of 2 write lanes (every destination)" in result.output
    assert all(lane.budget is None for lane in lanes_of(rt).values())


def test_reset_with_nothing_learned_says_so(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, _ = cloned(make_runtime, gateway)

    result = runner.invoke(app, ["limits", "reset", "--all"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "No write lane has learned a limit (every destination)" in result.output


def test_reset_needs_a_target_and_refuses_two(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, dst = cloned(make_runtime, gateway)
    seed(rt, state_for(dst))

    neither = runner.invoke(app, ["limits", "reset"], obj=rt)
    both = runner.invoke(app, ["limits", "reset", "--all", "--dst", str(dst)], obj=rt)
    unknown = runner.invoke(app, ["limits", "reset", "--dst", "nowhere"], obj=rt)

    assert (neither.exit_code, both.exit_code, unknown.exit_code) == (2, 2, 2)
    assert "--dst DEST" in neither.output and "cannot be combined" in both.output
    assert "No destination 'nowhere'" in unknown.output
    assert lanes_of(rt)["forward", dst].budget == LEARNED  # none of them touched anything


def test_reset_refuses_while_a_run_holds_the_limiter(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, dst = cloned(make_runtime, gateway)
    seed(rt, state_for(dst))
    src = next(c for c in gateway.channels.values() if c.title == "Source")

    async def hold() -> int:
        async with opened_store(rt) as store:
            return (await store.start_run(RunSpec(src, gateway.channels[dst]))).run.id

    run_id = asyncio.run(hold())

    result = runner.invoke(app, ["limits", "reset", "--all"], obj=rt)

    assert result.exit_code == 2, result.output
    assert f"Run or backup {run_id} is running elsewhere" in result.output
    assert lanes_of(rt)["forward", dst].budget == LEARNED


# ---- --reset-limits ---------------------------------------------------------------------------


def test_clone_with_reset_limits_runs_past_a_budget_it_forgot(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = gateway.add_channel("Source")
    dst = gateway.add_channel("Copy")
    for i in range(1, 4):
        gateway.add_message(src.id, f"m{i}")
    rt = make_runtime(gateway=gateway)
    full = LaneBudget(2, 1, NOW, NOW, 1800, ok=2, bad=3, bad_at=NOW, checked_at=NOW)
    seed(
        rt,
        LimiterState(
            2.0,
            NOW.astimezone().date(),
            0,
            lanes=(LaneState("forward", dst.id, 2.0, full),),
            buckets=(SentBucket("forward", dst.id, int(NOW.timestamp() // 300), 2),),
        ),
    )

    result = runner.invoke(app, [*CLONE, "--reset-limits"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "--reset-limits: forgot the limits learned by 1 write lanes" in result.output
    assert [m.text for m in gateway.messages[dst.id]] == ["m1", "m2", "m3"]
    assert lanes_of(rt)["forward", dst.id].budget is None


def test_run_with_reset_limits_continues_the_pair_and_changes_nothing_else(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """The flag belongs to one run: same pair, same filter, the cursor carries on, and a later run
    without it does not forget anything again."""
    rt, dst = cloned(make_runtime, gateway)
    src = next(c for c in gateway.channels.values() if c.title == "Source")
    gateway.add_message(src.id, "m4")
    seed(rt, state_for(dst))

    async def pair() -> tuple[int, str, object]:
        async with opened_store(rt) as store:
            mirror = await store.find_mirror(src.id, dst)
            assert mirror is not None
            return mirror.id, mirror.filters_json, mirror.options

    before = asyncio.run(pair())

    result = runner.invoke(app, ["run", "--reset-limits"], obj=rt)

    assert result.exit_code == 0, result.output
    assert [m.text for m in gateway.messages[dst]] == ["m1", "m2", "m3", "m4"]  # only m4 was new
    assert asyncio.run(pair()) == before  # the same pair, filter and remembered options
    assert lanes_of(rt)["forward", dst].budget is None
    assert runs_reset_flag(rt) == [False, True]

    seed(rt, state_for(dst))
    gateway.add_message(src.id, "m5")
    again = runner.invoke(app, ["run"], obj=rt)

    assert again.exit_code == 0, again.output
    assert lanes_of(rt)["forward", dst].budget is not None  # no flag: nothing was forgotten
    assert runs_reset_flag(rt) == [False, True, False]


def test_retry_with_reset_limits_forgets_before_it_sends_again(
    make_runtime: MakeRuntime, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    instant_runner(
        monkeypatch
    )  # a message Telegram refuses makes the first run pause between sends
    src, dst = source_with_messages(gateway, 3)
    gateway.poison(src, 2)
    rt = make_runtime(gateway=gateway)
    assert runner.invoke(app, CLONE, obj=rt).exit_code == 0
    gateway.heal(src, 2)
    seed(rt, state_for(dst))

    result = runner.invoke(app, ["retry", "--reset-limits"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "--reset-limits: forgot the limits learned by 1 write lanes" in result.output
    assert lanes_of(rt)["forward", dst].budget is None
    assert runs_reset_flag(rt) == [False, True]
