"""``TaskScreen``: what ``RunScreen``/``BackupScreen`` share (Phase 15b, L1, docs/06-lo-trinh.md) —
driving a background task (``Runner``/``BackupWriter``) with ``stop_on_interrupt``, the ``p``/``r``/
``q`` hotkeys, reporting the outcome exactly once, and exiting 130 on a confirmed Ctrl+C. That
control loop does not care whether the task is a ``Run`` or a ``Backup``; only the task itself, the
reporter drawn while it runs, and the lines its outcome prints are specific to which one it is, so
those three are the only things a subclass supplies (``_run``, ``_reporter``, ``_result_lines``).
"""

import asyncio
from typing import Generic, Protocol, TypeVar

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.errors import EXIT_INTERRUPTED, describe_any
from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.cli.keys import MenuKey, apply_key
from tgmirror.engine.runner import RunControl
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.messages import t

T = TypeVar("T")


class _Reporter(Protocol):
    def render(self) -> RenderableType: ...


class TaskScreen(Screen, Generic[T]):
    tick_interval = 0.25  # a file transfer wants to look alive, not just at the keys' pace

    _reporter: _Reporter  # set by the subclass's __init__ before render()/tick() can run

    def __init__(self) -> None:
        self._control = RunControl()
        self._lines: list[str] = []
        self._task: asyncio.Task[T] | None = None
        self._reported = False  # the task's outcome goes into _lines once, not every tick
        self._interrupt_hit = False
        self.footer_hint = t("tui.keys")

    async def on_enter(self) -> ScreenResult | None:
        self._task = asyncio.create_task(self._drive())
        return None

    @property
    def task(self) -> "asyncio.Task[T] | None":
        """Public so a test can ``await`` it directly instead of polling ``tick()`` in real time."""
        return self._task

    async def _drive(self) -> T:
        with stop_on_interrupt(self._control, self._on_first_ctrlc) as interrupt:
            try:
                final = await self._run()
            finally:
                # set even when a second (hard) Ctrl+C raises KeyboardInterrupt through here, so
                # ``tick()`` still sees it and quits the whole app (the confirmed Ctrl+C rule)
                self._interrupt_hit = interrupt.hit
        return final

    async def _run(self) -> T:
        """Carry out the task (``Runner.run``/``BackupWriter.run``, plus whatever debug logging
        the subclass wants around it) and return its result. Exceptions propagate through
        ``_drive`` as-is; ``tick()`` reads them back off ``self._task``."""
        raise NotImplementedError

    def _result_lines(self, final: T) -> list[str]:
        raise NotImplementedError

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
                self._lines.append(describe_any(exc))
            else:
                self._lines.extend(self._result_lines(self._task.result()))
        return None
