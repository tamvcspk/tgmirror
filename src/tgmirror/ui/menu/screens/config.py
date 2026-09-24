""" "Cấu hình": the paths ``tgmirror`` uses (read-only) and every ``[limits]`` key, editable in
place — same ``core.config.set_limit`` as ``tgmirror config set``, drawn in the frame like the
wizard screens (Chặng 2, ``ui/menu/prompter.py``)."""

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.keys import MenuKey
from tgmirror.core.config import LIMIT_KEYS, format_limit, set_limit
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.menu.widgets import SelectList
from tgmirror.ui.messages import t


class ConfigScreen(Screen):
    def __init__(self, app: AppContext) -> None:
        self._app = app
        self._list: SelectList[str] = SelectList(items=[])
        self.footer_hint = t("menu.footer_pick")

    async def on_enter(self) -> None:
        self._refresh()

    async def on_return(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        limits = self._app.rt.config().limits
        items = [(f"{key} = {format_limit(getattr(limits, key))}", key) for key in LIMIT_KEYS]
        self._list = SelectList(items=items, index=self._list.index)

    def render(self) -> RenderableType:
        paths = self._app.rt.paths
        head = [
            Text(t("config.paths_title"), style="bold"),
            Text(t("config.path_line", label=t("config.path_config"), path=paths.config_file)),
            Text(t("config.path_line", label=t("config.path_db"), path=paths.db_path)),
            Text(t("config.path_line", label=t("config.path_sessions"), path=paths.sessions_dir)),
            Text(""),
            Text(t("config.limits_title"), style="bold"),
        ]
        return Group(*head, self._list.render())

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if key == MenuKey.ESC:
            return "pop"
        chosen = self._list.handle_key(key)
        if chosen is None:
            return "stay"
        return ("push", _edit_limit_screen(self._app, chosen))


def _edit_limit_screen(app: AppContext, key: str) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        current = getattr(app.rt.config().limits, key)
        raw = await prompter.text(
            t("menu.config_prompt_value", name=key), default=format_limit(current)
        )
        new_limits = set_limit(app.rt.paths, key, raw)
        saved = t("config.saved", name=key, value=format_limit(getattr(new_limits, key)))
        return ("replace", InfoScreen([saved]))

    return WizardScreen(t("menu.config_edit_title", name=key), flow)
