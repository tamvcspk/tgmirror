"""The screen a run/retry drives while it copies — the flagship of Chặng 1 (docs/06-lo-trinh.md,
"Kế hoạch giao diện full-screen (menu)"): the same ``Runner``/``RunControl``/``stop_on_interrupt``
as the classic CLI (``cli/commands/run.py:execute``), drawn inside the app's one ``Live`` instead
of a private one, with no ``typer.echo`` (alt-screen would swallow it).

``TuiReporter`` needs no change for this: its ``render()`` was already pure and decoupled from its
own ``Live`` (``ui/tui.py``) — this screen calls ``notice``/``progress``/``transfer`` on it (via the
``Runner``) and pulls ``render()`` each redraw, never entering ``TuiReporter`` as a context manager.

Ctrl+C keeps its meaning (dừng run và thoát cả app): ``stop_on_interrupt`` only touches
``signal.SIGINT``, so it composes fine with the app's own key-reading loop; when it was the *first*
Ctrl+C that ended the run, ``tick()`` returns ``"quit"`` and the whole app exits, matching the
classic CLI's ``EXIT_INTERRUPTED`` (130).
"""

import asyncio
import traceback
from pathlib import Path

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.errors import describe
from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.cli.keys import MenuKey, apply_key
from tgmirror.core.config import Limits
from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import TelegramGateway
from tgmirror.engine.runner import RunControl, Runner
from tgmirror.store.db import Store
from tgmirror.store.runs import FilterChange, Run, RunStatus, StartedRun
from tgmirror.ui.menu import debug
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.messages import t
from tgmirror.ui.tui import TuiReporter

EXIT_INTERRUPTED = 130


class RunScreen(Screen):
    tick_interval = 0.25  # a file transfer wants to look alive, not just at the keys' pace

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
    ) -> None:
        current = started.run
        self._store = store
        self._gateway = gateway
        self._current = current
        self._limits = limits
        self._tmp_dir = tmp_dir
        self._wait = wait
        self._reporter = TuiReporter(limits, current)
        self._control = RunControl()
        self._lines: list[str] = _start_lines(started, failed_count)
        self._task: asyncio.Task[Run] | None = None
        self._reported = False  # the task's outcome goes into _lines once, not every tick
        self._interrupt_hit = False
        self.footer_hint = t("tui.keys")

    async def on_enter(self) -> ScreenResult | None:
        self._task = asyncio.create_task(self._drive())
        debug.log("run.task_created", run=self._current.id)
        return None

    @property
    def task(self) -> "asyncio.Task[Run] | None":
        """Public so a test can ``await`` it directly instead of polling ``tick()`` in real time."""
        return self._task

    async def _drive(self) -> Run:
        debug.log("run.drive_start", run=self._current.id)
        with stop_on_interrupt(self._control, self._on_first_ctrlc) as interrupt:
            try:
                runner = Runner(
                    self._store,
                    self._gateway,
                    self._limits,
                    reporter=self._reporter,
                    control=self._control,
                    wait=self._wait,
                    tmp_dir=self._tmp_dir,
                )
                final = await runner.run(self._current)
                debug.log("run.drive_done", run=final.id, status=str(final.status))
            except BaseException as exc:
                # temporary, for diagnosing the crash reported 2026-09-24 (docs/06-lo-trinh.md,
                # "Kế hoạch giao diện full-screen (menu)"): message/traceback, never message
                # *content* of a Telegram call (this is a Runner/asyncio-level exception, not one
                # carrying user data) — remove once the root cause is found (CLAUDE.md rule 6).
                debug.log(
                    "run.drive_error",
                    error=type(exc).__name__,
                    message=str(exc),
                    trace=" | ".join(
                        line.strip()
                        for line in "".join(traceback.format_exception(exc)).splitlines()
                    ),
                )
                raise
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
        debug.log("run.key", key=str(key), task_done=self._task is not None and self._task.done())
        if self._task is not None and self._task.done():
            # finished: quit the whole app if that is what Ctrl+C means here, else any key
            # returns to the menu (same transition ``tick()`` makes when no key arrives first)
            return ("quit", EXIT_INTERRUPTED) if self._interrupt_hit else "pop"
        if isinstance(key, str):
            apply_key(self._control, key)  # p/r/q; anything else (typed while running) is ignored
        return "stay"

    async def tick(self) -> ScreenResult | None:
        run = self._reporter._run  # what render() will show: has progress() moved it?
        debug.log(
            "run.tick",
            task_done=self._task is not None and self._task.done(),
            handled=run.handled,
            status=str(run.status),
        )
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
                self._lines.extend(
                    _result_lines(self._task.result(), self._current.options.retry_of)
                )
        return None


def _start_lines(started: StartedRun, failed_count: int | None) -> list[str]:
    current = started.run
    lines: list[str] = []
    if current.options.protected_ack:
        lines.append(t("warn.responsibility"))
    if (retry_of := current.options.retry_of) is not None:
        lines.append(
            t(
                "run.retry_start",
                id=current.id,
                of=retry_of,
                count=failed_count or 0,
                src=current.src_title,
                dst=current.dst_title,
            )
        )
        return lines
    lines.append(
        t(
            "run.start",
            id=current.id,
            src=current.src_title,
            dst=current.dst_title,
            cursor=current.cursor_from,
        )
    )
    if started.forgot is not None:
        lines.append(t("run.fresh_started", count=started.forgot))
    elif started.filters is FilterChange.CHANGED:
        lines.append(t("run.filter_changed"))
    if started.filters is FilterChange.SAME and current.filters_json != "{}":
        lines.append(t("run.filter_reused"))
    return lines


def _result_lines(final: Run, retry_of: int | None) -> list[str]:
    lines = [
        t(
            "run.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            failed=final.failed,
        )
    ]
    if final.skipped_filter:
        lines.append(t("run.skipped", count=final.skipped_filter))
    if final.skipped_unsupported:
        lines.append(t("run.unsupported_total", count=final.skipped_unsupported, id=final.id))
    if final.gone:
        lines.append(t("retry.gone", count=final.gone))
    if final.failed:
        key = "retry.still_failing" if retry_of is not None else "run.retry_hint"
        lines.append(t(key, count=final.failed, id=final.id))
    if final.status is RunStatus.STOPPED:
        if retry_of is not None:
            lines.append(t("run.retry_continue_hint", of=retry_of))
        else:
            lines.append(t("run.continue_hint", id=final.id))
    return lines
