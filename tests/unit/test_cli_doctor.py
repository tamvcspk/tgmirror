"""``tgmirror doctor`` (docs/02-cli-ux.md): session, cryptg, destination permissions, safety notes.
Every check is independent — one being broken (no session, a destination that lost its rights)
must not hide the others or make the command exit non-zero.

Plain (non-async) test functions throughout, like ``test_cli_retry.py``'s: ``CliRunner.invoke``
does its own ``asyncio.run`` internally, which cannot nest inside a pytest-asyncio test's already
running loop, so any ``Store``/``begin_run`` setup below also goes through a bare ``asyncio.run``.
"""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import keyring
import keyring.backends.fail
from typer.testing import CliRunner

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import ChannelInfo
from tgmirror.core.paths import Paths
from tgmirror.core.secrets import write_keyring
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store.db import Store

MakeRuntime = Callable[..., Runtime]

runner = CliRunner()


def _seed_pair(gateway: FakeGateway, paths: Paths) -> tuple[ChannelInfo, ChannelInfo]:
    """A finished clone of a fresh pair, so ``store.list_pairs()`` (and so ``doctor``) sees it."""

    async def go() -> tuple[ChannelInfo, ChannelInfo]:
        paths.ensure()
        src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
        store = await Store.open(paths.db_path)
        await begin_run(store, gateway, src, dst, RunRequest())
        await store.close()
        return src, dst

    return asyncio.run(go())


def test_reports_missing_credentials_without_a_terminal_or_env(make_runtime: MakeRuntime) -> None:
    rt = make_runtime(env={})

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "api_id" in result.output
    assert "cryptg" in result.output.lower()
    assert "no source/destination pair yet" in result.output


def test_reports_credential_source(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT))  # default env: API_ENV

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Credential: from env" in result.output


def test_reports_keyring_as_the_source(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    write_keyring(1, "abc")
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT), env={})

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Credential: from keyring (" in result.output


def test_suggests_moving_config_toml_credentials_to_a_usable_keyring(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT), env={})
    rt.paths.config_dir.mkdir(parents=True)
    rt.paths.config_file.write_text('api_id = 1\napi_hash = "abc"\n', encoding="utf-8")

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Credential: from config.toml" in result.output
    assert "run `tgmirror login` again" in result.output


def test_does_not_suggest_moving_without_a_usable_keyring(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    keyring.set_keyring(keyring.backends.fail.Keyring())
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT), env={})
    rt.paths.config_dir.mkdir(parents=True)
    rt.paths.config_file.write_text('api_id = 1\napi_hash = "abc"\n', encoding="utf-8")

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "run `tgmirror login` again" not in result.output


def test_reports_not_logged_in(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=None))

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "not logged in" in result.output


def test_not_logged_in_with_an_existing_pair_says_it_needs_a_session(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    paths = Paths.under(tmp_path)
    _seed_pair(gateway, paths)
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=None), root=tmp_path)

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "need a session" in result.output


def test_reports_session_ok_and_no_destinations_yet(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT))

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "logged in as" in result.output
    assert "no source/destination pair yet" in result.output


def test_reports_a_still_writable_destination(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    paths = Paths.under(tmp_path)
    _seed_pair(gateway, paths)
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT), root=tmp_path)

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Copy" in result.output
    assert "still writable" in result.output


def test_reports_a_destination_that_lost_write_access(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    paths = Paths.under(tmp_path)
    _, dst = _seed_pair(gateway, paths)
    gateway.channels[dst.id] = replace(gateway.channels[dst.id], can_post=False)
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT), root=tmp_path)

    result = runner.invoke(app, ["doctor"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "NO LONGER writable" in result.output
