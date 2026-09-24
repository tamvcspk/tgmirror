""" "Tài khoản": who is logged in, and a confirm-then-log-out.

No login form here yet (Chặng 2: needs ``MenuPrompter``'s masked ``text``/``secret`` fields to
reuse ``core/auth.py``'s login flow safely). After a log out from here the account is gone for the
rest of this app session; logging back in still means leaving the app and running `tgmirror login`.
"""

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.commands.auth import who
from tgmirror.cli.keys import MenuKey
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.widgets import SelectList
from tgmirror.ui.messages import t


class AccountScreen(Screen):
    def __init__(self, app: AppContext) -> None:
        self._app = app
        self.footer_hint = t("menu.footer_back")

    def render(self) -> RenderableType:
        account = self._app.account
        line = t("whoami.line", who=who(account), id=account.id) if account else t("logout.none")
        return Text(line)

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if self._app.account is None or key != MenuKey.ENTER:
            return "pop"
        return ("push", _ConfirmLogout(self._app))


class _ConfirmLogout(Screen):
    def __init__(self, app: AppContext) -> None:
        self._app = app
        self._list: SelectList[bool] = SelectList(
            items=[(t("menu.no"), False), (t("menu.yes"), True)]
        )
        self.footer_hint = t("menu.footer_pick")

    def render(self) -> RenderableType:
        account = self._app.account
        who_line = who(account) if account else ""
        return Group(Text(t("menu.confirm_logout", who=who_line)), self._list.render())

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if key == MenuKey.ESC:
            return "pop"
        chosen = self._list.handle_key(key)
        if chosen is None:
            return "stay"
        if not chosen:
            return "pop"
        account = self._app.account
        if account is not None:
            await self._app.auth.log_out()
            self._app.account = None
            return ("push", InfoScreen([t("logout.ok", who=who(account))]))
        return "pop"
