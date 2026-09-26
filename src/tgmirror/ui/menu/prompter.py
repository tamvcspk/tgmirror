"""``MenuPrompter``: the ``Prompter`` (``ui/prompts.py``) the full-screen menu draws in its own
frame, so ``cli/wizard.py`` and the login flow run unchanged inside the app (Chặng 2,
docs/06-lo-trinh.md).

The flow runs as a task; each question parks it on a future that ``WizardScreen``
(``ui/menu/screens/wizard.py``) resolves from the app's key queue. Esc resolves it with ``GoBack``:

- inside ``run_steps`` (a flow split into steps, ``CloneFlow.steps``), the step is re-run with its
  earlier answers replayed, so the flow lands on the previous question again, with the old answer
  pre-selected; at a step's first question the previous step is re-run the same way;
- anywhere else (the login flow), or at the very first question, ``GoBack`` leaves the flow: the
  wizard is cancelled and the menu returns to where it was.

Replaying needs no change in the wizard: its functions only ask, and a step's questions depend
only on the answers given within it and on the steps before it (which are not re-run).
"""

import asyncio
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.errors import describe
from tgmirror.cli.keys import MenuKey
from tgmirror.core.errors import TgMirrorError
from tgmirror.ui.menu.widgets import SelectList, TypeToFilter
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Choice, Step

T = TypeVar("T")

_NO_HINT = object()


class GoBack(Exception):  # noqa: N818 - a key the user pressed, not a failure
    """Esc on a question: back one question, or out of the flow at its first one."""


@dataclass(frozen=True, slots=True)
class Answer:
    message: str
    value: Any
    shown: str  # how the list of answers so far shows it ("" = not at all)


class Question(Protocol):
    message: str
    footer: str

    def render(self, height: int | None) -> RenderableType:
        """The question and its input; ``height`` rows at most when known."""
        ...

    def handle_key(self, key: MenuKey | str) -> tuple[bool, Any]:
        """``(True, value)`` once answered; ``(False, None)`` while still editing."""
        ...

    def shown(self, value: Any) -> str: ...


@dataclass
class TextQuestion:
    message: str
    text: str = ""
    masked: bool = False  # secrets: never drawn, not even their length
    private: bool = False  # drawn while typing but left out of the answers list (a phone number)
    footer: str = field(default_factory=lambda: t("menu.footer_text"))

    def render(self, height: int | None) -> RenderableType:
        typed = "•" * 4 if self.masked and self.text else self.text
        line = Text("› ", style="bold cyan")
        line.append(typed)
        line.append("▏", style="blink")
        return Group(Text(self.message, style="bold"), line)

    def handle_key(self, key: MenuKey | str) -> tuple[bool, Any]:
        if key == MenuKey.ENTER:
            return True, self.text
        if key == MenuKey.BACKSPACE:
            self.text = self.text[:-1]
        elif isinstance(key, str) and not isinstance(key, MenuKey) and key.isprintable():
            self.text += key
        return False, None

    def shown(self, value: Any) -> str:
        if self.masked:
            return "••••"
        return "" if self.private else str(value)


class SelectQuestion:
    """One of ``choices``; typing filters (``filterable``), Up/Down move, Enter picks."""

    def __init__(
        self,
        message: str,
        choices: Sequence[Choice[Any]],
        *,
        index: int = 0,
        filterable: bool = True,
    ) -> None:
        self.message = message
        self._choices = list(choices)
        self._filter = TypeToFilter() if filterable else None
        self._list: SelectList[int] = SelectList(
            items=[(c.label, i) for i, c in enumerate(self._choices)], index=index
        )
        self.footer = t("menu.footer_select" if filterable else "menu.footer_pick")

    def render(self, height: int | None) -> RenderableType:
        head: list[RenderableType] = [Text(self.message, style="bold")]
        if self._filter is not None and self._filter.text:
            head.append(self._filter.render())
        rows = None if height is None else max(height - len(head), 1)
        if not self._list.items:
            return Group(*head, Text(t("menu.no_match"), style="dim"))
        return Group(*head, self._list.render(rows))

    def handle_key(self, key: MenuKey | str) -> tuple[bool, Any]:
        if key == MenuKey.ENTER:
            if not self._list.items:
                return False, None
            return True, self._choices[self._list.items[self._list.index][1]].value
        if key in (MenuKey.UP, MenuKey.DOWN):
            self._list.handle_key(key)
        elif self._filter is not None and self._filter.handle_key(key):
            needle = self._filter.text.casefold()
            self._list = SelectList(
                items=[
                    (c.label, i)
                    for i, c in enumerate(self._choices)
                    if needle in c.label.casefold()
                ]
            )
        return False, None

    def shown(self, value: Any) -> str:
        return next((c.label for c in self._choices if c.value == value), "")


class CheckQuestion:
    """Any number of ``choices`` (none too): Up/Down move, Space ticks, Enter confirms."""

    def __init__(
        self, message: str, choices: Sequence[Choice[Any]], ticked: Sequence[Any] = ()
    ) -> None:
        self.message = message
        self._choices = list(choices)
        self._ticked = {i for i, c in enumerate(self._choices) if c.value in ticked}
        self._index = 0
        self.footer = t("menu.footer_check")

    def render(self, height: int | None) -> RenderableType:
        items = [
            (("[x] " if i in self._ticked else "[ ] ") + c.label, i)
            for i, c in enumerate(self._choices)
        ]
        rows = None if height is None else max(height - 1, 1)
        listing = SelectList(items=items, index=self._index).render(rows)
        return Group(Text(self.message, style="bold"), listing)

    def handle_key(self, key: MenuKey | str) -> tuple[bool, Any]:
        if not self._choices:
            return (True, []) if key == MenuKey.ENTER else (False, None)
        if key == MenuKey.ENTER:
            return True, [c.value for i, c in enumerate(self._choices) if i in self._ticked]
        if key == MenuKey.UP:
            self._index = (self._index - 1) % len(self._choices)
        elif key == MenuKey.DOWN:
            self._index = (self._index + 1) % len(self._choices)
        elif key == " ":
            self._ticked ^= {self._index}
        return False, None

    def shown(self, value: Any) -> str:
        return ", ".join(c.label for c in self._choices if c.value in value) or "—"


class MenuPrompter:
    """``Prompter`` for ``WizardScreen``: the current ``question`` and ``notes`` are what the screen
    draws; ``handle_key`` is what it feeds keys to. ``private_text`` keeps typed text answers out of
    the answers list (the login flow: a phone number is never shown again, CLAUDE.md rule 6)."""

    def __init__(self, *, private_text: bool = False) -> None:
        self.question: Question | None = None
        self.notes: list[str] = []
        self.changed = asyncio.Event()  # set when a question is asked or a note is added
        self._private_text = private_text
        self._future: asyncio.Future[Any] | None = None
        self._done: list[list[Answer]] = []  # answers of the finished steps (``run_steps``)
        self._answers: list[Answer] = []  # answers of the step (or flow) in progress
        self._script: deque[Answer] = deque()  # replayed without asking (going back)
        self._hints: deque[Answer] = deque()  # pre-selected when asked live again

    @property
    def asking(self) -> bool:
        """A question is open and waiting for keys (``question`` alone lags: an answered one stays
        set until the flow task gets to run again)."""
        return self.question is not None and self._future is not None and not self._future.done()

    @property
    def answered(self) -> list[Answer]:
        """Every answer so far, oldest first (the screen shows the last few)."""
        return [a for step in self._done for a in step] + self._answers

    # -- Prompter --------------------------------------------------------------------------------

    def say(self, message: str) -> None:
        self.notes.append(message)
        self.changed.set()

    def echo(self, message: str, err: bool = False) -> None:
        """``typer.echo``'s signature, for flows that print (``CloneFlow``'s ``echo``)."""
        self.say(message)

    async def text(self, message: str, default: str = "") -> str:
        def build(hint: Any) -> Question:
            text = default if hint is _NO_HINT else str(hint)
            return TextQuestion(message, text, private=self._private_text)

        return await self._ask(message, build)

    async def path(self, message: str, *, only_directories: bool = False) -> str:
        """No line editor to attach a path completer to in this hand-drawn frame (unlike the
        classic wizard's ``QuestionaryPrompter``): falls back to a plain text question."""
        return await self.text(message)

    async def secret(self, message: str) -> str:
        return await self._ask(message, lambda hint: TextQuestion(message, masked=True))

    async def confirm(self, message: str, default: bool = True) -> bool:
        def build(hint: Any) -> Question:
            value = default if hint is _NO_HINT else bool(hint)
            choices = [Choice(t("menu.yes"), True), Choice(t("menu.no"), False)]
            return SelectQuestion(message, choices, index=0 if value else 1, filterable=False)

        return await self._ask(message, build)

    async def select(self, message: str, choices: Sequence[Choice[T]]) -> T:
        def build(hint: Any) -> Question:
            index = next((i for i, c in enumerate(choices) if c.value == hint), 0)
            return SelectQuestion(message, choices, index=index, filterable=len(choices) > 3)

        return await self._ask(message, build)

    async def checkbox(self, message: str, choices: Sequence[Choice[T]]) -> list[T]:
        def build(hint: Any) -> Question:
            return CheckQuestion(message, choices, () if hint is _NO_HINT else hint)

        return await self._ask(message, build)

    # -- driven by WizardScreen ------------------------------------------------------------------

    def handle_key(self, key: MenuKey | str) -> bool:
        """Feed one key to the open question. ``False``: no question is open (the flow is busy)."""
        question, future = self.question, self._future
        if question is None or future is None or future.done():
            return False  # not ``asking``
        if key == MenuKey.ESC:
            future.set_exception(GoBack())
            return True
        answered, value = question.handle_key(key)
        if answered:
            future.set_result(value)
        return True

    async def run_steps(self, steps: Sequence[Step]) -> None:
        """Run ``steps`` in order; Esc goes back one question, across steps (module docstring).

        A step that fails with a ``TgMirrorError`` after asking something (a destination that is
        not allowed, a title that is too long) says why and asks its questions again, with the
        rejected answers pre-selected; one that fails before asking anything ends the flow.
        """
        self._done = []
        index = 0
        replay: list[Answer] = []
        hints: list[Answer] = []
        while index < len(steps):
            self._answers, self._script, self._hints = [], deque(replay), deque(hints)
            try:
                await steps[index]()
            except GoBack:
                self.notes.clear()
                if self._answers:  # back to this step's previous question
                    replay, hints = self._answers[:-1], self._answers[-1:]
                    continue
                while index > 0:  # back to the last question of the nearest step that asked one
                    index -= 1
                    previous = self._done.pop()
                    if previous:
                        replay, hints = previous[:-1], previous[-1:]
                        break
                else:
                    raise  # Esc at the very first question: leave the flow
                continue
            except TgMirrorError as exc:
                if not self._answers:
                    raise
                self.notes.clear()
                self.say(describe(exc))
                replay, hints = [], self._answers
                continue
            self._done.append(self._answers)
            replay, hints = [], []
            index += 1
        self._answers = []

    # -- internals -------------------------------------------------------------------------------

    async def _ask(self, message: str, build: Any) -> Any:
        if self._script:
            earlier = self._script.popleft()
            if earlier.message == message:
                self._answers.append(earlier)
                return earlier.value
            self._script.clear()  # the flow went another way this time: ask from here on
        hint: Any = _NO_HINT
        if self._hints:
            earlier = self._hints.popleft()
            if earlier.message == message:
                hint = earlier.value
            else:
                self._hints.clear()
        question: Question = build(hint)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.question, self._future = question, future
        self.changed.set()
        try:
            value = await future
        finally:
            self.question, self._future = None, None
        self.notes.clear()
        self._answers.append(Answer(message, value, question.shown(value)))
        return value
