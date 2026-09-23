"""What a command needs from the outside world, gathered in one injectable object.

Commands never build a Telethon client or call ``questionary`` themselves: they use the
``Runtime`` they find in ``ctx.obj``. The default one talks to real Telegram; tests pass their own
(``FakeGateway``, scripted prompts) through ``CliRunner.invoke(..., obj=runtime)``.
"""

import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    nullcontext,
)
from dataclasses import dataclass
from os import environ

import typer

from tgmirror.cli.keys import KeyProvider, no_keys, terminal_keys
from tgmirror.core.auth import TelegramAuth
from tgmirror.core.config import Config, Limits, load_config
from tgmirror.core.errors import NotLoggedIn
from tgmirror.core.gateway import TelegramGateway
from tgmirror.core.paths import Paths
from tgmirror.core.telethon_gateway import telethon_session
from tgmirror.engine.runner import Reporter
from tgmirror.store.db import Store
from tgmirror.store.runs import Run
from tgmirror.ui.progress import LineReporter
from tgmirror.ui.prompts import Prompter, QuestionaryPrompter
from tgmirror.ui.tui import TuiReporter

# ``Runtime.reporter``: how a clone's progress is shown while it runs (p/r/q hotkeys are ``keys``,
# above — both depend on a real terminal, so both are injectable the same way for tests). Takes the
# run as it stands when it starts, so a TUI can seed its panel instead of showing nothing until the
# first batch commits (a run of one big file can go a long time without one).
ReporterFactory = Callable[[Limits, Run], AbstractContextManager[Reporter]]


def plain_reporter(limits: Limits, run: Run) -> AbstractContextManager[Reporter]:
    """The default: no terminal (or a test), one line at a time (``ui/progress.py``)."""
    return nullcontext(LineReporter(typer.echo))


def terminal_reporter(limits: Limits, run: Run) -> AbstractContextManager[Reporter]:
    """The Rich Live view (``ui/tui.py``), used while a real terminal is attached."""
    return TuiReporter(limits, run)


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
    keys: KeyProvider = no_keys  # hotkeys (p/r/q) while a clone runs
    reporter: ReporterFactory = plain_reporter  # how a clone's progress is shown while it runs

    def config(self) -> Config:
        return load_config(self.paths, self.env)


@asynccontextmanager
async def authorized(rt: Runtime) -> AsyncIterator[Connection]:
    """A connection whose session is logged in; raises ``NotLoggedIn`` otherwise."""
    async with rt.connect(rt.config()) as conn:
        if await conn.auth.account() is None:
            raise NotLoggedIn("no valid session")
        yield conn


@asynccontextmanager
async def opened_store(rt: Runtime) -> AsyncIterator[Store]:
    """The SQLite state (created and migrated on first use), closed on exit."""
    rt.paths.ensure()
    async with await Store.open(rt.paths.db_path) as store:
        yield store


def default_runtime() -> Runtime:
    """Real Telegram, real terminal. Cheap to build: nothing is opened until a command connects."""
    paths = Paths.default()

    @asynccontextmanager
    async def connect(config: Config) -> AsyncIterator[Connection]:
        async with telethon_session(paths, config) as (auth, gateway):
            yield Connection(auth, gateway)

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    return Runtime(
        paths=paths,
        connect=connect,
        prompter=QuestionaryPrompter(),
        interactive=interactive,
        env=environ,
        keys=terminal_keys,
        reporter=terminal_reporter if interactive else plain_reporter,
    )
