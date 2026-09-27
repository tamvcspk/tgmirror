"""The screen a ``backup`` drives while it saves messages to disk (Phase 15,
docs/06-lo-trinh.md) — mirrors ``run_screen.py::RunScreen``, but drives ``BackupWriter``/``Backup``
instead of ``Runner``/``Run``: ``engine/backup.py`` already duplicates ``engine/runner.py``'s
control loop rather than sharing a base class (no destination, no ``msg_map``), so this screen does
the same instead of forcing one abstraction over two different progress shapes.

``BackupTuiReporter`` (below) fills the same role here that ``ui/tui.py::TuiReporter`` fills for
``RunScreen``: a pure, ``Live``-free ``render()`` the screen pulls each redraw. It cannot reuse
``TuiReporter`` because ``engine.backup.Reporter.progress`` carries a ``Backup``, not a ``Run`` (a
backup has no ``total_items``/ETA yet — Phase 11a has no analyze step, out of this phase's scope).

The run/backup control loop itself (``stop_on_interrupt``, ``p``/``r``/``q``, reporting the outcome
once, exit 130 on a confirmed Ctrl+C) lives in ``ui/menu/task_screen.py::TaskScreen`` — this screen
only supplies the task, the reporter and the result lines (Phase 15b, L1).
"""

import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.core.config import Limits
from tgmirror.core.gateway import TelegramGateway
from tgmirror.engine.backup import BackupWriter
from tgmirror.engine.backupdir import BackupManifest
from tgmirror.engine.transfer import Transfer
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store
from tgmirror.ui.lines import backup_result_lines, backup_start_line
from tgmirror.ui.menu.task_screen import TaskScreen
from tgmirror.ui.messages import t
from tgmirror.ui.progress import duration


class BackupTuiReporter:
    """Renders a backup in flight, driven by the same three ``Reporter`` calls as
    ``BackupLineReporter`` (``ui/progress.py``). Not a context manager (unlike ``TuiReporter``): it
    owns no ``Live`` of its own — ``BackupScreen`` pulls ``render()`` each redraw, same as
    ``RunScreen`` does with ``TuiReporter``."""

    def __init__(self, backup: Backup, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._backup = backup
        self._clock = clock
        self._floods = 0
        self._last_flood: float | None = None
        self._paused = False

    def notice(self, code: str, **params: object) -> None:
        if code == "paused":
            self._paused = True
        elif code == "resumed":
            self._paused = False
        if code in ("flood_waiting", "throttled"):
            self._floods += 1
            self._last_flood = self._clock()

    def progress(self, backup: Backup) -> None:
        self._backup = backup

    def transfer(self, transfer: Transfer) -> None:
        pass  # no per-file lines yet (matches BackupLineReporter)

    def render(self) -> RenderableType:
        # no header line here: ``BackupScreen._lines`` already printed "Lần backup ..." once above
        # this panel, and the menu's footer already shows the hotkey hint (``Screen.footer_hint``)
        b = self._backup
        counts = t(
            "backup.progress", id=b.id, done=b.done, skipped=b.skipped_filter, cursor=b.cursor_to
        )
        if self._floods:
            ago = duration(timedelta(seconds=max(self._clock() - (self._last_flood or 0.0), 0.0)))
            counts += t("tui.floods", count=self._floods, ago=ago)
        lines = [Text(counts)]
        if self._paused:
            lines.append(Text(t("backup.paused")))
        return Group(*lines)


class BackupScreen(TaskScreen[Backup]):
    def __init__(
        self,
        store: Store,
        gateway: TelegramGateway,
        backup: Backup,
        directory: Path,
        manifest: BackupManifest,
        *,
        limits: Limits,
    ) -> None:
        super().__init__()
        self.backup_id = backup.id  # MenuApp reads this so its "running elsewhere" badge skips it
        self._store = store
        self._gateway = gateway
        self._backup = backup
        self._directory = directory
        self._manifest = manifest
        self._limits = limits
        self._reporter = BackupTuiReporter(backup)
        self._lines = [backup_start_line(backup.id, backup.src_title, backup.dir)]

    async def _run(self) -> Backup:
        writer = BackupWriter(
            self._store,
            self._gateway,
            self._limits,
            reporter=self._reporter,
            control=self._control,
        )
        return await writer.run(self._backup, self._directory, self._manifest)

    def _result_lines(self, final: Backup) -> list[str]:
        return backup_result_lines(final)
