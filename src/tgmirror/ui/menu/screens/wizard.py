"""A flow that asks questions (a new clone, logging in), drawn in the app's frame.

The flow is a coroutine taking a ``MenuPrompter`` (``ui/menu/prompter.py``) and returning where
the app goes when it is done (typically ``("replace", RunScreen/InfoScreen)``). It runs as a task
next to the app's key loop: this screen draws the prompter's open question and feeds it keys.

How the flow ends decides the rest: Esc at its first question (``GoBack``) or a "no" to a
confirmation (``Declined``) goes back to the menu; a ``TgMirrorError`` becomes an ``InfoScreen``
with the same sentence the CLI prints; anything else is a bug and propagates.
"""

import asyncio
from collections.abc import Awaitable, Callable

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.text import Text

from tgmirror.cli.errors import Declined, describe
from tgmirror.cli.keys import MenuKey
from tgmirror.core.errors import TgMirrorError
from tgmirror.ui.menu.prompter import GoBack, MenuPrompter
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.messages import t

Flow = Callable[[MenuPrompter], Awaitable[ScreenResult]]

SETTLE = 0.3  # seconds a key waits for the flow's next question before the screen redraws
ANSWERS_SHOWN = 6  # the last answers listed above the question


class WizardScreen(Screen):
    tick_interval = 0.2  # a slow step (reading Telegram) shows "working"; pick up its question

    def __init__(self, title: str, flow: Flow, *, prompter: MenuPrompter | None = None) -> None:
        self._title = title
        self._flow = flow
        self.prompter = prompter or MenuPrompter()
        self._task: asyncio.Task[ScreenResult] | None = None

    @property
    def footer_hint(self) -> str:  # type: ignore[override]
        question = self.prompter.question
        return question.footer if self.prompter.asking and question else t("menu.footer_working")

    @property
    def task(self) -> "asyncio.Task[ScreenResult] | None":
        return self._task

    async def on_enter(self) -> ScreenResult | None:
        self._task = asyncio.create_task(self._flow(self.prompter))
        return await self._settle()

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if self._task is None:
            return "stay"
        if self._task.done():
            return self._outcome()
        if self.prompter.handle_key(key):
            return await self._settle()
        return "stay"  # busy between two questions: the key means nothing yet

    async def tick(self) -> ScreenResult | None:
        if self._task is not None and self._task.done():
            return self._outcome()
        return None

    async def _settle(self) -> ScreenResult:
        """Let the flow run to its next question (or its end) before the app redraws, so a quick
        step does not flash "working". Bounded: a slow one shows "working", ``tick`` does the rest.
        """
        task = self._task
        assert task is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE
        while not task.done() and not self.prompter.asking:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            self.prompter.changed.clear()
            waiter = asyncio.ensure_future(self.prompter.changed.wait())
            try:
                await asyncio.wait(
                    {task, waiter}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                waiter.cancel()
        return self._outcome() if task.done() else "stay"

    def _outcome(self) -> ScreenResult:
        task = self._task
        assert task is not None and task.done()
        if task.cancelled():
            return "pop"
        exc = task.exception()
        if exc is None:
            return task.result()
        if isinstance(exc, GoBack | Declined):
            return "pop"
        if isinstance(exc, TgMirrorError):
            return ("replace", InfoScreen([describe(exc)]))
        raise exc

    def render(self) -> RenderableType:
        head: list[RenderableType] = [Text(self._title, style="bold"), Text("")]
        for answer in self.prompter.answered[-ANSWERS_SHOWN:]:
            line = Text("✓ " + answer.message, style="dim")
            if answer.shown:
                line.append(": " + answer.shown, style="dim")
            head.append(line)
        head += [Text(note, style="yellow") for note in self.prompter.notes]
        question = self.prompter.question
        if question is None or not self.prompter.asking:
            head.append(Text(t("menu.working"), style="dim"))
            return Group(*head)
        return _Fit(Group(*head), question.render)


class _Fit:
    """``head`` then ``body(rows left)``: a long list gets exactly the rows the frame leaves it
    (the ``Layout`` region's height), so the highlighted choice is always on screen."""

    def __init__(self, head: RenderableType, body: Callable[[int | None], RenderableType]) -> None:
        self._head = head
        self._body = body

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        head = console.render_lines(self._head, options.update(height=None), pad=False)
        rows = None if options.height is None else max(options.height - len(head), 4)
        yield Group(self._head, self._body(rows))
