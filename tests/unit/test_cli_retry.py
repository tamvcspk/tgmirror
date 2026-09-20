"""``tgmirror retry`` and ``tgmirror status`` through the real Typer app.

No terminal and no network: ``FakeGateway``, and a SQLite file under tmp_path.
"""

import asyncio
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import pytest
from typer.testing import CliRunner

import tgmirror.cli.commands.status as status_command
from tests.fakes import FakeGateway
from tests.unit.test_cli_run import (
    CLONE,
    instant_runner,
    keys_pressed,
    saved_runs,
    source_with_messages,
    start_elsewhere,
    texts,
)
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.errors import FloodWait
from tgmirror.store.db import utc_now
from tgmirror.store.runs import RunStatus

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]


@pytest.fixture(autouse=True)
def nothing_really_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A message Telegram refuses makes the runner send unit by unit, pausing between the sends."""
    instant_runner(monkeypatch)


def cloned_with_a_failure(
    make_runtime: MakeRuntime, gateway: FakeGateway, count: int = 3
) -> tuple[Runtime, int, int]:
    """A finished run 1 that could not copy message 2; returns the runtime, source, destination."""
    src, dst = source_with_messages(gateway, count)
    gateway.poison(src, 2)
    rt = make_runtime(gateway=gateway)
    assert runner.invoke(app, CLONE, obj=rt).exit_code == 0
    return rt, src, dst


def no_telegram(rt: Runtime) -> Runtime:
    """``rt`` for a command that must not connect at all."""

    @asynccontextmanager
    async def refuse(config: object):  # type: ignore[no-untyped-def]
        raise AssertionError("this command must not connect to Telegram")
        yield

    return replace(rt, connect=refuse)


# ---- retry ----------------------------------------------------------------------------------


def test_a_clone_that_failed_something_points_at_retry(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, _ = source_with_messages(gateway)
    gateway.poison(src, 2)

    result = runner.invoke(app, CLONE, obj=make_runtime(gateway=gateway))

    assert "Retry the failed messages with: tgmirror retry 1" in result.output


def test_retry_sends_the_failed_message_again_and_says_what_it_does(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = cloned_with_a_failure(make_runtime, gateway)
    gateway.heal(src, 2)

    result = runner.invoke(app, ["retry"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Run 2: retrying 1 failed messages of run 1 (Source → Copy)." in result.output
    assert "Run 2: done. 1 messages copied, 0 failed." in result.output
    assert "tgmirror retry" not in result.output  # nothing is left to point at
    assert texts(gateway, dst) == ["m1", "m3", "m2"]
    first, second = saved_runs(rt)
    assert (second.options.retry_of, second.stats) == (1, {"done": 1, "failed": 0})
    assert first.stats == {"done": 2, "failed": 1}  # the log of run 1 is not rewritten


def test_retry_with_a_number_and_json_history_of_the_retry(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, _ = cloned_with_a_failure(make_runtime, gateway)
    gateway.heal(src, 2)

    assert runner.invoke(app, ["retry", "1"], obj=rt).exit_code == 0
    detail = json.loads(runner.invoke(app, ["history", "2", "--json"], obj=rt).output)
    text = runner.invoke(app, ["history", "2"], obj=rt).output

    assert detail["retry_of"] == 1 and detail["copied"] == 1
    assert "Retry of:    the failed messages of run 1" in text
    assert "Source:      from id" not in text  # a retry reads by id: no source range


def test_a_message_that_fails_again_is_reported_and_can_be_retried_once_more(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = cloned_with_a_failure(make_runtime, gateway)

    again = runner.invoke(app, ["retry"], obj=rt)  # Telegram still refuses message 2

    assert again.exit_code == 0, again.output
    assert "1 messages still fail. See `tgmirror history 2`, or retry: tgmirror retry 2" in (
        again.output
    )
    gateway.heal(src, 2)
    assert runner.invoke(app, ["retry"], obj=rt).exit_code == 0  # no number: the latest run
    assert texts(gateway, dst) == ["m1", "m3", "m2"]


def test_retry_of_a_run_with_nothing_failed_says_so_and_starts_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)

    result = runner.invoke(app, ["retry"], obj=no_telegram(rt))  # nothing to do: no connection

    assert result.exit_code == 0, result.output
    assert "Run 1 has no failed messages to retry." in result.output
    assert len(saved_runs(rt)) == 1


def test_retry_needs_a_run_that_exists(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    empty = runner.invoke(app, ["retry"], obj=rt)
    unknown = runner.invoke(app, ["retry", "9"], obj=rt)

    assert empty.exit_code == 2 and "Nothing has been cloned yet" in empty.output
    assert unknown.exit_code == 2 and "No run '9'" in unknown.output


def test_a_message_deleted_from_the_source_is_left_out_and_not_retried_again(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = cloned_with_a_failure(make_runtime, gateway)
    gateway.delete_message(src, 2)

    result = runner.invoke(app, ["retry"], obj=rt)

    assert result.exit_code == 0, result.output
    assert (
        "1 messages no longer exist at the source and cannot be copied; left out." in result.output
    )
    assert texts(gateway, dst) == ["m1", "m3"]
    assert "gone" in (saved_runs(rt)[-1].stats)
    assert "Gone at source: 1 messages" in runner.invoke(app, ["history", "2"], obj=rt).output
    nothing = runner.invoke(app, ["retry", "1"], obj=no_telegram(rt))
    assert "Run 1 has no failed messages to retry." in nothing.output  # nor is run 1's list stale


def test_stopping_a_retry_says_to_carry_on_with_retry_not_run(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = cloned_with_a_failure(make_runtime, gateway)
    gateway.heal(src, 2)
    stopping = make_runtime(gateway=gateway, keys=keys_pressed("q"))

    result = runner.invoke(app, ["retry"], obj=stopping)

    assert result.exit_code == 0, result.output
    assert "Carry on retrying with: tgmirror retry 1" in result.output
    assert "Continue with: tgmirror run" not in result.output  # `run` would only look for new ones
    assert saved_runs(rt)[-1].status is RunStatus.STOPPED and texts(gateway, dst) == ["m1", "m3"]
    assert runner.invoke(app, ["retry", "1"], obj=rt).exit_code == 0
    assert texts(gateway, dst) == ["m1", "m3", "m2"]


def test_retry_is_refused_while_telegram_said_to_wait(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, _ = cloned_with_a_failure(make_runtime, gateway)
    gateway.add_message(src, "m4")
    gateway.fail_next("copy_messages", FloodWait(3600))  # too long to wait out: the run is parked
    assert runner.invoke(app, ["run"], obj=rt).exit_code == 3
    gateway.heal(src, 2)

    result = runner.invoke(app, ["retry", "1"], obj=no_telegram(rt))

    assert result.exit_code == 3 and "must not run again before" in result.output


def test_retry_is_refused_while_another_process_holds_the_pair(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, _ = cloned_with_a_failure(make_runtime, gateway)
    start_elsewhere(rt, gateway)  # a live run of the same pair
    gateway.heal(src, 2)

    result = runner.invoke(app, ["retry", "1"], obj=rt)

    assert result.exit_code == 1  # RunBusy
    assert "held by another process" in result.output


# ---- status ---------------------------------------------------------------------------------


def test_status_with_no_runs_says_so(make_runtime: MakeRuntime) -> None:
    result = runner.invoke(app, ["status"], obj=make_runtime())

    assert result.exit_code == 2 and "Nothing has been cloned yet" in result.output


def test_status_of_the_latest_run_when_nothing_is_running(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 5)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)

    result = runner.invoke(app, ["status"], obj=no_telegram(rt))

    assert result.exit_code == 0, result.output
    out = result.output
    assert "No run is running; this is the latest one." in out
    assert "Run 1: Source → Copy" in out and "Status:      done" in out
    assert "Progress:    ~100% (source up to id 5 / 5)" in out
    assert "5 copied, 0 failed, 0 left out by the filter" in out
    assert "5/5000 messages sent today" in out
    assert "not limited once in the last 24 hours" in out
    assert "tgmirror retry" not in out


def test_status_offers_retry_for_what_failed(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, _, _ = cloned_with_a_failure(make_runtime, gateway)

    result = runner.invoke(app, ["status"], obj=rt)

    assert "2 copied, 1 failed" in result.output
    assert "1 messages failed. Retry: tgmirror retry 1" in result.output


def test_status_of_a_run_another_terminal_is_running(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 3)
    rt = make_runtime(gateway=gateway)
    start_elsewhere(rt, gateway)

    result = runner.invoke(app, ["status"], obj=no_telegram(rt))  # the session is that run's

    assert result.exit_code == 0, result.output
    assert "Status:      running" in result.output
    assert "No run is running" not in result.output
    assert "Progress:" in result.output and "not enough data yet" in result.output


def test_status_of_a_run_that_died_says_nobody_holds_it(
    make_runtime: MakeRuntime, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_with_messages(gateway, 3)
    rt = make_runtime(gateway=gateway)
    start_elsewhere(rt, gateway)
    monkeypatch.setattr(status_command, "utc_now", lambda: utc_now() + timedelta(minutes=10))

    result = runner.invoke(app, ["status"], obj=rt)

    assert "No process holds this run" in result.output
    assert "No run is running; this is the latest one." in result.output


def test_status_of_a_run_waiting_out_a_flood(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 3)
    rt = make_runtime(gateway=gateway)
    gateway.fail_next("copy_messages", FloodWait(3600))
    assert runner.invoke(app, CLONE, obj=rt).exit_code == 3

    result = runner.invoke(app, ["status"], obj=rt)

    assert "waiting (flood)" in result.output and "Waiting until:" in result.output
    assert "limited 1 times in the last 24 hours; latest" in result.output
    assert "(flood_wait, 3600s)" in result.output


def test_status_of_a_retry_counts_the_failed_messages(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, _ = cloned_with_a_failure(make_runtime, gateway)
    gateway.heal(src, 2)
    runner.invoke(app, ["retry"], obj=rt)

    result = runner.invoke(app, ["status"], obj=rt)

    assert "Run 2: Source → Copy" in result.output
    assert "Retry of:    the failed messages of run 1" in result.output
    assert "Progress:    100% (1 / 1 failed messages retried)" in result.output


def test_status_json(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    rt, _, _ = cloned_with_a_failure(make_runtime, gateway)

    data = json.loads(runner.invoke(app, ["status", "--json"], obj=rt).output)

    assert data["run"] == 1 and data["status"] == "done" and data["live"] is False
    assert (data["progress"], data["copied"], data["failed"], data["failed_now"]) == (1.0, 2, 1, 1)
    assert data["source"]["title"] == "Source" and data["retry_of"] is None
    assert data["source_last"] == 3 and data["eta_seconds"] is None
    assert data["limiter"]["daily_cap"] == 5000 and data["floods_24h"] == 0


def test_status_and_retry_show_up_in_the_help(make_runtime: MakeRuntime) -> None:
    out = runner.invoke(app, ["--help"], obj=make_runtime()).output

    assert " retry " in out and " status " in out


def test_the_store_is_only_read_by_status(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    rt, _, _ = cloned_with_a_failure(make_runtime, gateway)

    async def snapshot() -> list[object]:
        async with opened_store(rt) as store:
            return [await store.list_runs(10), await store.count_failed(1)]

    before = asyncio.run(snapshot())
    runner.invoke(app, ["status"], obj=rt)
    runner.invoke(app, ["status", "--json"], obj=rt)

    assert asyncio.run(snapshot()) == before
