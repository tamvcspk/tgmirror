"""The screen a ``backup`` drives while it saves messages to disk (Phase 15,
docs/06-lo-trinh.md) — mirrors ``run_screen.py::RunScreen``, but drives ``BackupWriter``/``Backup``
instead of ``Runner``/``Run``: ``engine/backup.py`` already duplicates ``engine/runner.py``'s
control loop rather than sharing a base class (no destination, no ``msg_map``), so this screen does
the same instead of forcing one abstraction over two different progress shapes.

``BackupTuiReporter`` (below) fills the same role here that ``ui/tui.py::TuiReporter`` fills for
``RunScreen``: a pure, ``Live``-free ``render()`` the screen pulls each redraw. It cannot reuse
``TuiReporter`` because ``engine.backup.Reporter.progress`` carries a ``Backup``, not a ``Run`` (a
backup has no ``total_items``/ETA yet — Phase 11a has no analyze step, out of this phase's scope).
"""

import asyncio
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.errors import describe
from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.cli.keys import MenuKey, apply_key
from tgmirror.core.config import Limits
from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import TelegramGateway
from tgmirror.engine.backup import BackupWriter
from tgmirror.engine.backupdir import BackupManifest
from tgmirror.engine.runner import RunControl
from tgmirror.engine.transfer import Transfer
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store
from tgmirror.store.runs import RunStatus
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.messages import t
from tgmirror.ui.progress import duration

EXIT_INTERRUPTED = 130


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
        b = self._backup
        header = t("backup.start", id=b.id, src=b.src_title, dir=b.dir)
        counts = t(
            "backup.progress", id=b.id, done=b.done, skipped=b.skipped_filter, cursor=b.cursor_to
        )
        if self._floods:
            ago = duration(timedelta(seconds=max(self._clock() - (self._last_flood or 0.0), 0.0)))
            counts += t("tui.floods", count=self._floods, ago=ago)
        lines = [Text(header), Text(counts)]
        if self._paused:
            lines.append(Text(t("backup.paused")))
        lines.append(Text(t("tui.keys"), style="dim"))
        return Group(*lines)


class BackupScreen(Screen):
    tick_interval = 0.25  # a file transfer wants to look alive, not just at the keys' pace

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
        self._store = store
        self._gateway = gateway
        self._backup = backup
        self._directory = directory
        self._manifest = manifest
        self._limits = limits
        self._reporter = BackupTuiReporter(backup)
        self._control = RunControl()
        start = t("backup.start", id=backup.id, src=backup.src_title, dir=backup.dir)
        self._lines: list[str] = [start]
        self._task: asyncio.Task[Backup] | None = None
        self._reported = False  # the task's outcome goes into _lines once, not every tick
        self._interrupt_hit = False
        self.footer_hint = t("tui.keys")

    async def on_enter(self) -> ScreenResult | None:
        self._task = asyncio.create_task(self._drive())
        return None

    @property
    def task(self) -> "asyncio.Task[Backup] | None":
        """Public so a test can ``await`` it directly instead of polling ``tick()`` in real time."""
        return self._task

    async def _drive(self) -> Backup:
        with stop_on_interrupt(self._control, self._on_first_ctrlc) as interrupt:
            try:
                writer = BackupWriter(
                    self._store,
                    self._gateway,
                    self._limits,
                    reporter=self._reporter,
                    control=self._control,
                )
                final = await writer.run(self._backup, self._directory, self._manifest)
            finally:
                # set even when a second (hard) Ctrl+C raises KeyboardInterrupt through here, so
                # ``tick()`` still sees it and quits the whole app (the confirmed Ctrl+C rule)
                self._interrupt_hit = interrupt.hit
        return final

    def _on_first_ctrlc(self) -> None:
        self._lines.append(t("run.stopping"))

    def render(self) -> RenderableType:
        body: list[RenderableType] = [Text(line) for line in self._lines]
        if self._task is None or not self._task.done():
            body.append(self._reporter.render())
        return Group(*body)

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if self._task is not None and self._task.done():
            # finished: quit the whole app if that is what Ctrl+C means here, else any key
            # returns to the menu (same transition ``tick()`` makes when no key arrives first)
            return ("quit", EXIT_INTERRUPTED) if self._interrupt_hit else "pop"
        if isinstance(key, str):
            apply_key(self._control, key)  # p/r/q; anything else (typed while running) is ignored
        return "stay"

    async def tick(self) -> ScreenResult | None:
        if self._task is None or not self._task.done():
            return None
        if self._interrupt_hit:  # quitting anyway: no point rendering a result line first
            return ("quit", EXIT_INTERRUPTED)
        if not self._reported:  # a finished task is polled every tick_interval forever after;
            self._reported = True  # report its outcome exactly once, not on every one of those
            exc = self._task.exception()
            if exc is not None:
                self._lines.append(describe(exc) if isinstance(exc, TgMirrorError) else str(exc))
            else:
                self._lines.extend(_result_lines(self._task.result()))
        return None


def _result_lines(final: Backup) -> list[str]:
    lines = [
        t(
            "backup.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            cursor=final.cursor_to,
        )
    ]
    if final.skipped_filter:
        lines.append(t("run.skipped", count=final.skipped_filter))
    if final.status is RunStatus.STOPPED:
        lines.append(t("backup.continue_hint", dir=final.dir))
    return lines
