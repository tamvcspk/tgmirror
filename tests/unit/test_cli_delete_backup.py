"""The safety net of ``tgmirror delete`` through the real Typer app (Phase 20.2): the backup goes
first, nothing is deleted unless it finished, and ``status`` / ``history`` know the operation.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.errors import NoPermission
from tgmirror.core.gateway import ChannelInfo
from tgmirror.engine import backupdir
from tgmirror.store.ops import Op, OpStatus

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]
ADS = ["--hashtag", "#ads"]
CONFIRMED = ["--yes", "--expect", "3"]


def fill(gateway: FakeGateway, ads: int = 3, other: int = 2) -> ChannelInfo:
    chat = gateway.add_channel("Chat")
    for i in range(ads):
        gateway.add_message(chat.id, f"ad {i} #ads", hashtags=("#ads",))
    for i in range(other):
        gateway.add_message(chat.id, f"keep {i}")
    return chat


def ids(gateway: FakeGateway, chat: ChannelInfo) -> list[int]:
    return [m.id for m in gateway.messages[chat.id]]


def saved_ids(directory: Path) -> list[int]:
    return [r.id for r in backupdir.iter_records(directory)]


def op_of(rt: Runtime, op_id: int = 1) -> Op | None:
    async def read() -> Op | None:
        async with opened_store(rt) as store:
            return await store.get_op(op_id)

    return asyncio.run(read())


def test_backup_to_saves_the_plan_then_deletes(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    directory = tmp_path / "net"

    result = cli.invoke(
        app, ["delete", "Chat", *ADS, *CONFIRMED, "--backup-to", str(directory)], obj=rt
    )

    assert result.exit_code == 0, result.output
    assert saved_ids(directory) == [1, 2, 3]  # the three planned, not the two others
    assert ids(gateway, chat) == [4, 5]
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.DONE
    assert op.backup_dir == str(directory.resolve()) and op.backup_done
    assert str(directory.resolve()) in result.output
    assert result.output.index("tgmirror restore") < result.output.index("O1: ")  # backup first


def test_without_a_flag_and_without_a_terminal_there_is_no_backup(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["delete", "Chat", *ADS, *CONFIRMED], obj=rt)

    assert result.exit_code == 0, result.output
    op = op_of(rt)
    assert op is not None and op.backup_dir is None
    assert not (rt.paths.data_dir / "safety").exists()


def test_backup_to_and_no_backup_exclude_each_other(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app,
        ["delete", "Chat", *ADS, *CONFIRMED, "--backup-to", str(tmp_path), "--no-backup"],
        obj=rt,
    )

    assert result.exit_code == 2
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]
    assert op_of(rt) is None  # refused before it even planned


def test_at_a_terminal_it_asks_and_the_default_is_a_backup_in_the_data_dir(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    prompter = ScriptedPrompter(text=["3"])  # the count; the two confirmations default to Yes
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["delete", "Chat", *ADS], obj=rt)

    assert result.exit_code == 0, result.output
    safety = rt.paths.data_dir / "safety" / "O1"
    assert saved_ids(safety) == [1, 2, 3]
    assert ids(gateway, chat) == [4, 5]
    kinds = [kind for kind, _ in prompter.asked]
    assert kinds == ["text", "confirm", "confirm"]  # count, backup?, where?


def test_at_a_terminal_a_no_to_the_backup_deletes_without_one(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    prompter = ScriptedPrompter(text=["3"], confirm=[False])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["delete", "Chat", *ADS], obj=rt)

    assert result.exit_code == 0, result.output
    assert ids(gateway, chat) == [4, 5]
    op = op_of(rt)
    assert op is not None and op.backup_dir is None


def test_at_a_terminal_another_directory_can_be_typed(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    elsewhere = tmp_path / "elsewhere"
    prompter = ScriptedPrompter(text=["3", str(elsewhere)], confirm=[True, False])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["delete", "Chat", *ADS], obj=rt)

    assert result.exit_code == 0, result.output
    assert saved_ids(elsewhere) == [1, 2, 3]


def test_no_backup_skips_the_question(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    fill(gateway)
    prompter = ScriptedPrompter(text=["3"])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["delete", "Chat", *ADS, "--no-backup"], obj=rt)

    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in prompter.asked] == ["text"]  # only the count


def test_a_backup_that_fails_deletes_nothing_and_the_plan_is_kept(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    gateway.fail_next("export_unit", NoPermission("cannot download"))
    rt = make_runtime(gateway=gateway)
    directory = tmp_path / "net"
    args = ["delete", "Chat", *ADS, *CONFIRMED, "--backup-to", str(directory)]

    failed = cli.invoke(app, args, obj=rt)

    assert failed.exit_code != 0
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]  # nothing deleted
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.PLANNED and not op.backup_done
    assert op.backup_dir == str(directory.resolve())


def test_resume_carries_on_the_unfinished_backup_where_it_was(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    gateway.fail_next("export_unit", NoPermission("cannot download"))
    rt = make_runtime(gateway=gateway)
    directory = tmp_path / "net"
    first = cli.invoke(
        app, ["delete", "Chat", *ADS, *CONFIRMED, "--backup-to", str(directory)], obj=rt
    )
    assert first.exit_code != 0

    result = cli.invoke(app, ["delete", "--resume", "O1", *CONFIRMED], obj=rt)  # no flag again

    assert result.exit_code == 0, result.output
    assert saved_ids(directory) == [1, 2, 3]
    assert ids(gateway, chat) == [4, 5]
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.DONE and op.backup_done


def test_a_protected_chat_needs_the_statement_for_the_backup(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    gateway.channels[chat.id] = replace(chat, noforwards=True)
    rt = make_runtime(gateway=gateway)
    directory = tmp_path / "net"
    base = ["delete", "Chat", *ADS, *CONFIRMED, "--backup-to", str(directory)]

    refused = cli.invoke(app, base, obj=rt)

    assert refused.exit_code == 2 and "--yes-i-administer-this-channel" in refused.output
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]
    assert not directory.exists()

    # the plan was kept, and so was the wish for a backup: resuming without the directory still
    # backs up (and without the statement still refuses, rather than deleting unprotected)
    again = cli.invoke(app, ["delete", "--resume", "O1", *CONFIRMED], obj=rt)
    assert again.exit_code == 2 and ids(gateway, chat) == [1, 2, 3, 4, 5]

    allowed = cli.invoke(
        app, ["delete", "--resume", "O1", *CONFIRMED, "--yes-i-administer-this-channel"], obj=rt
    )

    assert allowed.exit_code == 0, allowed.output
    assert saved_ids(directory) == [1, 2, 3] and ids(gateway, chat) == [4, 5]


def test_history_and_status_know_the_operation_and_its_backup(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    directory = tmp_path / "net"
    assert (
        cli.invoke(
            app, ["delete", "Chat", *ADS, *CONFIRMED, "--backup-to", str(directory)], obj=rt
        ).exit_code
        == 0
    )

    detail = cli.invoke(app, ["history", "O1"], obj=rt)
    assert detail.exit_code == 0 and str(directory.resolve()) in detail.output

    status = cli.invoke(app, ["status"], obj=rt)
    assert status.exit_code == 0, status.output
    assert "O1" in status.output and "Chat" in status.output and "100%" in status.output

    record = json.loads(cli.invoke(app, ["status", "--json"], obj=rt).output)
    assert record["operation"] == 1 and record["done"] == 3 and record["status"] == "done"
    assert record["progress"] == 1.0
    assert record["backup"] == {"directory": str(directory.resolve()), "finished": True}


def test_status_shows_the_latest_thing_whatever_it_is(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    assert (
        cli.invoke(app, ["delete", "Chat", *ADS, "--yes", "--expect", "3"], obj=rt).exit_code == 0
    )
    assert (
        cli.invoke(app, ["clone", "--src", "Chat", "--dst-new", "Copy", "--yes"], obj=rt).exit_code
        == 0
    )

    status = json.loads(cli.invoke(app, ["status", "--json"], obj=rt).output)

    assert status["kind"] == "run"  # the clone is newer than the delete
    assert ids(gateway, chat) == [4, 5]


def test_a_saved_plan_remembers_the_backup_it_was_given(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    directory = tmp_path / "net"
    saved = cli.invoke(app, ["delete", "Chat", *ADS, "--backup-to", str(directory)], obj=rt)
    assert saved.exit_code == 2 and not directory.exists()

    result = cli.invoke(app, ["delete", "--resume", "O1", *CONFIRMED], obj=rt)  # no flag again

    assert result.exit_code == 0, result.output
    assert saved_ids(directory) == [1, 2, 3] and ids(gateway, chat) == [4, 5]


def test_a_stopped_safety_backup_does_not_point_at_the_backup_command(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    gateway.fail_next("export_unit", NoPermission("cannot download"))
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app, ["delete", "Chat", *ADS, *CONFIRMED, "--backup-to", str(tmp_path / "net")], obj=rt
    )

    assert "tgmirror backup" not in result.output
