"""What a screen needs of the app that hosts it (``ui/menu/app.py``), kept separate so screens
never import ``app`` directly — ``app`` is the one module that imports every screen, and a screen
importing it back would be a cycle."""

from typing import Protocol

from tgmirror.cli.runtime import Runtime
from tgmirror.core.auth import AccountInfo, TelegramAuth
from tgmirror.core.gateway import TelegramGateway
from tgmirror.store.db import Store
from tgmirror.ui.menu.screen import Screen


class AppContext(Protocol):
    rt: Runtime
    store: Store
    auth: TelegramAuth
    gateway: TelegramGateway
    account: AccountInfo | None

    def push(self, screen: Screen) -> None: ...
    def request_quit(self, code: int = 0) -> None: ...
