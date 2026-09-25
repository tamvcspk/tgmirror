from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import keyring
import pytest

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway, FakeKeyring, ScriptedPrompter
from tgmirror.cli.keys import KeyProvider, no_keys
from tgmirror.cli.runtime import Connection, ReporterFactory, Runtime, plain_reporter
from tgmirror.core.config import Config
from tgmirror.core.paths import Paths

API_ENV = {"TGMIRROR_API_ID": "12345", "TGMIRROR_API_HASH": "0123456789abcdef0123456789abcdef"}


@pytest.fixture(autouse=True)
def english_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assertions on user-facing text use the English table; language switching has own test."""
    monkeypatch.setenv("TGMIRROR_LANG", "en")


@pytest.fixture(autouse=True)
def fake_keyring() -> Iterator[FakeKeyring]:
    """No test ever touches the machine's real keyring (docs/06-lo-trinh.md, Phase 9): a fresh
    in-memory backend per test, restored to whatever was installed before afterwards."""
    original = keyring.get_keyring()
    fake = FakeKeyring()
    keyring.set_keyring(fake)
    try:
        yield fake
    finally:
        keyring.set_keyring(original)


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def make_runtime(tmp_path: Path) -> Callable[..., Runtime]:
    """Build a ``Runtime`` on fakes. Logged in, credentials set, no terminal, unless told."""

    def factory(
        *,
        gateway: FakeGateway | None = None,
        auth: FakeAuth | None = None,
        prompter: ScriptedPrompter | None = None,
        interactive: bool = False,
        env: Mapping[str, str] | None = None,
        root: Path | None = None,  # own config/data dirs (a second runtime in one test)
        keys: KeyProvider = no_keys,  # hotkeys while a clone runs
        reporter: ReporterFactory = plain_reporter,  # progress while a clone runs
    ) -> Runtime:
        gw = gateway if gateway is not None else FakeGateway()
        au = auth if auth is not None else FakeAuth(logged_in=ACCOUNT)

        @asynccontextmanager
        async def connect(config: Config) -> AsyncIterator[Connection]:
            yield Connection(au, gw)

        return Runtime(
            paths=Paths.under(root or tmp_path),
            connect=connect,
            prompter=prompter if prompter is not None else ScriptedPrompter(),
            interactive=interactive,
            env=API_ENV if env is None else env,
            keys=keys,
            reporter=reporter,
        )

    return factory
