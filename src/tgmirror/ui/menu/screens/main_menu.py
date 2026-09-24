"""The screen the app opens on and always returns to.

What it offers follows the account: logged in, everything; logged out (or no api_id/api_hash
yet), "Đăng nhập" first and only what needs no Telegram connection (status, history)."""

from typing import Literal

from rich.console import RenderableType

from tgmirror.cli.keys import MenuKey
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.account import AccountScreen
from tgmirror.ui.menu.screens.channels import ChannelsScreen
from tgmirror.ui.menu.screens.clone import clone_screen
from tgmirror.ui.menu.screens.history import HistoryScreen
from tgmirror.ui.menu.screens.login import login_screen
from tgmirror.ui.menu.screens.resume import ResumeScreen
from tgmirror.ui.menu.screens.status_dashboard import StatusDashboardScreen
from tgmirror.ui.menu.widgets import SelectList
from tgmirror.ui.messages import t

_Action = Literal[
    "login", "clone", "resume", "retry", "status", "history", "channels", "account", "quit"
]


class MainMenuScreen(Screen):
    tick_interval = 3.0  # refresh whether a pair exists / a run is live, without needing a key

    def __init__(self, app: AppContext) -> None:
        self._app = app
        self._list: SelectList[_Action] = SelectList(items=[])
        self.footer_hint = t("menu.footer_main")

    async def on_enter(self) -> None:
        await self._refresh()

    async def on_return(self) -> None:
        await self._refresh()

    async def tick(self) -> ScreenResult | None:
        await self._refresh()
        return None

    async def _refresh(self) -> None:
        items: list[tuple[str, _Action]]
        if self._app.account is None:
            items = [
                (t("menu.item_login"), "login"),
                (t("menu.item_status"), "status"),
                (t("menu.item_history"), "history"),
                (t("menu.item_quit"), "quit"),
            ]
        else:
            items = [(t("menu.item_clone"), "clone")]
            if await self._app.store.list_pairs(1):
                items.append((t("menu.item_resume"), "resume"))
                items.append((t("menu.item_retry"), "retry"))
            items += [
                (t("menu.item_status"), "status"),
                (t("menu.item_history"), "history"),
                (t("menu.item_channels"), "channels"),
                (t("menu.item_account"), "account"),
                (t("menu.item_quit"), "quit"),
            ]
        if [value for _, value in items] != [value for _, value in self._list.items]:
            self._list = SelectList(items=items)  # other items: start at the top again
        else:
            self._list = SelectList(items=items, index=self._list.index)

    def render(self) -> RenderableType:
        return self._list.render()

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        chosen = self._list.handle_key(key)
        if chosen is None:
            return "stay"
        return await self._dispatch(chosen)

    async def _dispatch(self, action: _Action) -> ScreenResult:
        match action:
            case "quit":
                return "quit"
            case "login":
                return ("push", login_screen(self._app))
            case "clone":
                return ("push", clone_screen(self._app))
            case "resume" | "retry":
                return ("push", ResumeScreen(self._app, mode=action))
            case "status":
                return ("push", StatusDashboardScreen(self._app))
            case "history":
                return ("push", HistoryScreen(self._app))
            case "channels":
                return ("push", ChannelsScreen(self._app))
            case "account":
                return ("push", AccountScreen(self._app))
