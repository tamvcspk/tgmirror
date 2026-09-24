"""Esc must pop back a step on every list-driven screen.

Real bug (found by hand on a real terminal, 2026-09-23): ``SelectList.handle_key`` only
understands Up/Down/Enter and returns ``None`` for anything else, including Esc. A screen that
just forwards every key to its list and treats ``None`` as "stay" silently swallows Esc unless it
checks for it first — ``HistoryScreen``, ``ResumeScreen`` and ``_ConfirmLogout`` all did this.
``ChannelsScreen`` already checked Esc first (its own type-to-filter box has the same shape of
bug) — that one is the reference for what "stay"-forwarding must never do without an Esc guard.
"""

from dataclasses import dataclass
from typing import Any

from tgmirror.cli.keys import MenuKey
from tgmirror.ui.menu.screens.account import AccountScreen, _ConfirmLogout
from tgmirror.ui.menu.screens.config import ConfigScreen
from tgmirror.ui.menu.screens.history import HistoryScreen
from tgmirror.ui.menu.screens.resume import ResumeScreen


@dataclass
class _StubApp:
    account: Any = None


async def test_history_screen_esc_pops() -> None:
    screen = HistoryScreen(_StubApp())  # type: ignore[arg-type]

    assert await screen.handle_key(MenuKey.ESC) == "pop"


async def test_resume_screen_esc_pops() -> None:
    screen = ResumeScreen(_StubApp(), mode="resume")  # type: ignore[arg-type]

    assert await screen.handle_key(MenuKey.ESC) == "pop"


async def test_confirm_logout_esc_pops() -> None:
    screen = _ConfirmLogout(_StubApp())  # type: ignore[arg-type]

    assert await screen.handle_key(MenuKey.ESC) == "pop"


async def test_account_screen_esc_pops_when_logged_in() -> None:
    screen = AccountScreen(_StubApp(account=object()))  # type: ignore[arg-type]

    assert await screen.handle_key(MenuKey.ESC) == "pop"


async def test_config_screen_esc_pops() -> None:
    screen = ConfigScreen(_StubApp())  # type: ignore[arg-type]

    assert await screen.handle_key(MenuKey.ESC) == "pop"
