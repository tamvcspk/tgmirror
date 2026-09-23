"""``clone``, ``run``, ``pause``, ``stop`` and ``history`` through the real Typer app.

No terminal and no network: ``FakeGateway``, a scripted prompter, and a SQLite file under tmp_path.
Sources stay below the default batch size, so the runs never sleep between batches.
"""

import asyncio
import functools
import json
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

import tgmirror.cli.commands.run as run_command
from tests.fakes import FakeAuth, FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.keys import KeyProvider, apply_key
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.errors import FloodWait
from tgmirror.engine.runner import RunControl, Runner
from tgmirror.store.runs import Control, Run, RunSpec, RunStatus

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]

CLONE = ["clone", "--src", "Source", "--dst", "Copy", "--yes"]


def saved_runs(rt: Runtime) -> list[Run]:
    """Every run, the first one first."""

    async def read() -> list[Run]:
        async with opened_store(rt) as store:
            return list(reversed(await store.list_runs(100)))

    return asyncio.run(read())


def start_elsewhere(rt: Runtime, gateway: FakeGateway) -> Run:
    """Pretend another process runs the clone: a run that is live and beating."""
    by_title = {c.title: c for c in gateway.channels.values()}
    src, dst = by_title["Source"], by_title["Copy"]

    async def do() -> Run:
        async with opened_store(rt) as store:
            return (await store.start_run(RunSpec(src, dst))).run

    return asyncio.run(do())


def set_status(rt: Runtime, run_id: int, status: RunStatus) -> None:
    async def do() -> None:
        async with opened_store(rt) as store:
            await store.set_status(run_id, status)

    asyncio.run(do())


def control_of(rt: Runtime, run_id: int) -> Control:
    async def read() -> Control:
        async with opened_store(rt) as store:
            return await store.read_control(run_id)

    return asyncio.run(read())


def source_with_messages(gateway: FakeGateway, count: int = 3, **kw: object) -> tuple[int, int]:
    src = gateway.add_channel("Source", **kw)  # type: ignore[arg-type]
    dst = gateway.add_channel("Copy")
    for i in range(1, count + 1):
        gateway.add_message(src.id, f"m{i}")
    return src.id, dst.id


def texts(gateway: FakeGateway, channel: int) -> list[str]:
    return [m.text for m in gateway.messages[channel]]


def keys_pressed(*keys: str) -> KeyProvider:
    """Hotkeys as if the person pressed ``keys`` the moment the clone starts."""

    @contextmanager
    def provider(control: RunControl) -> Iterator[bool]:
        for key in keys:
            apply_key(control, key)
        yield True

    return provider


# ---- clone: it copies right away ------------------------------------------------------------


def test_clone_copies_the_messages_and_reports(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 5)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, CLONE, obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3", "m4", "m5"]
    (run,) = saved_runs(rt)
    assert (run.src_id, run.dst_id, run.status, run.cursor_src_id, run.stats) == (
        src,
        dst,
        RunStatus.DONE,
        5,
        {"done": 5, "failed": 0},
    )
    assert "Run 1: Source → Copy, continuing after source message 0." in result.output
    assert "Run 1: done. 5 messages copied, 0 failed." in result.output
    assert (
        "Saved" not in result.output and "tgmirror run" not in result.output
    )  # nothing to do next


def test_clone_into_a_new_channel_creates_it_then_copies(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = gateway.add_channel("Source")
    gateway.add_message(src.id, "hello")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, ["clone", "--src", "Source", "--dst-new", "Copy", "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    (run,) = saved_runs(rt)
    assert texts(gateway, run.dst_id) == ["hello"]
    assert "just created" in result.output


def test_clone_into_an_existing_destination_needs_no_yes_and_no_terminal(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)

    result = runner.invoke(
        app, ["clone", "--src", "Source", "--dst", "Copy"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"]  # explicit flags are the go-ahead


def test_clone_records_where_the_destination_stood(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Reconcile later reads the destination only after this point."""
    _, dst = source_with_messages(gateway)
    for text in ("old 1", "old 2"):
        gateway.add_message(dst, text)
    rt = make_runtime(gateway=gateway)

    runner.invoke(app, CLONE, obj=rt)

    (run,) = saved_runs(rt)
    assert run.options.dst_base_id == 2


def test_clone_options_mode_and_batch_size(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "copy", "--batch-size", "2"], obj=rt)

    assert result.exit_code == 0, result.output
    (run,) = saved_runs(rt)
    assert (run.mode, run.options.batch_size) == ("copy", 2)
    assert [c.args[2] for c in gateway.calls_to("copy_messages")] == [[1, 2], [3]]


def test_an_unavailable_mode_is_refused_before_anything_is_created(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Copy", "--mode", "teleport", "--yes"],
        obj=rt,
    )

    assert result.exit_code == 2 and "teleport" in result.output
    assert gateway.calls_to("create_channel") == [] and saved_runs(rt) == []


def test_batch_size_is_bounded(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    source_with_messages(gateway)

    result = runner.invoke(app, [*CLONE, "--batch-size", "101"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 2


# ---- clone again: delta ---------------------------------------------------------------------


def test_cloning_the_same_pair_again_copies_only_what_is_new(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 2)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    gateway.add_message(src, "m3")

    again = runner.invoke(app, CLONE, obj=rt)

    assert again.exit_code == 0, again.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"]
    assert [c.args[2] for c in gateway.calls_to("copy_messages")] == [[1, 2], [3]]
    first, second = saved_runs(rt)
    assert (first.stats, second.stats) == ({"done": 2, "failed": 0}, {"done": 1, "failed": 0})
    assert (second.cursor_from, second.cursor_src_id) == (2, 3)
    assert "continuing after source message 2" in again.output


def test_cloning_the_same_pair_with_nothing_new_is_quick_and_says_done(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)

    again = runner.invoke(app, CLONE, obj=rt)

    assert again.exit_code == 0 and "0 messages copied" in again.output
    assert len(gateway.calls_to("copy_messages")) == 1


# ---- clone: the one question, and parity with the wizard ------------------------------------


def test_wizard_and_flags_end_in_the_same_run_and_the_same_copy(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """Parity rule: the terminal path and the flags call the same begin_run and copy."""

    def prepared() -> FakeGateway:
        gw = FakeGateway()
        source_with_messages(gw, 4)
        return gw

    flags_gw, wizard_gw = prepared(), prepared()
    flags_rt = make_runtime(gateway=flags_gw, root=tmp_path / "flags")
    flags = runner.invoke(app, CLONE, obj=flags_rt)
    prompter = ScriptedPrompter(
        select=["Source", "Copy", "Automatic", "No filter"], confirm=[False, True]
    )
    wizard_rt = make_runtime(
        gateway=wizard_gw, prompter=prompter, interactive=True, root=tmp_path / "wizard"
    )
    wizard = runner.invoke(app, ["clone"], obj=wizard_rt)

    assert flags.exit_code == wizard.exit_code == 0, wizard.output
    (a,), (b,) = saved_runs(flags_rt), saved_runs(wizard_rt)
    assert (a.src_id, a.dst_id, a.mode, a.options, a.status, a.stats, a.filters_json) == (
        b.src_id, b.dst_id, b.mode, b.options, b.status, b.stats, b.filters_json,
    )  # fmt: skip
    assert texts(flags_gw, a.dst_id) == texts(wizard_gw, b.dst_id) == ["m1", "m2", "m3", "m4"]
    assert flags.output == wizard.output
    _, question = [m for kind, m in prompter.asked if kind == "confirm"]  # not one per step
    assert question.startswith("Clone ") and question.endswith(" now?")


def test_declining_the_question_creates_and_copies_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    prompter = ScriptedPrompter(
        select=["Source", "Copy", "Automatic", "No filter"], confirm=[False, False]
    )
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["clone"], obj=rt)

    assert result.exit_code == 1
    assert texts(gateway, dst) == [] and saved_runs(rt) == []
    assert gateway.calls_to("copy_messages") == []


def test_yes_on_a_terminal_asks_nothing_and_copies(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    prompter = ScriptedPrompter()

    result = runner.invoke(
        app, CLONE, obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 0 and prompter.asked == []
    assert texts(gateway, dst) == ["m1", "m2", "m3"]


# ---- run: the same clone again --------------------------------------------------------------


def test_run_continues_the_latest_clone_and_run_n_picks_a_pair_from_history(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 2)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    gateway.add_message(src, "m3")

    latest = runner.invoke(app, ["run"], obj=rt)
    gateway.add_message(src, "m4")
    by_number = runner.invoke(app, ["run", "1"], obj=rt)

    assert latest.exit_code == by_number.exit_code == 0, by_number.output
    assert texts(gateway, dst) == ["m1", "m2", "m3", "m4"]  # each later run copied only the new one
    assert [c.args[2] for c in gateway.calls_to("copy_messages")] == [[1, 2], [3], [4]]
    assert [r.id for r in saved_runs(rt)] == [1, 2, 3]


def test_run_keeps_the_filter_and_the_options_of_that_run(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 2)
    gateway.add_message(src, "m3 #k", hashtags=("#k",))
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, [*CLONE, "--hashtag", "#k", "--batch-size", "7", "--mode", "copy"], obj=rt)
    gateway.add_message(src, "m4 #k", hashtags=("#k",))
    gateway.add_message(src, "m5")

    again = runner.invoke(app, ["run"], obj=rt)

    assert again.exit_code == 0, again.output
    assert texts(gateway, dst) == ["m3 #k", "m4 #k"]
    first, second = saved_runs(rt)
    assert (second.filters_json, second.options.batch_size, second.mode) == (
        first.filters_json,
        7,
        "copy",
    )
    assert "Using the filter of the previous run" in again.output


def test_run_with_two_pairs_and_no_number_asks_which_to_continue(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Without this step, a bare `run` would silently pick pair B (the latest run overall,
    started second below); picking "#1" proves the wizard, not the old default, decided this."""
    src_a, dst_a = source_with_messages(gateway, 1)
    src_b, dst_b = gateway.add_channel("Source2"), gateway.add_channel("Copy2")
    gateway.add_message(src_b.id, "n1")

    prompter = ScriptedPrompter(select=["#1"])
    rt = make_runtime(gateway=gateway, interactive=True, prompter=prompter)
    runner.invoke(app, CLONE, obj=rt)  # pair A -> run 1
    runner.invoke(app, ["clone", "--src", "Source2", "--dst", "Copy2", "--yes"], obj=rt)  # run 2
    gateway.add_message(src_a, "m2")
    gateway.add_message(src_b.id, "n2")

    result = runner.invoke(app, ["run"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst_a) == ["m1", "m2"]  # pair A was continued, not pair B
    assert texts(gateway, dst_b.id) == ["n1"]  # untouched
    assert ("select", "Continue which pair? (type to filter)") in prompter.asked


def test_run_with_one_pair_and_no_number_asks_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 1)
    prompter = ScriptedPrompter()  # a "select" call with nothing queued would fail the test
    rt = make_runtime(gateway=gateway, interactive=True, prompter=prompter)
    runner.invoke(app, CLONE, obj=rt)
    gateway.add_message(src, "m2")

    result = runner.invoke(app, ["run"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2"]
    assert ("select", "Continue which pair? (type to filter)") not in prompter.asked


def test_run_with_two_pairs_but_no_terminal_keeps_picking_the_latest(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Non-interactive (a script) must never gain a prompt it did not have before."""
    src_a, dst_a = source_with_messages(gateway, 1)
    src_b, dst_b = gateway.add_channel("Source2"), gateway.add_channel("Copy2")
    gateway.add_message(src_b.id, "n1")
    rt = make_runtime(gateway=gateway)  # interactive=False by default
    runner.invoke(app, CLONE, obj=rt)
    runner.invoke(app, ["clone", "--src", "Source2", "--dst", "Copy2", "--yes"], obj=rt)
    gateway.add_message(src_b.id, "n2")

    result = runner.invoke(app, ["run"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst_b.id) == ["n1", "n2"]  # the latest run overall (pair B), unasked
    assert texts(gateway, dst_a) == ["m1"]


def test_run_with_no_history_or_an_unknown_number_exits_2(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    nothing = runner.invoke(app, ["run"], obj=rt)
    unknown = runner.invoke(app, ["run", "42"], obj=rt)

    assert nothing.exit_code == 2 and "Nothing has been cloned yet" in nothing.output
    assert unknown.exit_code == 2 and "No run '42'" in unknown.output


def test_run_needs_a_login_and_touches_nothing_otherwise(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    calls = len(gateway.calls_to("copy_messages"))
    logged_out = make_runtime(gateway=gateway, auth=FakeAuth(), root=rt.paths.data_dir.parent)

    result = runner.invoke(app, ["run"], obj=logged_out)

    assert result.exit_code == 1 and "tgmirror login" in result.output
    assert len(gateway.calls_to("copy_messages")) == calls


def test_a_flood_wait_ends_the_run_with_exit_3_and_the_next_run_is_refused_at_once(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    gateway.fail_next("copy_messages", FloodWait(3600))

    first = runner.invoke(app, CLONE, obj=rt)
    second = runner.invoke(app, ["run"], obj=rt)
    third = runner.invoke(app, CLONE, obj=rt)

    assert first.exit_code == 3 and "3600s" in first.output
    assert saved_runs(rt)[0].status is RunStatus.WAITING_FLOOD
    for refused in (second, third):
        assert refused.exit_code == 3 and "must not run again before" in refused.output
    assert len(gateway.calls_to("copy_messages")) == 1  # the others never got to Telegram
    assert len(saved_runs(rt)) == 1  # a refused run is not logged as a run


def test_a_restricted_source_exits_4_with_advice(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, noforwards=True, is_admin=True)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, CLONE, obj=rt)

    assert result.exit_code == 4
    assert "Restrict saving content" in result.output and "--mode reupload" in result.output
    assert saved_runs(rt)[0].status is RunStatus.FAILED


def test_a_clone_held_by_another_process_is_refused_unless_taken_over(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    held = start_elsewhere(rt, gateway)

    refused = runner.invoke(app, CLONE, obj=rt)
    refused_run = runner.invoke(app, ["run"], obj=rt)
    forced = runner.invoke(app, [*CLONE, "--force-takeover"], obj=rt)

    assert refused.exit_code == refused_run.exit_code == 1
    assert "--force-takeover" in refused.output and "--force-takeover" in refused_run.output
    assert forced.exit_code == 0, forced.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"]
    assert saved_runs(rt)[0].id == held.id and saved_runs(rt)[0].fail_reason == "taken_over"


def test_run_resumes_a_clone_paused_in_another_terminal_instead_of_starting_a_second(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    held = start_elsewhere(rt, gateway)
    set_status(rt, held.id, RunStatus.PAUSED)
    runner.invoke(app, ["pause"], obj=rt)
    assert control_of(rt, held.id) is Control.PAUSE

    result = runner.invoke(app, ["run"], obj=rt)

    assert (
        result.exit_code == 0 and f"Run {held.id} was paused in another terminal" in result.output
    )
    assert control_of(rt, held.id) is Control.NONE  # the other process carries on
    assert gateway.calls_to("copy_messages") == [] and len(saved_runs(rt)) == 1


# ---- fresh start ----------------------------------------------------------------------------


def copied_count(rt: Runtime, gateway: FakeGateway) -> int:
    by_title = {c.title: c for c in gateway.channels.values()}

    async def read() -> int:
        async with opened_store(rt) as store:
            return await store.count_copied(by_title["Source"].id, by_title["Copy"].id)

    return asyncio.run(read())


def test_clone_fresh_copies_everything_again_and_says_so(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)

    result = runner.invoke(app, [*CLONE, "--fresh"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Fresh start: forgot 3 copied messages" in result.output
    assert "already skipped" not in result.output and "filter changed" not in result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"] * 2
    first, second = saved_runs(rt)
    assert (second.cursor_from, second.done) == (0, 3) and first.done == 3  # the log keeps both
    listing = runner.invoke(app, ["history"], obj=rt).output
    assert listing.count("Source → Copy") == 2  # both runs are still in the log


def test_a_fresh_start_asks_first_on_a_terminal_and_declining_forgets_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    prompter = ScriptedPrompter(confirm=[False])
    asking = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(
        app, ["clone", "--src", "Source", "--dst", "Copy", "--fresh"], obj=asking
    )

    assert result.exit_code == 1
    ((kind, question),) = prompter.asked
    assert kind == "confirm" and "already has 3 messages" in question
    assert copied_count(rt, gateway) == 3 and len(saved_runs(rt)) == 1  # untouched
    again = runner.invoke(app, CLONE, obj=rt)
    assert "0 messages copied" in again.output and texts(gateway, dst) == ["m1", "m2", "m3"]


def test_a_fresh_start_without_yes_or_a_terminal_is_a_usage_error_and_forgets_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)

    result = runner.invoke(app, ["clone", "--src", "Source", "--dst", "Copy", "--fresh"], obj=rt)

    assert result.exit_code == 2 and "--yes" in result.output and "3 copied" in result.output
    assert copied_count(rt, gateway) == 3 and len(gateway.calls_to("copy_messages")) == 1


def test_fresh_on_a_pair_with_nothing_to_forget_asks_nothing_and_needs_no_yes(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst", "Copy", "--fresh"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"] and "Fresh start" not in result.output


def test_run_fresh_starts_the_latest_pair_over(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)

    refused = runner.invoke(app, ["run", "--fresh"], obj=rt)
    assert refused.exit_code == 2 and "--yes" in refused.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"]

    result = runner.invoke(app, ["run", "--fresh", "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Fresh start: forgot 3 copied messages" in result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"] * 2


def test_run_fresh_on_a_terminal_asks_with_the_count(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    prompter = ScriptedPrompter(confirm=[True])

    result = runner.invoke(
        app,
        ["run", "--fresh"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 0, result.output
    assert "already has 3 messages" in prompter.asked[0][1]


def test_run_fresh_is_not_swallowed_by_a_run_paused_in_another_terminal(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    held = start_elsewhere(rt, gateway)
    set_status(rt, held.id, RunStatus.PAUSED)
    calls = len(gateway.calls_to("copy_messages"))

    result = runner.invoke(app, ["run", "--fresh", "--yes"], obj=rt)

    assert result.exit_code == 1 and "--force-takeover" in result.output  # RunBusy, not a resume
    assert copied_count(rt, gateway) == 3  # nothing was forgotten
    assert len(gateway.calls_to("copy_messages")) == calls
    assert control_of(rt, held.id) is Control.NONE


def test_the_wizard_offers_continue_or_scratch_only_for_a_pair_with_progress(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    fresh_pair = ScriptedPrompter(
        select=["Source", "Copy", "Automatic", "No filter"], confirm=[False, True]
    )
    runner.invoke(
        app, ["clone"], obj=make_runtime(gateway=gateway, prompter=fresh_pair, interactive=True)
    )
    # source, destination, mode, filter: no restart question
    assert len(fresh_pair.select_labels) == 4

    scratch = ScriptedPrompter(
        select=["Source", "Copy", "Automatic", "Start from scratch", "No filter"],
        confirm=[False, True],
    )
    result = runner.invoke(
        app, ["clone"], obj=make_runtime(gateway=gateway, prompter=scratch, interactive=True)
    )

    assert result.exit_code == 0, result.output
    assert [label for label in scratch.select_labels[3]] == [
        "Continue: only what is new",
        "Start from scratch: copy everything again",
    ]
    assert "already has 3 messages" in [m for k, m in scratch.asked if k == "confirm"][1]
    assert texts(gateway, dst) == ["m1", "m2", "m3"] * 2 and "Fresh start" in result.output


def test_the_wizard_continue_choice_is_an_ordinary_delta(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 2)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    gateway.add_message(src, "m3")
    carry_on = ScriptedPrompter(
        select=["Source", "Copy", "Automatic", "Continue", "No filter"], confirm=[False, True]
    )

    result = runner.invoke(
        app, ["clone"], obj=make_runtime(gateway=gateway, prompter=carry_on, interactive=True)
    )

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"] and "Fresh start" not in result.output


def test_the_fresh_flag_skips_the_wizard_restart_question(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    prompter = ScriptedPrompter(
        select=["Source", "Copy", "Automatic", "No filter"], confirm=[False, True]
    )

    result = runner.invoke(
        app,
        ["clone", "--fresh"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 0, result.output
    assert len(prompter.select_labels) == 4
    assert texts(gateway, dst) == ["m1", "m2", "m3"] * 2


# ---- Ctrl+C and the hotkeys -----------------------------------------------------------------


def test_ctrl_c_saves_and_exits_130_then_run_finishes_without_duplicates(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """The first Ctrl+C finishes the batch in flight, saves, and leaves with 130."""
    _, dst = source_with_messages(gateway, 4)
    rt = make_runtime(gateway=gateway)
    original = gateway.copy_messages

    async def interrupt_then_copy(s: int, d: int, ids: list[int]) -> list[int | None]:
        signal.raise_signal(signal.SIGINT)  # what the terminal does
        return await original(s, d, ids)

    gateway.copy_messages = interrupt_then_copy  # type: ignore[method-assign]
    result = runner.invoke(app, [*CLONE, "--batch-size", "2"], obj=rt)

    assert result.exit_code == 130, result.output
    assert texts(gateway, dst) == ["m1", "m2"]  # the batch in flight was finished
    (run,) = saved_runs(rt)
    assert (run.status, run.cursor_src_id) == (RunStatus.STOPPED, 2)
    assert "Stopping after the current batch" in result.output
    assert "Continue with: tgmirror run 1" in result.output

    gateway.copy_messages = original  # type: ignore[method-assign]
    resumed = runner.invoke(app, ["run"], obj=rt)

    assert resumed.exit_code == 0, resumed.output
    assert texts(gateway, dst) == ["m1", "m2", "m3", "m4"]  # no duplicate, no gap
    assert [r.status for r in saved_runs(rt)] == [RunStatus.STOPPED, RunStatus.DONE]


def test_the_key_q_stops_the_clone_and_exits_0(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway, keys=keys_pressed("q"))

    result = runner.invoke(app, CLONE, obj=rt)

    assert result.exit_code == 0, result.output  # a deliberate stop is not an interruption
    assert texts(gateway, dst) == [] and saved_runs(rt)[0].status is RunStatus.STOPPED
    assert "Continue with: tgmirror run 1" in result.output


def test_the_keys_are_announced_and_p_then_r_changes_nothing_for_a_finished_clone(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway, keys=keys_pressed("p", "r"))

    result = runner.invoke(app, CLONE, obj=rt)

    assert result.exit_code == 0, result.output
    assert "Keys: [p] pause  [r] resume  [q] stop" in result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"]


def test_no_keys_line_without_a_terminal(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    source_with_messages(gateway)

    result = runner.invoke(app, CLONE, obj=make_runtime(gateway=gateway))

    assert "Keys:" not in result.output


# ---- pause / stop ---------------------------------------------------------------------------


def test_pause_and_stop_the_running_clone(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    held = start_elsewhere(rt, gateway)

    paused = runner.invoke(app, ["pause"], obj=rt)
    assert paused.exit_code == 0 and f"run {held.id} to pause" in paused.output
    assert control_of(rt, held.id) is Control.PAUSE

    stopped = runner.invoke(app, ["stop"], obj=rt)
    assert stopped.exit_code == 0 and f"run {held.id} to stop" in stopped.output
    assert control_of(rt, held.id) is Control.STOP


def test_pause_and_stop_with_nothing_running_change_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)  # finished: nothing is running

    paused = runner.invoke(app, ["pause"], obj=rt)
    stopped = runner.invoke(app, ["stop"], obj=rt)

    assert paused.exit_code == stopped.exit_code == 1
    assert "No clone is running" in paused.output
    assert control_of(rt, 1) is Control.NONE


def test_pause_with_no_history_at_all_says_nothing_is_running(make_runtime: MakeRuntime) -> None:
    result = runner.invoke(app, ["pause"], obj=make_runtime())

    assert result.exit_code == 1 and "No clone is running" in result.output


# ---- history --------------------------------------------------------------------------------


def test_history_lists_the_runs_newest_first(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, _ = source_with_messages(gateway, 2)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)
    gateway.add_message(src, "m3")
    runner.invoke(app, CLONE, obj=rt)

    result = runner.invoke(app, ["history"], obj=rt)

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if "Source → Copy" in line]
    assert len(lines) == 2 and lines[0].split()[0] == "2" and lines[1].split()[0] == "1"
    assert "done" in lines[0] and "Recent runs" in result.output


def test_history_of_an_empty_log(make_runtime: MakeRuntime) -> None:
    result = runner.invoke(app, ["history"], obj=make_runtime())

    assert result.exit_code == 0 and "No runs yet" in result.output


def test_history_json_is_machine_readable(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    source_with_messages(gateway, 2)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)

    listing = json.loads(runner.invoke(app, ["history", "--json"], obj=rt).output)
    detail = json.loads(runner.invoke(app, ["history", "1", "--json"], obj=rt).output)

    assert [r["run"] for r in listing] == [1]
    assert (detail["status"], detail["copied"], detail["failed"]) == ("done", 2, 0)
    assert detail["source"]["title"] == "Source" and detail["failed_messages"] == []


def test_history_detail_lists_what_failed_and_why(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, _ = source_with_messages(gateway, 3)
    gateway.poison(src, 2, "MESSAGE_ID_INVALID")
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, CLONE, obj=rt)

    result = runner.invoke(app, ["history", "1"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Run 1: Source → Copy" in result.output
    assert "2 copied, 1 failed" in result.output
    assert "source message 2: MESSAGE_ID_INVALID" in result.output


def test_history_detail_shows_the_limits_telegram_set(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    gateway.fail_next("copy_messages", FloodWait(3600))
    runner.invoke(app, CLONE, obj=rt)

    result = runner.invoke(app, ["history", "1"], obj=rt)

    assert "Limits from Telegram" in result.output and "flood_wait 3600s" in result.output
    assert "waiting (flood)" in result.output


def test_history_of_an_unknown_run_exits_2(make_runtime: MakeRuntime) -> None:
    assert runner.invoke(app, ["history", "9"], obj=make_runtime()).exit_code == 2


# ---- flood handling (phase 4) ---------------------------------------------------------------


class Slept:
    """Replaces the runner's sleep: nothing really waits, the total is recorded."""

    def __init__(self) -> None:
        self.total = 0.0

    async def __call__(self, seconds: float) -> None:
        self.total += seconds


def instant_runner(monkeypatch: pytest.MonkeyPatch) -> Slept:
    slept = Slept()
    monkeypatch.setattr(run_command, "Runner", functools.partial(Runner, sleep=slept))
    return slept


def test_a_short_flood_wait_is_sat_out_and_the_run_finishes(
    make_runtime: MakeRuntime, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    slept = instant_runner(monkeypatch)
    gateway.fail_next("copy_messages", FloodWait(30))

    result = runner.invoke(app, CLONE, obj=rt)

    assert result.exit_code == 0, result.output
    assert "Telegram asks to wait 30s" in result.output and "same batch" in result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"] and slept.total >= 31
    assert saved_runs(rt)[0].status is RunStatus.DONE


def test_wait_sits_out_a_flood_the_run_would_otherwise_stop_for(
    make_runtime: MakeRuntime, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    slept = instant_runner(monkeypatch)
    gateway.fail_next("copy_messages", FloodWait(3600))

    result = runner.invoke(app, [*CLONE, "--wait"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"] and slept.total >= 3601


def test_the_daily_cap_ends_the_run_with_exit_3_until_the_next_day(
    make_runtime: MakeRuntime, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, dst = source_with_messages(gateway, count=6)
    rt = make_runtime(gateway=gateway)
    rt.paths.ensure()
    rt.paths.config_file.write_text(
        "[limits]\nbatch_size = 2\ndaily_cap = 4\nmin_delay = 0.01\nlong_pause_range = [0, 0]\n",
        encoding="utf-8",
    )
    instant_runner(monkeypatch)

    first = runner.invoke(app, CLONE, obj=rt)
    second = runner.invoke(app, ["run"], obj=rt)

    assert first.exit_code == 3
    assert "4 messages sent today" in first.output and "daily cap (4)" in first.output
    (run,) = saved_runs(rt)
    assert (run.status, run.fail_reason, run.cursor_src_id) == (
        RunStatus.WAITING_FLOOD,
        "daily_cap",
        4,
    )
    assert texts(gateway, dst) == ["m1", "m2", "m3", "m4"]
    assert second.exit_code == 3 and "daily limit on messages sent" in second.output
    assert len(gateway.calls_to("copy_messages")) == 2  # the second run never got to Telegram
