"""``tgmirror restore`` through the real Typer app: no terminal, no network (phase 11b)."""

import asyncio
import json
import shutil
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.gateway import ChannelInfo
from tgmirror.store.runs import RunSpec

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]


def backed_up(
    make_runtime: MakeRuntime,
    gateway: FakeGateway,
    out: Path,
    *,
    count: int = 2,
    noforwards: bool = False,
    is_admin: bool = True,
) -> ChannelInfo:
    src = gateway.add_channel("Source", noforwards=noforwards, is_admin=is_admin)
    for i in range(1, count + 1):
        gateway.add_message(src.id, f"m{i}")
    flags = ["backup", "Source", str(out)]
    if noforwards:
        flags.append("--yes-i-administer-this-channel")
    rt = make_runtime(gateway=gateway)
    result = cli.invoke(app, flags, obj=rt)
    assert result.exit_code == 0, result.output
    return src


def test_restore_creates_a_new_destination_and_copies_messages(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    src = backed_up(make_runtime, gateway, out, count=3)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["restore", str(out), "--dst-new", "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    # the destination is auto-named after the backup's source (decisions 6/7, phase 11b)
    created = next(c for c in gateway.channels.values() if c.id != src.id and c.title == "Source")
    assert [m.text for m in gateway.messages[created.id]] == ["m1", "m2", "m3"]


def test_restore_into_an_existing_destination(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    backed_up(make_runtime, gateway, out, count=2)
    dst = gateway.add_channel("Existing dest")
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["restore", str(out), "--dst", "Existing dest", "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    assert [m.text for m in gateway.messages[dst.id]] == ["m1", "m2"]


def test_restore_needs_the_flag_for_a_protected_backup(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    backed_up(make_runtime, gateway, out, count=1, noforwards=True, is_admin=False)
    dst = gateway.add_channel("Dest")
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["restore", str(out), "--dst", "Dest", "--yes"], obj=rt)

    assert result.exit_code == 2
    assert gateway.messages[dst.id] == []


def test_restore_with_the_flag_succeeds_for_a_protected_backup(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    backed_up(make_runtime, gateway, out, count=1, noforwards=True, is_admin=False)
    dst = gateway.add_channel("Dest")
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app,
        ["restore", str(out), "--dst", "Dest", "--yes-i-administer-this-channel", "--yes"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert [m.text for m in gateway.messages[dst.id]] == ["m1"]


def test_wizard_create_new_asks_for_a_name_instead_of_the_source_title(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    """Unlike ``--dst-new`` (auto-named after the backup's source), the interactive wizard's
    "create new" choice asks for a title, same as `clone`'s (`wizard.ask_new_channel`)."""
    out = tmp_path / "out"
    backed_up(make_runtime, gateway, out, count=1)
    gateway.add_channel("Other")  # an eligible existing destination, so "create new" is a real pick
    prompter = ScriptedPrompter(
        select=["Create a new channel", "No filter"],
        text=[str(out), "Typed title", ""],
        confirm=[True],
    )
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["restore"], obj=rt)

    assert result.exit_code == 0, result.output
    created = next(c for c in gateway.channels.values() if c.title == "Typed title")
    assert [m.text for m in gateway.messages[created.id]] == ["m1"]


def test_a_directory_with_no_backup_json_is_refused(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["restore", str(empty), "--dst-new", "--yes"], obj=rt)

    assert result.exit_code == 2


def test_wizard_restores_the_same_content_as_the_flags(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    """Parity rule: an interactive session and the flags end up restoring the same messages."""
    flags_dir = tmp_path / "backup_flags"
    backed_up(make_runtime, gateway, flags_dir, count=2)
    flags_dst = gateway.add_channel("Flags dest")
    flags = cli.invoke(
        app,
        ["restore", str(flags_dir), "--dst", "Flags dest", "--yes"],
        obj=make_runtime(gateway=gateway),
    )
    assert flags.exit_code == 0, flags.output

    wizard_gateway = FakeGateway()
    wizard_dir = tmp_path / "backup_wizard"
    backed_up(make_runtime, wizard_gateway, wizard_dir, count=2)
    wizard_dst = wizard_gateway.add_channel("Wizard dest")
    prompter = ScriptedPrompter(
        select=["Wizard dest", "No filter"], text=[str(wizard_dir)], confirm=[True]
    )
    wizard_result = cli.invoke(
        app,
        ["restore"],
        obj=make_runtime(
            gateway=wizard_gateway,
            prompter=prompter,
            interactive=True,
            root=tmp_path / "wizard_state",
        ),
    )

    assert wizard_result.exit_code == 0, wizard_result.output
    flags_texts = [m.text for m in gateway.messages[flags_dst.id]]
    wizard_texts = [m.text for m in wizard_gateway.messages[wizard_dst.id]]
    assert flags_texts == wizard_texts == ["m1", "m2"]


def test_wizard_declining_the_confirmation_restores_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    backed_up(make_runtime, gateway, out, count=1)
    dst = gateway.add_channel("Dest")
    prompter = ScriptedPrompter(select=["Dest", "No filter"], text=[str(out)], confirm=[False])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["restore"], obj=rt)

    assert result.exit_code == 1  # Declined -> err.aborted
    assert gateway.messages[dst.id] == []


# ---- C1, Phase 15b: --force-takeover had no effect on restore ---------------------------------


def test_restore_force_takeover_actually_forces_a_takeover(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    """``RestoreFlow.ready()`` never passed ``--force-takeover`` into ``RunRequest.force``, so a
    restore pair held by another (dead) process could never be taken over, flag or not."""
    out = tmp_path / "out"
    src = backed_up(make_runtime, gateway, out, count=1)
    dst = gateway.add_channel("Dest")
    rt = make_runtime(gateway=gateway)
    first = cli.invoke(app, ["restore", str(out), "--dst", "Dest", "--yes"], obj=rt)
    assert first.exit_code == 0, first.output

    async def hold() -> None:
        async with opened_store(rt) as store:
            await store.start_run(RunSpec(src, dst))  # a fresh heartbeat: looks alive

    asyncio.run(hold())

    refused = cli.invoke(
        app, ["restore", str(out), "--dst", "Dest", "--yes"], obj=make_runtime(gateway=gateway)
    )
    forced = cli.invoke(
        app,
        ["restore", str(out), "--dst", "Dest", "--yes", "--force-takeover"],
        obj=make_runtime(gateway=gateway),
    )

    assert refused.exit_code == 1, refused.output  # RunBusy
    assert forced.exit_code == 0, forced.output


# ---- N6, Phase 15b: a restore pair whose directory went missing -------------------------------


def test_run_refuses_and_creates_no_row_when_the_backup_dir_is_gone(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    """``begin_run`` must refuse before writing anything — never silently fall back to a live
    re-check of a source that may not even exist any more, and never leave a run row `running`
    with nothing driving it (that combination is what used to crash on an `assert`)."""
    out = tmp_path / "out"
    backed_up(make_runtime, gateway, out, count=2)
    gateway.add_channel("Dest")
    restored = cli.invoke(
        app, ["restore", str(out), "--dst", "Dest", "--yes"], obj=make_runtime(gateway=gateway)
    )
    assert restored.exit_code == 0, restored.output
    before = cli.invoke(app, ["history", "--json"], obj=make_runtime(gateway=gateway)).output

    shutil.rmtree(out)
    result = cli.invoke(app, ["run"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 2, result.output  # BackupDirMissing is a RunError
    assert str(out) in result.output
    after = cli.invoke(app, ["history", "--json"], obj=make_runtime(gateway=gateway)).output
    assert json.loads(after) == json.loads(before)  # no new run row


def test_run_from_backup_repoints_the_pair_and_continues_its_delta(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    src = backed_up(make_runtime, gateway, out, count=2)
    dst = gateway.add_channel("Dest")
    restored = cli.invoke(
        app, ["restore", str(out), "--dst", "Dest", "--yes"], obj=make_runtime(gateway=gateway)
    )
    assert restored.exit_code == 0, restored.output
    assert [m.text for m in gateway.messages[dst.id]] == ["m1", "m2"]

    moved = tmp_path / "moved"
    shutil.copytree(out, moved)
    shutil.rmtree(out)  # the original directory is gone; only the moved copy is left
    gateway.add_message(src.id, "m3")
    backed_up_again = cli.invoke(
        app, ["backup", "Source", str(moved)], obj=make_runtime(gateway=gateway)
    )
    assert backed_up_again.exit_code == 0, backed_up_again.output

    result = cli.invoke(
        app, ["run", "--from-backup", str(moved)], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 0, result.output
    assert [m.text for m in gateway.messages[dst.id]] == ["m1", "m2", "m3"]


def test_run_from_backup_rejects_a_directory_of_a_different_source(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    backed_up(make_runtime, gateway, out, count=1)
    dst = gateway.add_channel("Dest")
    restored = cli.invoke(
        app, ["restore", str(out), "--dst", "Dest", "--yes"], obj=make_runtime(gateway=gateway)
    )
    assert restored.exit_code == 0, restored.output

    other_src = gateway.add_channel("Other source")
    gateway.add_message(other_src.id, "x1")
    other = tmp_path / "other"
    other_backup = cli.invoke(
        app, ["backup", "Other source", str(other)], obj=make_runtime(gateway=gateway)
    )
    assert other_backup.exit_code == 0, other_backup.output

    result = cli.invoke(
        app, ["run", "--from-backup", str(other)], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 2, result.output  # WrongBackupSource
    assert [m.text for m in gateway.messages[dst.id]] == ["m1"]  # nothing sent from the wrong dir


def test_restore_fresh_sends_everything_again(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    backed_up(make_runtime, gateway, out, count=2)
    dst = gateway.add_channel("Dest")
    first = cli.invoke(
        app, ["restore", str(out), "--dst", "Dest", "--yes"], obj=make_runtime(gateway=gateway)
    )
    assert first.exit_code == 0, first.output

    result = cli.invoke(
        app,
        ["restore", str(out), "--dst", "Dest", "--yes", "--fresh"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    assert [m.text for m in gateway.messages[dst.id]] == ["m1", "m2", "m1", "m2"]
