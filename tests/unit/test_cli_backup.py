"""``tgmirror backup`` through the real Typer app: no terminal, no network."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.gateway import ChannelInfo, MediaKind
from tgmirror.engine import backupdir
from tgmirror.store.backups import BackupSpec
from tgmirror.store.runs import Control, RunStatus

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]


def fill(gateway: FakeGateway, count: int = 2) -> ChannelInfo:
    src = gateway.add_channel("Source")
    for i in range(1, count + 1):
        gateway.add_message(src.id, f"m{i}")
    return src


def control_of(rt: Runtime, backup_id: int) -> Control:
    async def read() -> Control:
        async with opened_store(rt) as store:
            return await store.read_backup_control(backup_id)

    return asyncio.run(read())


def start_elsewhere(rt: Runtime, src: ChannelInfo, directory: Path) -> int:
    """Pretend another process is already backing this up: a live, beating row."""

    async def do() -> int:
        async with opened_store(rt) as store:
            backup = await store.start_backup(BackupSpec(src=src, dir=str(directory)))
            return backup.id

    return asyncio.run(do())


def test_backup_copies_messages_and_writes_the_directory(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway, 3)
    out = tmp_path / "out"
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["backup", "Source", str(out)], obj=rt)

    assert result.exit_code == 0, result.output
    manifest = backupdir.read_manifest(out)
    assert manifest is not None and manifest.src_title == "Source"
    records = backupdir.iter_records(out)
    assert [r.id for r in records] == [1, 2, 3]
    assert "Backup 1" in result.output
    assert "done" in result.output.lower() or "3" in result.output


def test_running_backup_again_continues_the_delta(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = fill(gateway, 2)
    out = tmp_path / "out"
    rt = make_runtime(gateway=gateway)
    assert cli.invoke(app, ["backup", "Source", str(out)], obj=rt).exit_code == 0

    gateway.add_message(src.id, "m3")
    result = cli.invoke(app, ["backup", "Source", str(out)], obj=rt)

    assert result.exit_code == 0, result.output
    assert [r.id for r in backupdir.iter_records(out)] == [1, 2, 3]


def test_protected_source_needs_the_flag_without_a_terminal(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = gateway.add_channel("Source", noforwards=True, is_admin=False)
    gateway.add_message(src.id, "m1")
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["backup", "Source", str(tmp_path / "out")], obj=rt)

    assert result.exit_code == 4
    assert not backupdir.manifest_path(tmp_path / "out").exists()


def test_protected_source_backed_up_with_the_flag(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = gateway.add_channel("Source", noforwards=True, is_admin=False)
    gateway.add_message(src.id, "m1")
    out = tmp_path / "out"
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app,
        ["backup", "Source", str(out), "--yes-i-administer-this-channel"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    manifest = backupdir.read_manifest(out)
    assert manifest is not None and manifest.protected_ack is True


def test_protected_source_admin_gets_a_plain_confirmation(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = gateway.add_channel("Source", noforwards=True, is_admin=True)
    gateway.add_message(src.id, "m1")
    out = tmp_path / "out"
    prompter = ScriptedPrompter(confirm=[True])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    # --yes skips the final "back up now?" question but never the D3 statement itself
    result = cli.invoke(app, ["backup", "Source", str(out), "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    assert prompter.asked == [("confirm", prompter.asked[0][1])]


def test_a_busy_directory_is_refused_and_force_takeover_overrides(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = fill(gateway, 1)
    out = tmp_path / "out"
    rt = make_runtime(gateway=gateway)
    start_elsewhere(rt, src, out.resolve())

    busy = cli.invoke(app, ["backup", "Source", str(out)], obj=rt)
    assert busy.exit_code != 0

    forced = cli.invoke(app, ["backup", "Source", str(out), "--force-takeover"], obj=rt)
    assert forced.exit_code == 0, forced.output


def test_pause_and_stop_reach_a_live_backup(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = fill(gateway, 1)
    out = tmp_path / "out"
    rt = make_runtime(gateway=gateway)
    backup_id = start_elsewhere(rt, src, out.resolve())

    result = cli.invoke(app, ["pause"], obj=rt)
    assert result.exit_code == 0, result.output
    assert control_of(rt, backup_id) is Control.PAUSE

    result = cli.invoke(app, ["stop"], obj=rt)
    assert result.exit_code == 0, result.output
    assert control_of(rt, backup_id) is Control.STOP


def test_filter_flags_narrow_what_is_backed_up(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = gateway.add_channel("Source")
    gateway.add_message(src.id, "text only")
    gateway.add_message(src.id, media=MediaKind.PHOTO, size=1, mime="image/jpeg")
    out = tmp_path / "out"
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["backup", "Source", str(out), "--media", "photo"], obj=rt)

    assert result.exit_code == 0, result.output
    records = backupdir.iter_records(out)
    assert [r.id for r in records] == [2]


# ---- wizard (mirrors tests/unit/test_cli_clone.py's wizard tests) ------------------------------


def test_wizard_backs_up_the_same_source_as_the_flags(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    """Parity rule: an interactive session and the flags end up saving the same messages."""
    fill(gateway, 2)
    flags_dir = tmp_path / "flags"
    flags = cli.invoke(app, ["backup", "Source", str(flags_dir)], obj=make_runtime(gateway=gateway))

    wizard_gateway = FakeGateway()
    fill(wizard_gateway, 2)
    wizard_dir = tmp_path / "wizard"
    prompter = ScriptedPrompter(
        select=["Source", "No filter"], text=[str(wizard_dir)], confirm=[True]
    )
    wizard_result = cli.invoke(
        app,
        ["backup"],
        obj=make_runtime(
            gateway=wizard_gateway,
            prompter=prompter,
            interactive=True,
            root=tmp_path / "wizard_state",  # its own database: the flags run already saved one
        ),
    )

    assert flags.exit_code == wizard_result.exit_code == 0, wizard_result.output
    flags_ids = [r.id for r in backupdir.iter_records(flags_dir)]
    wizard_ids = [r.id for r in backupdir.iter_records(wizard_dir)]
    assert flags_ids == wizard_ids == [1, 2]


def test_wizard_declining_the_confirmation_backs_up_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway, 2)
    out = tmp_path / "out"
    prompter = ScriptedPrompter(select=["Source", "No filter"], text=[str(out)], confirm=[False])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["backup"], obj=rt)

    assert result.exit_code == 1  # Declined -> err.aborted
    assert not backupdir.manifest_path(out).exists()


def test_wizard_typing_the_flag_lets_a_non_admin_through(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = gateway.add_channel("Source", noforwards=True, is_admin=False)
    gateway.add_message(src.id, "m1")
    out = tmp_path / "out"
    prompter = ScriptedPrompter(
        select=["Source", "No filter"],
        text=[str(out), "--yes-i-administer-this-channel"],
        confirm=[True],
    )
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["backup"], obj=rt)

    assert result.exit_code == 0, result.output
    manifest = backupdir.read_manifest(out)
    assert manifest is not None and manifest.protected_ack is True


def test_wizard_typing_anything_else_still_refuses_an_unadministered_source(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = gateway.add_channel("Source", noforwards=True, is_admin=False)
    gateway.add_message(src.id, "m1")
    out = tmp_path / "out"
    prompter = ScriptedPrompter(select=["Source"], text=[str(out), "sure, whatever"])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["backup"], obj=rt)

    assert result.exit_code == 4
    assert not backupdir.manifest_path(out).exists()


def test_wizard_keeps_filter_and_skips_question_on_resume(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = gateway.add_channel("Source")
    gateway.add_message(src.id, media=MediaKind.PHOTO, size=1, mime="image/jpeg")
    out = tmp_path / "out"
    rt = make_runtime(gateway=gateway)
    flags_result = cli.invoke(app, ["backup", "Source", str(out), "--media", "photo"], obj=rt)
    assert flags_result.exit_code == 0, flags_result.output

    gateway.add_message(src.id, media=MediaKind.PHOTO, size=1, mime="image/jpeg")
    prompter = ScriptedPrompter(select=["Source"], text=[str(out)], confirm=[True])
    rt2 = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["backup"], obj=rt2)

    assert result.exit_code == 0, result.output
    assert "keeping its filter" in result.output.lower()
    assert [r.id for r in backupdir.iter_records(out)] == [1, 2]


def test_wizard_refused_before_filter_questions_when_waiting_on_a_flood(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = fill(gateway, 1)
    out = tmp_path / "out"
    rt = make_runtime(gateway=gateway)

    async def park() -> None:
        async with opened_store(rt) as store:
            parked = await store.start_backup(BackupSpec(src=src, dir=str(out.resolve())))
            await store.finish_backup(
                parked.id,
                RunStatus.WAITING_FLOOD,
                resume_at=datetime.now(UTC) + timedelta(hours=1),
            )

    asyncio.run(park())
    prompter = ScriptedPrompter(select=["Source"], text=[str(out)])
    rt2 = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["backup"], obj=rt2)

    assert result.exit_code == 3
    assert len(prompter.select_labels) == 1  # only source picked: no filter question was asked
