"""``new`` (saving the job), ``run``, ``pause`` and ``stop`` through the real Typer app.

No terminal and no network: ``FakeGateway``, a scripted prompter, and a SQLite file under tmp_path.
Sources stay below the default batch size, so the runs never sleep between batches.
"""

import asyncio
import signal
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import FakeAuth, FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.errors import FloodWait
from tgmirror.store.jobs import Control, Job, JobStatus

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]


def saved_jobs(rt: Runtime) -> list[Job]:
    async def read() -> list[Job]:
        async with opened_store(rt) as store:
            return await store.list_jobs()

    return asyncio.run(read())


def claim(rt: Runtime, job_id: int) -> None:
    """Pretend another process runs the job."""

    async def do() -> None:
        async with opened_store(rt) as store:
            await store.claim(job_id)

    asyncio.run(do())


def control_of(rt: Runtime, job_id: int) -> Control:
    async def read() -> Control:
        async with opened_store(rt) as store:
            return await store.read_control(job_id)

    return asyncio.run(read())


def source_with_messages(gateway: FakeGateway, count: int = 3, **kw: object) -> tuple[int, int]:
    src = gateway.add_channel("Source", **kw)  # type: ignore[arg-type]
    dst = gateway.add_channel("Copy")
    for i in range(1, count + 1):
        gateway.add_message(src.id, f"m{i}")
    return src.id, dst.id


def texts(gateway: FakeGateway, channel: int) -> list[str]:
    return [m.text for m in gateway.messages[channel]]


# ---- new: saving the job --------------------------------------------------------------------


def test_new_saves_the_job_without_running_it(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    (job,) = saved_jobs(rt)
    assert (job.src_id, job.dst_id, job.status, job.cursor_src_id) == (
        src,
        dst,
        JobStatus.CREATED,
        0,
    )
    assert (job.name, job.mode, job.options.batch_size) == ("Source → Copy", "auto", 20)
    assert "Saved job 1" in result.output and "tgmirror run 1" in result.output
    assert gateway.calls_to("copy_messages") == [] and texts(gateway, dst) == []


def test_new_records_where_the_destination_stood(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Reconcile later reads the destination only after this point."""
    _, dst = source_with_messages(gateway)
    for text in ("old 1", "old 2"):
        gateway.add_message(dst, text)
    rt = make_runtime(gateway=gateway)

    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy"], obj=rt)

    (job,) = saved_jobs(rt)
    assert job.options.dst_base_id == 2


def test_new_options_name_mode_and_batch_size(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--name", "mine", "--mode", "copy",
         "--batch-size", "5"],
        obj=rt,
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    (job,) = saved_jobs(rt)
    assert (job.name, job.mode, job.options.batch_size) == ("mine", "copy", 5)


def test_new_refuses_a_second_job_for_the_same_pair(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy"], obj=rt)

    again = runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy"], obj=rt)

    assert again.exit_code == 2 and "Job 1 already" in again.output
    assert len(saved_jobs(rt)) == 1


def test_an_unavailable_mode_is_refused_before_anything_is_created(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app, ["new", "--src", "Source", "--dst-new", "Copy", "--mode", "reupload", "--yes"], obj=rt
    )

    assert result.exit_code == 2 and "phase 6" in result.output
    assert gateway.calls_to("create_channel") == [] and saved_jobs(rt) == []


def test_batch_size_is_bounded(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    source_with_messages(gateway)

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--batch-size", "101"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 2


def test_new_run_copies_the_messages_and_reports(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 5)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app, ["new", "--src", "Source", "--dst", "Copy", "--run", "--yes"], obj=rt
    )

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3", "m4", "m5"]
    (job,) = saved_jobs(rt)
    assert (job.status, job.cursor_src_id, job.stats) == (
        JobStatus.DONE,
        5,
        {"done": 5, "failed": 0},
    )
    assert "Running job 1" in result.output
    assert "Job 1: done. 5 messages copied, 0 failed." in result.output


def test_new_dst_new_run_creates_then_copies(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = gateway.add_channel("Source")
    gateway.add_message(src.id, "hello")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app, ["new", "--src", "Source", "--dst-new", "Copy", "--yes", "--run"], obj=rt
    )

    assert result.exit_code == 0, result.output
    (job,) = saved_jobs(rt)
    assert texts(gateway, job.dst_id) == ["hello"]


def test_wizard_and_flags_end_in_the_same_job_and_the_same_copy(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """Parity rule: the terminal path and the flags call the same create_job and run."""

    def prepared() -> FakeGateway:
        gw = FakeGateway()
        source_with_messages(gw, 4)
        return gw

    flags_gw, wizard_gw = prepared(), prepared()
    flags_rt = make_runtime(gateway=flags_gw, root=tmp_path / "flags")
    flags = runner.invoke(
        app, ["new", "--src", "Source", "--dst", "Copy", "--run", "--yes"], obj=flags_rt
    )
    prompter = ScriptedPrompter(
        select=["Source", "Copy", "No filter"], confirm=[True]
    )  # ... run it now: yes
    wizard_rt = make_runtime(
        gateway=wizard_gw, prompter=prompter, interactive=True, root=tmp_path / "wizard"
    )
    wizard = runner.invoke(app, ["new"], obj=wizard_rt)

    assert flags.exit_code == wizard.exit_code == 0, wizard.output
    (a,), (b,) = saved_jobs(flags_rt), saved_jobs(wizard_rt)
    assert (a.name, a.src_id, a.dst_id, a.mode, a.options, a.status, a.stats) == (
        b.name, b.src_id, b.dst_id, b.mode, b.options, b.status, b.stats,
    )  # fmt: skip
    assert texts(flags_gw, a.dst_id) == texts(wizard_gw, b.dst_id) == ["m1", "m2", "m3", "m4"]
    assert flags.output == wizard.output
    assert ("confirm", "Run it now?") in prompter.asked


def test_the_wizard_does_not_run_unless_told_to(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    prompter = ScriptedPrompter(select=["Source", "Copy", "No filter"], confirm=[False])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["new"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == [] and saved_jobs(rt)[0].status is JobStatus.CREATED
    assert "tgmirror run 1" in result.output


def test_yes_on_a_terminal_saves_without_asking_to_run(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    prompter = ScriptedPrompter()

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 0 and prompter.asked == []


# ---- run ------------------------------------------------------------------------------------


def make_job(make_runtime: MakeRuntime, gateway: FakeGateway, **kw: object) -> Runtime:
    rt = make_runtime(gateway=gateway, **kw)  # type: ignore[arg-type]
    made = runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes"], obj=rt)
    assert made.exit_code == 0, made.output
    return rt


def test_run_by_id_and_by_name(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    src, dst = source_with_messages(gateway, 2)
    rt = make_job(make_runtime, gateway)

    by_id = runner.invoke(app, ["run", "1"], obj=rt)
    gateway.add_message(src, "m3")
    by_name = runner.invoke(app, ["run", "Source → Copy"], obj=rt)

    assert by_id.exit_code == by_name.exit_code == 0, by_name.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"]  # the second run copied only the new one
    assert [c.args[2] for c in gateway.calls_to("copy_messages")] == [[1, 2], [3]]


def test_run_unknown_job_exits_2(make_runtime: MakeRuntime) -> None:
    result = runner.invoke(app, ["run", "42"], obj=make_runtime())

    assert result.exit_code == 2 and "No job '42'" in result.output


def test_run_needs_a_login_and_touches_nothing_otherwise(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_job(make_runtime, gateway)
    logged_out = make_runtime(gateway=gateway, auth=FakeAuth(), root=rt.paths.data_dir.parent)

    result = runner.invoke(app, ["run", "1"], obj=logged_out)

    assert result.exit_code == 1 and "tgmirror login" in result.output
    assert gateway.calls_to("copy_messages") == []


def test_a_flood_wait_saves_the_job_and_exits_3_then_refuses_to_rerun_at_once(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_job(make_runtime, gateway)
    gateway.fail_next("copy_messages", FloodWait(600))

    first = runner.invoke(app, ["run", "1"], obj=rt)
    second = runner.invoke(app, ["run", "1"], obj=rt)

    assert first.exit_code == 3 and "600s" in first.output
    assert saved_jobs(rt)[0].status is JobStatus.WAITING_FLOOD
    assert second.exit_code == 3 and "must not run again before" in second.output
    assert len(gateway.calls_to("copy_messages")) == 1  # the second run never got to Telegram


def test_a_restricted_source_exits_4_with_advice(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, noforwards=True, is_admin=True)
    rt = make_job(make_runtime, gateway)

    result = runner.invoke(app, ["run", "1"], obj=rt)

    assert result.exit_code == 4
    assert "Restrict saving content" in result.output and "phase 6" in result.output
    assert saved_jobs(rt)[0].status is JobStatus.FAILED


def test_a_job_held_by_another_process_is_refused_unless_taken_over(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_job(make_runtime, gateway)
    claim(rt, 1)

    refused = runner.invoke(app, ["run", "1"], obj=rt)
    forced = runner.invoke(app, ["run", "1", "--force-takeover"], obj=rt)

    assert refused.exit_code == 1 and "--force-takeover" in refused.output
    assert forced.exit_code == 0, forced.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"]


def test_ctrl_c_saves_and_exits_130(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    """The first Ctrl+C finishes the batch in flight, saves, and leaves with 130."""
    _, dst = source_with_messages(gateway, 4)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--batch-size", "2"], obj=rt)
    original = gateway.copy_messages

    async def interrupt_then_copy(s: int, d: int, ids: list[int]) -> list[int | None]:
        signal.raise_signal(signal.SIGINT)  # what the terminal does
        return await original(s, d, ids)

    gateway.copy_messages = interrupt_then_copy  # type: ignore[method-assign]

    result = runner.invoke(app, ["run", "1"], obj=rt)

    assert result.exit_code == 130, result.output
    assert texts(gateway, dst) == ["m1", "m2"]  # the batch in flight was finished
    (job,) = saved_jobs(rt)
    assert (job.status, job.cursor_src_id) == (JobStatus.STOPPED, 2)
    assert "Stopping after the current batch" in result.output


# ---- pause / stop ---------------------------------------------------------------------------


def test_pause_and_stop_a_running_job(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    source_with_messages(gateway)
    rt = make_job(make_runtime, gateway)
    claim(rt, 1)

    paused = runner.invoke(app, ["pause", "1"], obj=rt)
    assert paused.exit_code == 0 and "pause" in paused.output
    assert control_of(rt, 1) is Control.PAUSE

    stopped = runner.invoke(app, ["stop", "Source → Copy"], obj=rt)
    assert stopped.exit_code == 0 and "stop" in stopped.output
    assert control_of(rt, 1) is Control.STOP


def test_pause_and_stop_a_job_that_is_not_running_change_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway)
    rt = make_job(make_runtime, gateway)

    paused = runner.invoke(app, ["pause", "1"], obj=rt)
    stopped = runner.invoke(app, ["stop", "1"], obj=rt)

    assert paused.exit_code == stopped.exit_code == 1
    assert "not running" in paused.output and "created" in paused.output
    assert control_of(rt, 1) is Control.NONE


def test_pause_unknown_job_exits_2(make_runtime: MakeRuntime) -> None:
    assert runner.invoke(app, ["pause", "9"], obj=make_runtime()).exit_code == 2
