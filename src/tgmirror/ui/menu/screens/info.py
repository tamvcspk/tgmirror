"""A short message with nothing to do but read it: an error, a refusal, a "nothing to do" notice
that the classic CLI would have printed and exited on — here it is a dialog, and any key returns
to whatever pushed it (the process keeps running, unlike the CLI's ``typer.Exit``)."""

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.keys import MenuKey
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.messages import t


class InfoScreen(Screen):
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.footer_hint = t("menu.footer_info")

    def render(self) -> RenderableType:
        return Group(*(Text(line) for line in self._lines))

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        return "pop"
