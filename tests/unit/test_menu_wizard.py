"""Chặng 2 of the full-screen menu: "Sao chép mới" and "Đăng nhập" as ``WizardScreen``s — the same
``CloneFlow``/login flow as the CLI, answered by keys, with Esc going back one question.

The screens are driven directly (``on_enter``/``handle_key``), like ``test_resume_screen.py``: no
terminal, no ``Live``.
"""

import io
from collections.abc import Callable
from pathlib import Path

from rich.console import Console, RenderableType

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tgmirror.cli.keys import MenuKey
from tgmirror.cli.runtime import Connection, Runtime
from tgmirror.store.db import Store
from tgmirror.ui.menu.app import MenuApp
from tgmirror.ui.menu.run_screen import RunScreen
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.clone import clone_screen
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.login import login_screen
from tgmirror.ui.menu.screens.main_menu import MainMenuScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t

MakeRuntime = Callable[..., Runtime]
Key = MenuKey | str


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, no_color=True, width=120)
    console.print(renderable)
    return console.export_text()


async def press(screen: WizardScreen, *keys: Key) -> ScreenResult:
    result: ScreenResult = "stay"
    for key in keys:
        assert screen.prompter.asking, f"no open question for {key!r}: {plain(screen.render())}"
        result = await screen.handle_key(key)
    return result


def asked(screen: WizardScreen) -> str:
    assert screen.prompter.question is not None
    return screen.prompter.question.message


async def make_app(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway, auth: FakeAuth, **rt: object
) -> MenuApp:
    runtime = make_runtime(gateway=gateway, auth=auth, interactive=True, **rt)
    store = await Store.open(tmp_path / "t.db")
    account = await auth.account()
    return MenuApp(runtime, store, Connection(auth, gateway), account)


async def test_a_new_clone_asks_like_the_cli_and_hands_over_to_the_run_screen(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    gateway.add_channel("Source")
    target = gateway.add_channel("Good target")
    app = await make_app(make_runtime, tmp_path, gateway, FakeAuth(logged_in=ACCOUNT))
    screen = clone_screen(app)

    assert await screen.on_enter() == "stay"
    assert asked(screen) == t("clone.pick_source")
    await press(screen, MenuKey.ENTER)  # Source
    assert asked(screen) == t("clone.pick_destination")
    await press(screen, MenuKey.DOWN, MenuKey.ENTER)  # Good target (below "+ Create")
    await press(screen, MenuKey.ENTER)  # Automatic
    await press(screen, MenuKey.ENTER)  # customise? No
    assert asked(screen) == t("filter.pick")
    await press(screen, MenuKey.ENTER)  # No filter
    assert asked(screen).startswith(t("clone.confirm_start", src="", dst="")[:4])
    result = await press(screen, MenuKey.ENTER)  # Yes

    assert result[0] == "replace" and isinstance(result[1], RunScreen)
    assert gateway.calls_to("create_channel") == []
    (pair,) = await app.store.list_pairs(5)
    assert pair.dst_id == target.id
    await app.store.close()


async def test_esc_goes_back_one_question_across_steps(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    gateway.add_channel("Source")
    gateway.add_channel("Good target")
    app = await make_app(make_runtime, tmp_path, gateway, FakeAuth(logged_in=ACCOUNT))
    screen = clone_screen(app)
    await screen.on_enter()
    await press(screen, MenuKey.ENTER, MenuKey.DOWN, MenuKey.ENTER, MenuKey.ENTER, MenuKey.ENTER)
    assert asked(screen) == t("filter.pick")

    await press(screen, MenuKey.ESC)  # the filter step's first question: back to the strategy's
    assert asked(screen) == t("options.customise_auto")
    await press(screen, MenuKey.ESC)
    assert asked(screen) == t("options.pick_mode")
    await press(screen, MenuKey.ESC)  # into the destination step, "Good target" still highlighted
    assert asked(screen) == t("clone.pick_destination")
    assert "▸ [channel] Good target" in plain(screen.render())
    await press(screen, MenuKey.ESC, MenuKey.ESC)  # source, then out of the wizard
    assert screen.task is not None and screen.task.done()
    assert await screen.tick() == "pop"
    await app.store.close()


async def test_esc_at_the_first_question_leaves_and_no_ends_the_wizard_without_writing(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    gateway.add_channel("Source")
    app = await make_app(make_runtime, tmp_path, gateway, FakeAuth(logged_in=ACCOUNT))

    first = clone_screen(app)
    await first.on_enter()
    assert await press(first, MenuKey.ESC) == "pop"

    declined = clone_screen(app)
    await declined.on_enter()
    await press(declined, MenuKey.ENTER)  # Source; no eligible destination: a new channel
    assert t("clone.no_candidates") in plain(declined.render())
    await press(declined, *"Copy", MenuKey.ENTER, MenuKey.ENTER)  # title, empty description
    await press(declined, MenuKey.ENTER, MenuKey.ENTER, MenuKey.ENTER)  # auto, no, no filter
    assert await press(declined, MenuKey.DOWN, MenuKey.ENTER) == "pop"  # "No" to starting

    assert gateway.calls_to("create_channel") == []
    await app.store.close()


async def test_a_refused_destination_says_why_and_asks_again(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    gateway.add_channel("Source")
    app = await make_app(make_runtime, tmp_path, gateway, FakeAuth(logged_in=ACCOUNT))
    screen = clone_screen(app)
    await screen.on_enter()
    await press(screen, MenuKey.ENTER)  # Source -> a new channel
    await press(screen, MenuKey.ENTER, MenuKey.ENTER)  # empty title, empty description

    assert t("err.title_empty") in plain(screen.render())
    assert asked(screen) == t("clone.prompt_title")
    await app.store.close()


async def test_login_asks_phone_and_code_and_never_shows_them_again(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    auth = FakeAuth()
    app = await make_app(make_runtime, tmp_path, FakeGateway(), auth)
    screen = login_screen(app)

    await screen.on_enter()
    assert asked(screen) == t("login.prompt_phone")
    await press(screen, *"+84901234567", MenuKey.ENTER)
    assert asked(screen) == t("login.prompt_code")
    await press(screen, *"12345")
    shown = plain(screen.render())
    assert "84901234567" not in shown and "12345" not in shown
    result = await press(screen, MenuKey.ENTER)

    assert result[0] == "replace" and isinstance(result[1], InfoScreen)
    assert app.account == ACCOUNT
    await app.store.close()


async def test_login_without_api_credentials_asks_for_them_then_connects(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    auth = FakeAuth()
    runtime = make_runtime(gateway=FakeGateway(), auth=auth, interactive=True, env={})
    store = await Store.open(tmp_path / "t.db")
    app = MenuApp(runtime, store, None, None)  # what `launch` builds with no api_id yet
    main = MainMenuScreen(app)
    await main.on_enter()
    assert t("menu.item_login") in plain(main.render())
    assert t("menu.item_clone") not in plain(main.render())

    screen = login_screen(app)
    await screen.on_enter()
    await press(screen, *"12345", MenuKey.ENTER)
    await press(screen, *"0123456789abcdef0123456789abcdef", MenuKey.ENTER)
    await press(screen, *"+84901234567", MenuKey.ENTER)
    await press(screen, *"12345", MenuKey.ENTER)

    assert app.conn is not None and app.account == ACCOUNT
    assert runtime.config().api_id == 12345
    await main.on_return()
    assert t("menu.item_clone") in plain(main.render())
    await app.close_connection()
    await store.close()
