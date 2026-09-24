"""``MenuPrompter`` (Chặng 2): the wizard's questions answered by keys, and Esc going back one
question — inside a step, across steps, and out of the flow at the very first question."""

import asyncio
from typing import Any

import pytest
from rich.console import Console

from tgmirror.cli.keys import MenuKey
from tgmirror.core.errors import UsageError
from tgmirror.ui.menu.prompter import GoBack, MenuPrompter
from tgmirror.ui.prompts import Choice

Key = MenuKey | str


async def drive(prompter: MenuPrompter, flow: Any, keys: list[Key | list[Key]]) -> Any:
    """Run ``flow`` as a task; each item of ``keys`` answers the next open question (a list is
    several keys for one question). Returns what the flow returned."""
    task = asyncio.ensure_future(flow)
    for item in keys:
        while not prompter.asking:
            assert not task.done(), f"the flow ended early: {task}"
            await asyncio.sleep(0)
        for key in item if isinstance(item, list) else [item]:
            prompter.handle_key(key)
        await asyncio.sleep(0)
    return await asyncio.wait_for(task, 1)


def typed(text: str) -> list[Key]:
    return [*text, MenuKey.ENTER]


COLORS = [Choice("red", "r"), Choice("green", "g"), Choice("blue", "b"), Choice("black", "k")]


async def test_each_kind_of_question_answers_from_keys() -> None:
    prompter = MenuPrompter()

    async def flow() -> tuple[Any, ...]:
        return (
            await prompter.text("name?", default="x"),
            await prompter.secret("code?"),
            await prompter.confirm("sure?"),
            await prompter.select("color?", COLORS),
            await prompter.checkbox("which?", COLORS),
        )

    result = await drive(
        prompter,
        flow(),
        [
            [MenuKey.BACKSPACE, *typed("Ann")],
            typed("12345"),
            [MenuKey.DOWN, MenuKey.ENTER],  # No
            [*"bl", MenuKey.DOWN, MenuKey.ENTER],  # filtered to blue/black, then the second
            [" ", MenuKey.DOWN, MenuKey.DOWN, " ", MenuKey.ENTER],
        ],
    )

    assert result == ("Ann", "12345", False, "k", ["r", "b"])


async def test_a_secret_is_never_drawn_nor_listed() -> None:
    prompter = MenuPrompter()
    task = asyncio.ensure_future(prompter.secret("code?"))
    while not prompter.asking:  # noqa: ASYNC110 - no event to await
        await asyncio.sleep(0)
    for key in "98765":
        prompter.handle_key(key)
    assert prompter.question is not None
    console = Console(width=80, record=True, no_color=True)
    with console.capture():
        console.print(prompter.question.render(None))
    assert "98765" not in console.export_text()
    prompter.handle_key(MenuKey.ENTER)
    assert await task == "98765"
    assert [a.shown for a in prompter.answered] == ["••••"]


async def test_private_text_is_left_out_of_the_answers_list() -> None:
    prompter = MenuPrompter(private_text=True)
    result = await drive(prompter, prompter.text("phone?"), [typed("+84901234567")])
    assert result == "+84901234567"
    assert prompter.answered[0].shown == ""


async def test_esc_outside_steps_leaves_the_flow() -> None:
    prompter = MenuPrompter()
    with pytest.raises(GoBack):
        await drive(prompter, prompter.text("name?"), [MenuKey.ESC])


async def test_esc_goes_back_within_a_step_and_preselects_the_old_answer() -> None:
    prompter = MenuPrompter()
    asked: list[str] = []
    seen: dict[str, Any] = {}

    async def step() -> None:
        asked.append("color")
        seen["color"] = await prompter.select("color?", COLORS)
        asked.append("name")
        seen["name"] = await prompter.text("name?")

    await drive(
        prompter,
        prompter.run_steps([step]),
        [
            [MenuKey.DOWN, MenuKey.ENTER],  # green
            MenuKey.ESC,  # back to color?, green still highlighted
            [MenuKey.DOWN, MenuKey.ENTER],  # -> blue
            typed("Bo"),
        ],
    )

    assert seen == {"color": "b", "name": "Bo"}
    assert asked == ["color", "name", "color", "name"]


async def test_esc_at_a_steps_first_question_reopens_the_previous_steps_last_one() -> None:
    prompter = MenuPrompter()
    state: dict[str, Any] = {}
    runs: list[str] = []

    async def first() -> None:
        runs.append("first")
        state["a"] = await prompter.text("a?")
        state["b"] = await prompter.text("b?")

    async def silent() -> None:  # asks nothing (reads the store, say): skipped going back
        runs.append("silent")

    async def last() -> None:
        runs.append("last")
        state["c"] = await prompter.confirm("c?")

    await drive(
        prompter,
        prompter.run_steps([first, silent, last]),
        [
            typed("1"),
            typed("2"),
            MenuKey.ESC,  # at c?: back past `silent` to b?, which shows "2" again
            [MenuKey.BACKSPACE, *typed("3")],
            MenuKey.ENTER,
        ],
    )

    assert state == {"a": "1", "b": "3", "c": True}
    assert runs == ["first", "silent", "last", "first", "silent", "last"]


async def test_esc_at_the_very_first_question_of_the_steps_leaves_the_flow() -> None:
    prompter = MenuPrompter()

    async def step() -> None:
        await prompter.text("a?")

    with pytest.raises(GoBack):
        await drive(prompter, prompter.run_steps([step]), [MenuKey.ESC])


async def test_an_error_after_asking_says_why_and_asks_the_step_again() -> None:
    prompter = MenuPrompter()
    tries: list[str] = []

    async def step() -> None:
        title = await prompter.text("title?")
        tries.append(title)
        if not title:
            raise UsageError("empty")

    await drive(prompter, prompter.run_steps([step]), [MenuKey.ENTER, typed("ok")])

    assert tries == ["", "ok"]


async def test_an_error_before_asking_ends_the_flow() -> None:
    prompter = MenuPrompter()

    async def step() -> None:
        raise UsageError("no")

    with pytest.raises(UsageError):
        await prompter.run_steps([step])
