"""The screen the app opens on and always returns to."""

from typing import Literal

from rich.console import RenderableType

from tgmirror.cli.keys import MenuKey
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.account import AccountScreen
from tgmirror.ui.menu.screens.channels import ChannelsScreen
from tgmirror.ui.menu.screens.history import HistoryScreen
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.resume import ResumeScreen
from tgmirror.ui.menu.screens.status_dashboard import StatusDashboardScreen
from tgmirror.ui.menu.widgets import SelectList
from tgmirror.ui.messages import t

_Action = Literal["clone", "resume", "retry", "status", "history", "channels", "account", "quit"]


class MainMenuScreen(Screen):
    tick_interval = 3.0  # refresh whether a pair exists / a run is live, without needing a key

    def __init__(self, app: AppContext) -> None:
        self._app = app
        self._list: SelectList[_Action] = SelectList(items=[])
        self.footer_hint = t("menu.footer_main")

    async def on_enter(self) -> None:
        await self._refresh()

    async def tick(self) -> ScreenResult | None:
        await self._refresh()
        return None

    async def _refresh(self) -> None:
        has_pairs = bool(await self._app.store.list_pairs(1))
        items: list[tuple[str, _Action]] = [(t("menu.item_clone"), "clone")]
        if has_pairs:
            items.append((t("menu.item_resume"), "resume"))
            items.append((t("menu.item_retry"), "retry"))
        items += [
            (t("menu.item_status"), "status"),
            (t("menu.item_history"), "history"),
            (t("menu.item_channels"), "channels"),
            (t("menu.item_account"), "account"),
            (t("menu.item_quit"), "quit"),
        ]
        index = min(self._list.index, len(items) - 1)
        self._list = SelectList(items=items, index=index)

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
            case "clone":
                # Chặng 2: MenuPrompter + tái dùng cli/wizard.py. Cho tới đó, nói rõ thay vì im
                # lặng không làm gì (mục vẫn hiện trong menu vì lệnh cổ điển vẫn dùng được).
                return ("push", InfoScreen([t("menu.clone_not_yet")]))
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
