"""The screen a run/retry drives while it copies — the flagship of Chặng 1 (docs/06-lo-trinh.md,
"Kế hoạch giao diện full-screen (menu)"): the same ``Runner``/``RunControl``/``stop_on_interrupt``
as the classic CLI (``cli/commands/run.py:execute``), drawn inside the app's one ``Live`` instead
of a private one, with no ``typer.echo`` (alt-screen would swallow it).

``TuiReporter`` needs no change for this: its ``render()`` was already pure and decoupled from its
own ``Live`` (``ui/tui.py``) — this screen calls ``notice``/``progress``/``transfer`` on it (via the
``Runner``) and pulls ``render()`` each redraw, never entering ``TuiReporter`` as a context manager.

Ctrl+C keeps its meaning (dừng run và thoát cả app): ``stop_on_interrupt`` only touches
``signal.SIGINT``/``SIGTERM`` (Phase 12), so it composes fine with the app's own key-reading loop;
when it was the *first* Ctrl+C/SIGTERM that ended the run, ``tick()`` returns ``"quit"`` and the
whole app exits, matching the classic CLI's ``EXIT_INTERRUPTED`` (130). The full-screen menu needs
a TTY, so it never runs inside the Docker image (which has none) — SIGTERM there only ever reaches
the classic CLI path.

The run/backup control loop itself (``stop_on_interrupt``, ``p``/``r``/``q``, reporting the outcome
once, exit 130 on a confirmed Ctrl+C) lives in ``ui/menu/task_screen.py::TaskScreen`` — this screen
only supplies the task, the reporter and the result lines (Phase 15b, L1).
"""

import traceback
from pathlib import Path

from tgmirror.cli.keys import MenuKey
from tgmirror.core.config import Limits
from tgmirror.core.gateway import MessageReader, TelegramGateway
from tgmirror.engine.runner import Runner
from tgmirror.store.db import Store
from tgmirror.store.runs import Run, StartedRun
from tgmirror.ui.lines import run_result_lines, run_start_lines
from tgmirror.ui.menu import debug
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.task_screen import TaskScreen
from tgmirror.ui.messages import t
from tgmirror.ui.tui import TuiReporter


class RunScreen(TaskScreen[Run]):
    def __init__(
        self,
        store: Store,
        gateway: TelegramGateway,
        started: StartedRun,
        *,
        limits: Limits,
        tmp_dir: Path,
        wait: bool = False,
        failed_count: int | None = None,
        intro: list[str] | None = None,
        reader_override: MessageReader | None = None,
    ) -> None:
        super().__init__()
        current = started.run
        self.run_id = current.id  # MenuApp reads this so its "running elsewhere" badge skips it
        self._store = store
        self._gateway = gateway
        self._current = current
        self._limits = limits
        self._tmp_dir = tmp_dir
        self._wait = wait
        self._reader_override = reader_override
        self._reporter = TuiReporter(limits, current, silent=True)
        responsibility = [t("warn.responsibility")] if current.options.protected_ack else []
        self._lines = [*(intro or []), *responsibility, *run_start_lines(started, failed_count)]

    async def on_enter(self) -> ScreenResult | None:
        result = await super().on_enter()
        debug.log("run.task_created", run=self._current.id)
        return result

    async def _run(self) -> Run:
        debug.log("run.drive_start", run=self._current.id)
        try:
            runner = Runner(
                self._store,
                self._gateway,
                self._limits,
                reporter=self._reporter,
                control=self._control,
                wait=self._wait,
                tmp_dir=self._tmp_dir,
                reader_override=self._reader_override,
            )
            final = await runner.run(self._current)
            debug.log("run.drive_done", run=final.id, status=str(final.status))
        except BaseException as exc:
            # Root cause found and fixed 2026-09-27 (N5, Phase 15b: a race in
            # ``engine/reupload.py::Pipeline._next``) — kept on by the user's own choice
            # (2026-09-24) as a permanent diagnostic, not removed now that it found its bug.
            # Message/traceback only, never message *content* of a Telegram call (this is a
            # Runner/asyncio-level exception, not one carrying user data, CLAUDE.md rule 6).
            debug.log(
                "run.drive_error",
                error=type(exc).__name__,
                message=str(exc),
                trace=" | ".join(
                    line.strip() for line in "".join(traceback.format_exception(exc)).splitlines()
                ),
            )
            raise
        return final

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        debug.log("run.key", key=str(key), task_done=self._task is not None and self._task.done())
        return await super().handle_key(key)

    async def tick(self) -> ScreenResult | None:
        run = self._reporter.run  # what render() will show: has progress() moved it?
        debug.log(
            "run.tick",
            task_done=self._task is not None and self._task.done(),
            handled=run.handled,
            status=str(run.status),
        )
        return await super().tick()

    def _result_lines(self, final: Run) -> list[str]:
        return run_result_lines(final, self._current.options.retry_of)
