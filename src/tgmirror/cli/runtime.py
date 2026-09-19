"""What a command needs from the outside world, gathered in one injectable object.

Commands never build a Telethon client or call ``questionary`` themselves: they use the
``Runtime`` they find in ``ctx.obj``. The default one talks to real Telegram; tests pass their own
(``FakeGateway``, scripted prompts) through ``CliRunner.invoke(..., obj=runtime)``.
"""

import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from os import environ

from tgmirror.core.auth import TelegramAuth
from tgmirror.core.config import Config, load_config
from tgmirror.core.errors import NotLoggedIn
from tgmirror.core.gateway import TelegramGateway
from tgmirror.core.paths import Paths
from tgmirror.core.telethon_gateway import telethon_session
from tgmirror.ui.prompts import Prompter, QuestionaryPrompter


@dataclass(frozen=True, slots=True)
class Connection:
    auth: TelegramAuth
    gateway: TelegramGateway


Connector = Callable[[Config], AbstractAsyncContextManager[Connection]]


@dataclass(frozen=True, slots=True)
class Runtime:
    paths: Paths
    connect: Connector
    prompter: Prompter
    interactive: bool  # a terminal is attached, so prompting is possible
    env: Mapping[str, str]
    debug: bool = False  # show tracebacks instead of one-line errors

    def config(self) -> Config:
        return load_config(self.paths, self.env)


@asynccontextmanager
async def authorized(rt: Runtime) -> AsyncIterator[Connection]:
    """A connection whose session is logged in; raises ``NotLoggedIn`` otherwise."""
    async with rt.connect(rt.config()) as conn:
        if await conn.auth.account() is None:
            raise NotLoggedIn("no valid session")
        yield conn


def default_runtime() -> Runtime:
    """Real Telegram, real terminal. Cheap to build: nothing is opened until a command connects."""
    paths = Paths.default()

    @asynccontextmanager
    async def connect(config: Config) -> AsyncIterator[Connection]:
        async with telethon_session(paths, config) as (auth, gateway):
            yield Connection(auth, gateway)

    return Runtime(
        paths=paths,
        connect=connect,
        prompter=QuestionaryPrompter(),
        interactive=sys.stdin.isatty() and sys.stdout.isatty(),
        env=environ,
    )
