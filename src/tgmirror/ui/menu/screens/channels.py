""" "Kênh đã join": the same list ``tgmirror channels`` shows, with type-to-filter and ↑/↓ to
scroll — a long list gets exactly the rows the frame leaves it (``Fit``, shared with the wizard's
question rendering), same as the source picker in the clone wizard."""

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.keys import MenuKey
from tgmirror.core.gateway import ChannelInfo
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.widgets import Fit, SelectList, TypeToFilter
from tgmirror.ui.messages import t
from tgmirror.ui.tables import channel_label


class ChannelsScreen(Screen):
    def __init__(self, app: AppContext) -> None:
        self._app = app
        self._channels: list[ChannelInfo] = []
        self._filter = TypeToFilter()
        self._list: SelectList[None] = SelectList(items=[])
        self.footer_hint = t("menu.footer_filter")

    async def on_enter(self) -> None:
        self._channels = await self._app.gateway.list_channels()
        self._recompute()

    def _recompute(self) -> None:
        needle = self._filter.text.casefold()
        shown = [
            c
            for c in self._channels
            if needle in c.title.casefold() or needle in (c.username or "").casefold()
        ]
        labels = [channel_label(c) + (" 🔒" if c.noforwards else "") for c in shown]
        self._list = SelectList(items=[(label, None) for label in labels])

    def render(self) -> RenderableType:
        if not self._list.items:
            return Group(self._filter.render(), Text(t("channels.empty")))
        return Fit(self._filter.render(), self._list.render)

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if key == MenuKey.ESC:
            return "pop"
        if key in (MenuKey.UP, MenuKey.DOWN):
            self._list.handle_key(key)
        elif self._filter.handle_key(key):
            self._recompute()
        return "stay"
