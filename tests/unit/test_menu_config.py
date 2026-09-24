""" "Cấu hình" in the full-screen menu: same ``core.config.set_limit`` as ``tgmirror config set``,
list -> edit (a one-question ``WizardScreen``) -> back to the list showing the new value."""

import io
from collections.abc import Callable
from pathlib import Path

from rich.console import Console, RenderableType

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tgmirror.cli.keys import MenuKey
from tgmirror.cli.runtime import Connection, Runtime
from tgmirror.store.db import Store
from tgmirror.ui.menu.app import MenuApp
from tgmirror.ui.menu.screens.config import ConfigScreen
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t

MakeRuntime = Callable[..., Runtime]


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, no_color=True, width=120)
    console.print(renderable)
    return console.export_text()


async def make_app(make_runtime: MakeRuntime, tmp_path: Path) -> MenuApp:
    gateway = FakeGateway()
    auth = FakeAuth(logged_in=ACCOUNT)
    runtime = make_runtime(gateway=gateway, auth=auth, interactive=True)
    store = await Store.open(tmp_path / "t.db")
    return MenuApp(runtime, store, Connection(auth, gateway), ACCOUNT)


async def test_shows_paths_and_every_limit(make_runtime: MakeRuntime, tmp_path: Path) -> None:
    app = await make_app(make_runtime, tmp_path)
    screen = ConfigScreen(app)

    await screen.on_enter()
    shown = plain(screen.render())

    assert "batch_size = 20" in shown
    assert str(app.rt.paths.config_file) in shown
    await app.store.close()


async def test_editing_a_key_saves_it_and_the_list_shows_the_new_value(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    app = await make_app(make_runtime, tmp_path)
    screen = ConfigScreen(app)
    await screen.on_enter()

    result = await screen.handle_key(MenuKey.ENTER)  # batch_size is first
    assert isinstance(result, tuple) and result[0] == "push"
    edit = result[1]
    assert isinstance(edit, WizardScreen)
    assert await edit.on_enter() == "stay"
    assert edit.prompter.question is not None
    assert edit.prompter.question.message == t("menu.config_prompt_value", name="batch_size")
    assert edit.prompter.question.text == "20"  # current value pre-filled

    for _ in edit.prompter.question.text:  # type: ignore[union-attr]
        await edit.handle_key(MenuKey.BACKSPACE)
    for ch in "30":
        await edit.handle_key(ch)
    outcome = await edit.handle_key(MenuKey.ENTER)

    assert isinstance(outcome, tuple) and outcome[0] == "replace"
    info = outcome[1]
    assert isinstance(info, InfoScreen)
    assert t("config.saved", name="batch_size", value="30") in "\n".join(info._lines)
    assert app.rt.config().limits.batch_size == 30

    await screen.on_return()
    assert "batch_size = 30" in plain(screen.render())
    await app.store.close()


async def test_invalid_value_becomes_an_info_screen_and_nothing_is_saved(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    app = await make_app(make_runtime, tmp_path)
    screen = ConfigScreen(app)
    await screen.on_enter()

    result = await screen.handle_key(MenuKey.ENTER)  # batch_size
    edit = result[1]  # type: ignore[index]
    await edit.on_enter()
    for _ch in edit.prompter.question.text:  # type: ignore[union-attr]
        await edit.handle_key(MenuKey.BACKSPACE)
    for ch in "not-a-number":
        await edit.handle_key(ch)
    outcome = await edit.handle_key(MenuKey.ENTER)
    outcome = await edit.tick() or outcome  # let the flow's exception settle

    assert isinstance(outcome, tuple) and outcome[0] == "replace"
    assert isinstance(outcome[1], InfoScreen)
    assert app.rt.config().limits.batch_size == 20  # unchanged
    await app.store.close()
