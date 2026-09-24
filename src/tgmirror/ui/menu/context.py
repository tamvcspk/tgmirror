"""What a screen needs of the app that hosts it (``ui/menu/app.py``), kept separate so screens
never import ``app`` directly — ``app`` is the one module that imports every screen, and a screen
importing it back would be a cycle."""

from typing import Protocol

from tgmirror.cli.runtime import Connection, Runtime
from tgmirror.core.auth import AccountInfo, TelegramAuth
from tgmirror.core.config import Config
from tgmirror.core.gateway import TelegramGateway
from tgmirror.store.db import Store
from tgmirror.ui.menu.screen import Screen


class AppContext(Protocol):
    rt: Runtime
    store: Store
    conn: Connection | None  # None: no api_id/api_hash yet, or logged out from the menu
    account: AccountInfo | None

    @property
    def auth(self) -> TelegramAuth:
        """``conn.auth``; raises ``NotLoggedIn`` without a connection."""
        ...

    @property
    def gateway(self) -> TelegramGateway:
        """``conn.gateway``; raises ``NotLoggedIn`` without a connection. Only screens reached
        while logged in use it (the main menu offers nothing else then)."""
        ...

    async def open_connection(self, config: Config) -> Connection: ...
    async def close_connection(self) -> None: ...
    def push(self, screen: Screen) -> None: ...
    def request_quit(self, code: int = 0) -> None: ...
