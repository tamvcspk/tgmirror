"""Phase 15: "Backup"/"Restore" in the full-screen menu — the same ``BackupFlow``/``RestoreFlow``
as the CLI (``cli/commands/backup.py``/``cli/commands/restore.py``), answered by keys, handing over
to ``BackupScreen``/``RunScreen``. Mirrors ``test_menu_wizard.py``'s style for ``clone_screen``.

Also covers the ``ResumeScreen`` gap found while wiring Restore in: before this phase,
``RunScreen`` never received a ``reader_override``, so continuing a restore-derived pair from the
menu's "Continue"/"Retry failures" silently read the live gateway instead of the backup directory.
"""

import io
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console, RenderableType

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tgmirror.cli.keys import MenuKey
from tgmirror.cli.runtime import Connection, Runtime
from tgmirror.core.config import Limits
from tgmirror.core.gateway import ChannelInfo, ChatKind, ExportedMessage
from tgmirror.engine import backupdir
from tgmirror.engine.backup import begin_backup
from tgmirror.engine.backup_reader import BackupReader
from tgmirror.engine.runner import Runner
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store
from tgmirror.store.runs import Run
from tgmirror.ui.menu.app import MenuApp
from tgmirror.ui.menu.backup_screen import BackupScreen
from tgmirror.ui.menu.run_screen import RunScreen
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.backup import backup_screen
from tgmirror.ui.menu.screens.history import (
    BackupHistoryDetailScreen,
    HistoryDetailScreen,
    HistoryScreen,
)
from tgmirror.ui.menu.screens.restore import restore_screen
from tgmirror.ui.menu.screens.resume import ResumeScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t

MakeRuntime = Callable[..., Runtime]
Key = MenuKey | str
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, no_color=True, width=120)
    console.print(renderable)
    return console.export_text()


async def press(screen: WizardScreen, *keys: Key) -> ScreenResult:
    result: ScreenResult = "stay"
    for key in keys:
        assert screen.prompter.asking, f"no open question for {key!r}: {plain(screen.render())}"
        result = await screen.handle_key(key)
    return result


def asked(screen: WizardScreen) -> str:
    assert screen.prompter.question is not None
    return screen.prompter.question.message


async def make_app(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway, auth: FakeAuth
) -> MenuApp:
    runtime = make_runtime(gateway=gateway, auth=auth, interactive=True)
    store = await Store.open(tmp_path / "t.db")
    account = await auth.account()
    return MenuApp(runtime, store, Connection(auth, gateway), account)


def seed_backup(directory: Path, *, text: str = "m1") -> backupdir.BackupManifest:
    """A finished backup directory, without going through ``BackupWriter``/a ``Store`` (restore
    only ever reads ``backup.json``/``messages.jsonl`` off disk) — mirrors
    ``tests/integration/test_restore.py``'s ``Rig.seed``."""
    manifest = backupdir.BackupManifest(
        format_version=backupdir.FORMAT_VERSION,
        tgmirror_version="0.1.0",
        src_id=-1001,
        src_title="Source",
        src_kind=ChatKind.BROADCAST,
        src_about="",
        src_noforwards=False,
        protected_ack=False,
        filters_json="{}",
    )
    backupdir.write_manifest(directory, manifest)
    backupdir.append_records(
        directory,
        [
            ExportedMessage(
                id=1, date=NOW, grouped_id=None, topic_id=None, from_user_id=None,
                text_html=text, views=None,
            )
        ],
    )  # fmt: skip
    return manifest


async def test_a_new_backup_asks_like_the_cli_and_hands_over_to_the_backup_screen(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    gateway.add_channel("Source")
    app_ = await make_app(make_runtime, tmp_path, gateway, FakeAuth(logged_in=ACCOUNT))
    screen = backup_screen(app_)

    assert await screen.on_enter() == "stay"
    assert asked(screen) == t("clone.pick_source")
    await press(screen, MenuKey.ENTER)  # Source
    assert asked(screen) == t("backup.pick_dir")
    directory = tmp_path / "out"
    await press(screen, *str(directory), MenuKey.ENTER)
    assert asked(screen) == t("filter.pick")
    await press(screen, MenuKey.ENTER)  # No filter
    assert asked(screen).startswith(t("backup.confirm_start", src="", dir="")[:4])
    result = await press(screen, MenuKey.ENTER)  # Yes

    assert result[0] == "replace" and isinstance(result[1], BackupScreen)
    await app_.store.close()


async def test_a_restore_hands_over_to_the_run_screen_with_a_backup_reader(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    out = tmp_path / "out"
    seed_backup(out)

    app_ = await make_app(make_runtime, tmp_path, gateway, FakeAuth(logged_in=ACCOUNT))
    screen = restore_screen(app_)

    assert await screen.on_enter() == "stay"
    assert asked(screen) == t("restore.pick_dir")
    await press(screen, *str(out), MenuKey.ENTER)
    assert asked(screen) == t("restore.pick_destination")
    await press(screen, MenuKey.ENTER)  # "+ Create new"
    await press(screen, *"Restored", MenuKey.ENTER, MenuKey.ENTER)  # title, empty description
    assert asked(screen) == t("filter.pick")
    await press(screen, MenuKey.ENTER)  # No filter
    result = await press(screen, MenuKey.ENTER)  # Yes, start

    assert result[0] == "replace" and isinstance(result[1], RunScreen)
    assert isinstance(result[1]._reader_override, BackupReader)
    await app_.store.close()


async def test_resume_of_a_restored_pair_reads_the_backup_directory_not_the_gateway(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """Regression: before Phase 15 wired ``reader_override`` through ``RunScreen``, "Continue" on
    a restore-derived pair from the menu would read the live gateway instead of the backup
    directory (``engine.backup_reader.BackupReader``)."""
    gateway = FakeGateway()
    dst = gateway.add_channel("Restored")
    out = tmp_path / "out"
    manifest = seed_backup(out)
    src = ChannelInfo(manifest.src_id, manifest.src_title, manifest.src_kind)
    store = await Store.open(tmp_path / "t.db")
    request = RunRequest(mode="reupload", from_backup=str(out), reset_polls=True)
    started = await begin_run(store, gateway, src, dst, request)
    reader = BackupReader(out, manifest)
    runner = Runner(store, gateway, Limits(), reader_override=reader, tmp_dir=tmp_path / "tmp")
    await runner.run(started.run)

    app_ = MenuApp(
        make_runtime(gateway=gateway, interactive=True),
        store,
        Connection(FakeAuth(logged_in=ACCOUNT), gateway),
        ACCOUNT,
    )
    screen = ResumeScreen(app_, mode="resume")
    result = await screen.on_enter()  # a single pair: goes straight in, like the classic CLI

    assert result is not None and result[0] == "push"
    run_screen = result[1]
    assert isinstance(run_screen, RunScreen)
    assert isinstance(run_screen._reader_override, BackupReader)
    await store.close()


async def test_resume_offers_recovery_instead_of_crashing_when_the_backup_dir_is_gone(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """N6, Phase 15b: before this fix, ``begin_run`` silently fell back to a live re-check of the
    source once the restore directory went missing, then ``ResumeScreen`` crashed the whole app on
    ``reader_override_for``'s ``assert`` once the run row had already been created. Now
    ``ResumeScreen`` offers a recovery dialog (``BackupDirMissing``) instead of dying, and picking
    "Huỷ" pops back cleanly — the whole app never crashes."""
    gateway = FakeGateway()
    dst = gateway.add_channel("Restored")
    out = tmp_path / "out"
    manifest = seed_backup(out)
    src = ChannelInfo(manifest.src_id, manifest.src_title, manifest.src_kind)
    store = await Store.open(tmp_path / "t.db")
    request = RunRequest(mode="reupload", from_backup=str(out), reset_polls=True)
    started = await begin_run(store, gateway, src, dst, request)
    reader = BackupReader(out, manifest)
    runner = Runner(store, gateway, Limits(), reader_override=reader, tmp_dir=tmp_path / "tmp")
    await runner.run(started.run)
    shutil.rmtree(out)  # the directory this pair points at is now gone

    app_ = MenuApp(
        make_runtime(gateway=gateway, interactive=True),
        store,
        Connection(FakeAuth(logged_in=ACCOUNT), gateway),
        ACCOUNT,
    )
    screen = ResumeScreen(app_, mode="resume")
    result = await screen.on_enter()  # a single pair: goes straight in, like the classic CLI

    assert result is not None and result[0] == "push"
    recovery = result[1]
    assert isinstance(recovery, WizardScreen)
    assert await recovery.on_enter() == "stay"
    assert asked(recovery) == str(out)  # the missing directory is named in the question

    outcome = await press(recovery, MenuKey.DOWN, MenuKey.DOWN, MenuKey.ENTER)  # "Huỷ" (3rd item)

    assert outcome == "pop"
    await store.close()


async def test_resume_recovers_by_repointing_at_a_new_backup_directory(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """The "Chọn thư mục backup khác" branch of the same recovery dialog: picking a directory that
    backs up the same source (``src_id``) hands over to a ``RunScreen`` reading from it, exactly
    like an ordinary "Chạy tiếp" would."""
    gateway = FakeGateway()
    dst = gateway.add_channel("Restored")
    out = tmp_path / "out"
    manifest = seed_backup(out)
    src = ChannelInfo(manifest.src_id, manifest.src_title, manifest.src_kind)
    store = await Store.open(tmp_path / "t.db")
    request = RunRequest(mode="reupload", from_backup=str(out), reset_polls=True)
    started = await begin_run(store, gateway, src, dst, request)
    reader = BackupReader(out, manifest)
    runner = Runner(store, gateway, Limits(), reader_override=reader, tmp_dir=tmp_path / "tmp")
    await runner.run(started.run)
    moved = tmp_path / "moved"
    shutil.copytree(out, moved)  # a copy of the same backup, at a new path
    shutil.rmtree(out)

    app_ = MenuApp(
        make_runtime(gateway=gateway, interactive=True),
        store,
        Connection(FakeAuth(logged_in=ACCOUNT), gateway),
        ACCOUNT,
    )
    screen = ResumeScreen(app_, mode="resume")
    result = await screen.on_enter()
    assert result is not None and result[0] == "push"
    recovery = result[1]
    assert isinstance(recovery, WizardScreen)
    assert await recovery.on_enter() == "stay"

    await press(recovery, MenuKey.ENTER)  # "Chọn thư mục backup khác" (1st item)
    assert asked(recovery) == t("restore.pick_dir")
    outcome = await press(recovery, *str(moved), MenuKey.ENTER)

    assert outcome[0] == "push" and isinstance(outcome[1], RunScreen)
    assert isinstance(outcome[1]._reader_override, BackupReader)
    await store.close()


async def test_history_screen_lists_runs_and_backups_together(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """T1, Phase 15b: the menu's "Lịch sử" had the same gap as `tgmirror history` — only runs,
    never backups. Selecting a backup row goes to its own (simpler) detail screen."""
    gateway = FakeGateway()
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    gateway.add_message(src.id, "m1")
    store = await Store.open(tmp_path / "t.db")
    await begin_run(store, gateway, src, dst, RunRequest())
    await begin_backup(store, gateway, src, tmp_path / "out")

    app_ = MenuApp(
        make_runtime(gateway=gateway, interactive=True),
        store,
        Connection(FakeAuth(logged_in=ACCOUNT), gateway),
        ACCOUNT,
    )
    screen = HistoryScreen(app_)
    await screen.on_enter()

    items = [value for _, value in screen._list.items]
    assert {type(i) for i in items} == {Run, Backup}

    backup_index = next(i for i, v in enumerate(items) if isinstance(v, Backup))
    run_index = next(i for i, v in enumerate(items) if isinstance(v, Run))
    screen._list.index = backup_index
    backup_result = await screen.handle_key(MenuKey.ENTER)
    assert backup_result[0] == "push" and isinstance(backup_result[1], BackupHistoryDetailScreen)

    screen._list.index = run_index
    run_result = await screen.handle_key(MenuKey.ENTER)
    assert run_result[0] == "push" and isinstance(run_result[1], HistoryDetailScreen)

    await store.close()
