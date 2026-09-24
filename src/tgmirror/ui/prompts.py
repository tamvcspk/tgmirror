"""Interactive prompts behind a small async protocol.

Wizard and login code talk to ``Prompter`` only, so tests can script the answers and the real
implementation (questionary) stays in one place. It is async because questionary's synchronous
``ask()`` cannot run inside a running event loop, and prompts happen between Telegram calls.
Ctrl+C raises ``KeyboardInterrupt``; the CLI turns it into exit code 130.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

import questionary

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Choice(Generic[T]):  # noqa: UP046 - PEP 695 syntax needs Python 3.12; we support 3.11
    label: str
    value: T


class Prompter(Protocol):
    def say(self, message: str) -> None:
        """Print a line between prompts (validation errors, hints)."""
        ...

    async def text(self, message: str, default: str = "") -> str: ...

    async def secret(self, message: str) -> str:
        """Input that is not echoed (api_hash, login code, password)."""
        ...

    async def confirm(self, message: str, default: bool = True) -> bool: ...

    async def select(self, message: str, choices: Sequence[Choice[T]]) -> T:
        """Pick one; typing filters long lists."""
        ...

    async def checkbox(self, message: str, choices: Sequence[Choice[T]]) -> list[T]:
        """Pick any number (none is allowed); space toggles."""
        ...


Step = Callable[[], Awaitable[None]]


async def run_steps(prompter: Prompter, steps: Sequence[Step]) -> None:
    """Run a multi-step flow (``cli/commands/clone.py::CloneFlow.steps``) with ``prompter``.

    One after the other, unless the prompter can go back a step itself (the full-screen menu's
    ``MenuPrompter.run_steps``: Esc returns to the previous question, across steps too).
    """
    driver = getattr(prompter, "run_steps", None)
    if driver is not None:
        await driver(steps)
        return
    for step in steps:
        await step()


class QuestionaryPrompter:
    def say(self, message: str) -> None:
        print(message)

    async def text(self, message: str, default: str = "") -> str:
        return await questionary.text(message, default=default).unsafe_ask_async()

    async def secret(self, message: str) -> str:
        return await questionary.password(message).unsafe_ask_async()

    async def confirm(self, message: str, default: bool = True) -> bool:
        return await questionary.confirm(message, default=default).unsafe_ask_async()

    async def select(self, message: str, choices: Sequence[Choice[T]]) -> T:
        index = await questionary.select(
            message,
            choices=[questionary.Choice(title=c.label, value=i) for i, c in enumerate(choices)],
            use_search_filter=True,
            use_jk_keys=False,  # j/k would clash with the search filter
        ).unsafe_ask_async()
        return choices[index].value

    async def checkbox(self, message: str, choices: Sequence[Choice[T]]) -> list[T]:
        picked = await questionary.checkbox(
            message,
            choices=[questionary.Choice(title=c.label, value=i) for i, c in enumerate(choices)],
        ).unsafe_ask_async()
        return [choices[i].value for i in picked]
