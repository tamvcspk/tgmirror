"""Badge "đang chạy ở nơi khác" on the menu header (docs/06-lo-trinh.md, item 4 left over from
Chặng 1 of the full-screen menu): shown when some *other* process holds an active run, never for
the run this app's own ``RunScreen`` is driving."""

import io
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tgmirror.cli.runtime import Connection, Runtime
from tgmirror.core.config import Limits
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store.db import Store
from tgmirror.store.runs import RunStatus
from tgmirror.ui.menu.app import MenuApp
from tgmirror.ui.menu.run_screen import RunScreen

MakeRuntime = Callable[..., Runtime]


def _app(rt: Runtime, store: Store, gateway: FakeGateway, auth: FakeAuth) -> MenuApp:
    console = Console(file=io.StringIO(), no_color=True, width=200)
    return MenuApp(rt, store, Connection(auth, gateway), ACCOUNT, console=console)


async def test_badge_shows_a_run_held_by_another_process(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway, auth = FakeGateway(), FakeAuth(logged_in=ACCOUNT)
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    store = await Store.open(tmp_path / "t.db")
    started = await begin_run(store, gateway, src, dst, RunRequest())
    await store.set_status(started.run.id, RunStatus.RUNNING)  # "elsewhere": no RunScreen here
    app = _app(make_runtime(gateway=gateway, auth=auth), store, gateway, auth)

    await app._refresh_badge()

    assert app._badge is not None
    await store.close()


async def test_badge_stays_hidden_for_the_app_s_own_run(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway, auth = FakeGateway(), FakeAuth(logged_in=ACCOUNT)
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    store = await Store.open(tmp_path / "t.db")
    started = await begin_run(store, gateway, src, dst, RunRequest())
    await store.set_status(started.run.id, RunStatus.RUNNING)
    app = _app(make_runtime(gateway=gateway, auth=auth), store, gateway, auth)
    app.own_run_id = started.run.id  # as pushing its RunScreen would have set it

    await app._refresh_badge()

    assert app._badge is None
    await store.close()


async def test_badge_is_none_with_no_active_run(make_runtime: MakeRuntime, tmp_path: Path) -> None:
    gateway, auth = FakeGateway(), FakeAuth(logged_in=ACCOUNT)
    store = await Store.open(tmp_path / "t.db")
    app = _app(make_runtime(gateway=gateway, auth=auth), store, gateway, auth)

    await app._refresh_badge()

    assert app._badge is None
    await store.close()


async def test_pushing_a_run_screen_records_it_as_our_own(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway, auth = FakeGateway(), FakeAuth(logged_in=ACCOUNT)
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    store = await Store.open(tmp_path / "t.db")
    started = await begin_run(store, gateway, src, dst, RunRequest())
    app = _app(make_runtime(gateway=gateway, auth=auth), store, gateway, auth)
    screen = RunScreen(store, gateway, started, limits=Limits(), tmp_dir=tmp_path / "tmp")

    await app._apply(("push", screen))

    assert app.own_run_id == started.run.id
    assert screen.task is not None
    await screen.task  # let the run finish before the store closes under it
    await store.close()
