"""``BackupScreen``: Phase 15's "Backup" screen — drives the real ``BackupWriter``/``RunControl``
without a private ``Live`` (``BackupTuiReporter`` is only ever asked for ``render()``), mirroring
``tests/unit/test_run_screen.py`` for ``RunScreen``. No terminal, no CLI: exercised straight
against ``FakeGateway`` and a real (temp-file) ``Store``.
"""

import contextlib
import io
from pathlib import Path
from typing import Any

from rich.console import Console, RenderableType

from tests.fakes import FakeGateway
from tgmirror.core.config import Limits
from tgmirror.engine import backupdir
from tgmirror.engine.backup import begin_backup
from tgmirror.store.db import Store
from tgmirror.store.runs import RunStatus
from tgmirror.ui.menu.backup_screen import BackupScreen
from tgmirror.ui.messages import t


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, width=200, no_color=True)
    console.print(renderable)
    return console.export_text()


async def _store(tmp_path: Path) -> Store:
    return await Store.open(tmp_path / "t.db")


async def test_backup_screen_saves_and_shows_the_result(tmp_path: Path) -> None:
    gateway = FakeGateway()
    src = gateway.add_channel("Source")
    for i in range(1, 6):
        gateway.add_message(src.id, f"m{i}")
    store = await _store(tmp_path)
    directory = tmp_path / "out"
    backup, manifest = await begin_backup(store, gateway, src, directory)
    screen = BackupScreen(store, gateway, backup, directory, manifest, limits=Limits())

    await screen.on_enter()
    assert screen.task is not None
    await screen.task  # the backup itself, driven with FakeGateway (no real delay)
    result = await screen.tick()

    assert result is None  # a normal finish stays on the screen, does not quit the app
    text = plain(screen.render())
    assert "5" in text
    assert len(backupdir.iter_records(directory)) == 5
    await store.close()


async def test_q_key_stops_the_backup_without_quitting_the_app(tmp_path: Path) -> None:
    gateway = FakeGateway()
    src = gateway.add_channel("Source")
    for i in range(1, 4):
        gateway.add_message(src.id, f"m{i}")
    store = await _store(tmp_path)
    directory = tmp_path / "out"
    backup, manifest = await begin_backup(store, gateway, src, directory)
    screen = BackupScreen(store, gateway, backup, directory, manifest, limits=Limits())

    await screen.on_enter()
    await screen.handle_key("q")  # the same call the menu's key loop makes for a plain character
    assert screen.task is not None
    final = await screen.task

    assert final.status is RunStatus.STOPPED
    result = await screen.tick()
    assert result is None  # `q` (not Ctrl+C): back to the menu, not out of the app
    await store.close()


async def test_a_crash_shows_the_same_sentence_the_cli_would(tmp_path: Path) -> None:
    """N4, Phase 15b: mirrors ``test_run_screen.py``'s analogous test — a crash that is not
    ``TgMirrorError`` used to show a bare ``str(exc)`` here instead of the sentence the classic
    CLI shows for the exact same crash (``cli/errors.py::describe_any``)."""
    gateway = FakeGateway()
    src = gateway.add_channel("Source")
    gateway.add_message(src.id, "m1")

    async def die(src_id: int, unit: Any, media_dir: Path, on_transfer: Any = None) -> Any:
        raise RuntimeError("boom")

    gateway.export_unit = die  # type: ignore[method-assign]
    store = await _store(tmp_path)
    directory = tmp_path / "out"
    backup, manifest = await begin_backup(store, gateway, src, directory)
    screen = BackupScreen(store, gateway, backup, directory, manifest, limits=Limits())

    await screen.on_enter()
    assert screen.task is not None
    with contextlib.suppress(RuntimeError):  # the task re-raises it; tick() reads it back, not us
        await screen.task
    await screen.tick()

    assert t("err.crashed", detail="RuntimeError: boom") in screen._lines
    await store.close()


async def test_a_finished_backup_reports_its_result_once_not_every_tick(tmp_path: Path) -> None:
    """Regression pattern mirrored from ``test_run_screen.py``: a finished screen sitting on top
    while ``MenuApp`` keeps ticking it must not re-append its result line every time."""
    gateway = FakeGateway()
    src = gateway.add_channel("Source")
    gateway.add_message(src.id, "m1")
    store = await _store(tmp_path)
    directory = tmp_path / "out"
    backup, manifest = await begin_backup(store, gateway, src, directory)
    screen = BackupScreen(store, gateway, backup, directory, manifest, limits=Limits())

    await screen.on_enter()
    assert screen.task is not None
    await screen.task
    await screen.tick()
    lines_after_first_tick = len(screen._lines)
    for _ in range(5):  # the app would call this every 0.25s while nothing else happens
        await screen.tick()

    assert len(screen._lines) == lines_after_first_tick
    await store.close()
