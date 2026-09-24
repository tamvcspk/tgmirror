"""The full-screen menu app (Chặng 1, ``docs/06-lo-trinh.md``, "Kế hoạch giao diện full-screen
(menu)"): one ``Live(screen=True)`` for the app's whole life, entered only when ``tgmirror`` is
typed bare with a real terminal (``cli/app.py``). Never a daemon: the loop below is exactly this
process's own lifetime, from ``launch()`` being awaited to it returning an exit code.
"""

import asyncio

import typer
from rich.console import Console
from rich.live import Live

from tgmirror.cli.commands.auth import who
from tgmirror.cli.keys import MenuKey, menu_key_queue
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.core.auth import AccountInfo, TelegramAuth
from tgmirror.core.gateway import TelegramGateway
from tgmirror.store.db import Store
from tgmirror.ui.menu import debug, frame
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.main_menu import MainMenuScreen
from tgmirror.ui.messages import t


class MenuApp:
    """Owns the ``Live``/``Layout`` and the stack of ``Screen``s; satisfies ``AppContext``."""

    def __init__(
        self,
        rt: Runtime,
        store: Store,
        auth: TelegramAuth,
        gateway: TelegramGateway,
        account: AccountInfo | None,
        *,
        console: Console | None = None,
    ) -> None:
        self.rt = rt
        self.store = store
        self.auth = auth
        self.gateway = gateway
        self.account = account
        self._stack: list[Screen] = [MainMenuScreen(self)]
        self._layout = frame.build_layout()
        # auto_refresh=False: one writer only. Rich's own background refresh thread would call
        # `.refresh()` on its own schedule too, racing this app's own explicit calls (from the
        # main event-loop thread, after every key/tick) for no benefit — this app already redraws
        # after every state change, so a second, independent refresh clock only adds a chance of
        # the two stepping on each other instead of making anything more current.
        self._live = Live(
            self._layout, console=console, screen=True, transient=True, auto_refresh=False
        )
        self._quit_code: int | None = None

    def push(self, screen: Screen) -> None:
        self._stack.append(screen)

    def request_quit(self, code: int = 0) -> None:
        self._quit_code = code

    @property
    def current(self) -> Screen:
        """The screen on top of the stack. Public so a test can inspect it (or its ``render()``)
        directly instead of scraping the ``Live``'s console output while the app is running."""
        return self._stack[-1]

    async def run(self, queue: "asyncio.Queue[MenuKey | str] | None" = None) -> int:
        """Drive the app until a screen asks to quit. ``queue``: for tests, which build one and
        feed it a scripted key sequence instead of a real terminal's ``menu_key_queue``."""
        if queue is not None:
            return await self._drive(queue)
        loop = asyncio.get_running_loop()
        with menu_key_queue(loop) as real_queue:
            if real_queue is None:  # an odd terminal `open_reader()` could not handle
                typer.echo(t("menu.no_terminal"), err=True)
                return 1
            return await self._drive(real_queue)

    async def _drive(self, queue: "asyncio.Queue[MenuKey | str]") -> int:
        debug.log("drive.start", screen=type(self._stack[0]).__name__)
        await self._apply(await self._stack[0].on_enter())
        with self._live:
            self._redraw()
            iteration = 0
            while self._quit_code is None:
                iteration += 1
                top = self._stack[-1]
                key: MenuKey | str | None
                debug.log(
                    "loop.wait", n=iteration, screen=type(top).__name__, timeout=top.tick_interval
                )
                try:
                    key = await asyncio.wait_for(queue.get(), timeout=top.tick_interval)
                except TimeoutError:
                    key = None
                debug.log("loop.woke", n=iteration, key=None if key is None else str(key))
                result = await top.tick() if key is None else await top.handle_key(key)
                debug.log("loop.result", n=iteration, result=repr(result))
                await self._apply(result)
                self._redraw()
        debug.log("drive.stop", quit_code=self._quit_code)
        return self._quit_code or 0

    async def _apply(self, result: ScreenResult | None) -> None:
        match result:
            case None | "stay":
                return
            case "quit":
                self.request_quit(0)
            case ("quit", int(code)):
                self.request_quit(code)
            case "pop":
                if len(self._stack) > 1:
                    self._stack.pop()
                    debug.log("apply.pop", top=type(self._stack[-1]).__name__)
            case ("push", screen):
                self._stack.append(screen)
                debug.log("apply.push", top=type(screen).__name__)
                await self._apply(await screen.on_enter())  # may itself decide to push/pop again

    def _redraw(self) -> None:
        top = self._stack[-1]
        self._layout["header"].update(frame.header(self._who(), None))
        self._layout["body"].update(top.render())
        self._layout["footer"].update(frame.footer(top.footer_hint))
        self._live.refresh()
        debug.log("redraw", screen=type(top).__name__)

    def _who(self) -> str | None:
        return who(self.account) if self.account is not None else None


async def launch(rt: Runtime) -> int:
    """Entry point ``cli/app.py`` calls for a bare ``tgmirror`` on a real terminal."""
    debug.init()
    debug.log("launch")
    async with opened_store(rt) as store, authorized(rt) as conn:
        account = await conn.auth.account()
        app = MenuApp(rt, store, conn.auth, conn.gateway, account)
        return await app.run()
