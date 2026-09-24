""" "Kênh đã join": the same list ``tgmirror channels`` shows, with type-to-filter."""

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.keys import MenuKey
from tgmirror.core.gateway import ChannelInfo
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.widgets import TypeToFilter
from tgmirror.ui.messages import t
from tgmirror.ui.tables import channel_label


class ChannelsScreen(Screen):
    def __init__(self, app: AppContext) -> None:
        self._app = app
        self._channels: list[ChannelInfo] = []
        self._filter = TypeToFilter()
        self.footer_hint = t("menu.footer_filter")

    async def on_enter(self) -> None:
        self._channels = await self._app.gateway.list_channels()

    def render(self) -> RenderableType:
        needle = self._filter.text.casefold()
        shown = [
            c
            for c in self._channels
            if needle in c.title.casefold() or needle in (c.username or "").casefold()
        ]
        lines = [Text(channel_label(c) + (" 🔒" if c.noforwards else "")) for c in shown]
        if not lines:
            lines = [Text(t("channels.empty"))]
        return Group(self._filter.render(), *lines)

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if key == MenuKey.ESC:
            return "pop"
        self._filter.handle_key(key)
        return "stay"
