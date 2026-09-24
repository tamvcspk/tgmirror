"""Bare ``tgmirror`` (docs/06-lo-trinh.md, "Kế hoạch giao diện full-screen (menu)"): no terminal
still shows help exactly as ``no_args_is_help`` used to (``test_no_args_shows_help``,
``tests/unit/test_cli.py``, untouched by this change); a real terminal launches the menu — tested
two ways here: through the CLI entry point (no real tty under ``CliRunner``, so it reports that and
exits) and directly against ``MenuApp`` with a scripted key queue (no real terminal needed at all).
"""

import asyncio
import io
from collections.abc import Callable
from pathlib import Path

from rich.console import Console, RenderableType
from typer.testing import CliRunner

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tgmirror.cli.app import app
from tgmirror.cli.keys import MenuKey
from tgmirror.cli.runtime import Connection, Runtime
from tgmirror.store.db import Store
from tgmirror.ui.menu.app import MenuApp
from tgmirror.ui.menu.screens.account import AccountScreen


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, no_color=True, width=200)
    console.print(renderable)
    return console.export_text()


runner = CliRunner()

MakeRuntime = Callable[..., Runtime]


def test_bare_invocation_without_a_terminal_shows_help_like_before(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt = make_runtime(gateway=gateway, interactive=False)

    result = runner.invoke(app, [], obj=rt)

    assert result.exit_code == 0
    assert "Clone a Telegram channel" in result.output


def test_bare_invocation_with_a_terminal_tries_the_menu(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """``CliRunner`` gives no real tty, so ``open_reader()`` fails the same way a genuinely odd
    terminal would: the menu says so and exits, instead of the classic help text — proving the
    bare-invocation branch really is different for an interactive ``Runtime`` now."""
    rt = make_runtime(gateway=gateway, interactive=True)

    result = runner.invoke(app, [], obj=rt)

    assert result.exit_code == 1
    assert "keyboard" in result.output.lower()


async def test_menu_app_quits_from_the_main_menu_with_code_0(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway, auth = FakeGateway(), FakeAuth(logged_in=ACCOUNT)
    rt = make_runtime(gateway=gateway, auth=auth, interactive=True)
    store = await Store.open(tmp_path / "t.db")
    console = Console(file=io.StringIO(), no_color=True, width=200)
    app_ = MenuApp(rt, store, Connection(auth, gateway), ACCOUNT, console=console)
    queue: asyncio.Queue[MenuKey | str] = asyncio.Queue()
    # main menu with no run history: Sao chép mới, Trạng thái, Lịch sử, Kênh đã join, Tài khoản,
    # Cấu hình, Thoát — six Down presses land on "Thoát".
    for _ in range(6):
        queue.put_nowait(MenuKey.DOWN)
    queue.put_nowait(MenuKey.ENTER)

    code = await app_.run(queue=queue)

    assert code == 0
    await store.close()


async def test_menu_app_shows_whoami_on_the_account_screen(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """Checks the pushed screen's own ``render()`` (pure, no terminal needed — the pattern
    ``ui/tui.py``'s tests use), not the ``Live``'s console output: ``Live(screen=True)`` only
    flushes to its console on its own schedule, so scraping it mid-run is not reliable in a test.
    """
    gateway, auth = FakeGateway(), FakeAuth(logged_in=ACCOUNT)
    rt = make_runtime(gateway=gateway, auth=auth, interactive=True)
    store = await Store.open(tmp_path / "t.db")
    console = Console(file=io.StringIO(), no_color=True, width=200)
    app_ = MenuApp(rt, store, Connection(auth, gateway), ACCOUNT, console=console)
    queue: asyncio.Queue[MenuKey | str] = asyncio.Queue()
    for _ in range(4):  # Sao chép mới -> Trạng thái -> Lịch sử -> Kênh đã join -> Tài khoản
        queue.put_nowait(MenuKey.DOWN)
    queue.put_nowait(MenuKey.ENTER)  # into Tài khoản

    async def quit_once_on_the_account_screen() -> None:
        while not isinstance(app_.current, AccountScreen):  # noqa: ASYNC110 - no event to await
            await asyncio.sleep(0.01)
        assert "Test User" in plain(app_.current.render())
        app_.request_quit(0)
        queue.put_nowait(MenuKey.ESC)  # unblock the queue.get() the app loop is waiting on

    task = asyncio.create_task(quit_once_on_the_account_screen())
    await app_.run(queue=queue)
    await task

    await store.close()


async def test_new_clone_opens_the_wizard_and_esc_returns_to_the_menu(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """Chặng 2: "Sao chép mới" is a real wizard now; Esc at its first question pops back to the
    main menu, which still works (six Down presses reach "Thoát")."""
    gateway, auth = FakeGateway(), FakeAuth(logged_in=ACCOUNT)
    gateway.add_channel("Source")
    rt = make_runtime(gateway=gateway, auth=auth, interactive=True)
    store = await Store.open(tmp_path / "t.db")
    console = Console(file=io.StringIO(), no_color=True, width=200)
    app_ = MenuApp(rt, store, Connection(auth, gateway), ACCOUNT, console=console)
    queue: asyncio.Queue[MenuKey | str] = asyncio.Queue()
    for key in [MenuKey.ENTER, MenuKey.ESC, *[MenuKey.DOWN] * 6, MenuKey.ENTER]:
        queue.put_nowait(key)

    code = await app_.run(queue=queue)

    assert code == 0
    assert gateway.calls_to("list_channels")  # the wizard really started
    await store.close()
