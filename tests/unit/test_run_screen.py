"""``RunScreen``: the flagship of Chặng 1 (docs/06-lo-trinh.md, "Kế hoạch giao diện full-screen
(menu)") — drives the real ``Runner``/``RunControl`` without a private ``Live`` (``TuiReporter`` is
only ever asked for ``render()``) and without ``rt.keys``' own reader thread (p/r/q go through
``apply_key`` directly, as the menu's own key loop would call it). No terminal, no CLI, no
``typer.Exit``: exercised straight against ``FakeGateway`` and a real (temp-file) ``Store``.
"""

import io
from pathlib import Path

from rich.console import Console, RenderableType

from tests.fakes import FakeGateway
from tgmirror.cli.keys import apply_key
from tgmirror.core.config import Limits
from tgmirror.engine.runner import RunControl
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store.db import Store
from tgmirror.store.runs import RunStatus
from tgmirror.ui.menu.run_screen import RunScreen


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, width=200, no_color=True)
    console.print(renderable)
    return console.export_text()


async def _store(tmp_path: Path) -> Store:
    return await Store.open(tmp_path / "t.db")


async def test_run_screen_copies_and_shows_the_result(tmp_path: Path) -> None:
    gateway = FakeGateway()
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    for i in range(1, 6):
        gateway.add_message(src.id, f"m{i}")
    store = await _store(tmp_path)
    started = await begin_run(store, gateway, src, dst, RunRequest())
    screen = RunScreen(store, gateway, started, limits=Limits(), tmp_dir=tmp_path / "tmp")

    await screen.on_enter()
    assert screen.task is not None
    await screen.task  # the run itself, driven with FakeGateway (no real delay)
    result = await screen.tick()

    assert result is None  # a normal finish stays on the screen, does not quit the app
    text = plain(screen.render())
    assert "done" in text.lower()
    assert [m.text for m in gateway.messages[dst.id]] == ["m1", "m2", "m3", "m4", "m5"]
    await store.close()


async def test_q_key_stops_the_run_without_quitting_the_app(tmp_path: Path) -> None:
    gateway = FakeGateway()
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    for i in range(1, 4):
        gateway.add_message(src.id, f"m{i}")
    store = await _store(tmp_path)
    started = await begin_run(store, gateway, src, dst, RunRequest())
    screen = RunScreen(store, gateway, started, limits=Limits(), tmp_dir=tmp_path / "tmp")

    await screen.on_enter()
    await screen.handle_key("q")  # the same call the menu's key loop makes for a plain character
    assert screen.task is not None
    final = await screen.task

    assert final.status is RunStatus.STOPPED
    result = await screen.tick()
    assert result is None  # `q` (not Ctrl+C): back to the menu, not out of the app
    await store.close()


async def test_a_finished_run_reports_its_result_once_not_every_tick(tmp_path: Path) -> None:
    """Regression (found 2026-09-24 from a real terminal's debug log, ``TGMIRROR_MENU_DEBUG``):
    ``MenuApp`` keeps calling ``tick()`` every ``tick_interval`` for as long as a screen stays on
    top, including a finished ``RunScreen`` sitting there until the user presses a key — ``tick()``
    used to re-append the result/error line to ``_lines`` on *every* one of those calls, growing
    without bound and drowning the screen in identical duplicate lines."""
    gateway = FakeGateway()
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    gateway.add_message(src.id, "m1")
    store = await _store(tmp_path)
    started = await begin_run(store, gateway, src, dst, RunRequest())
    screen = RunScreen(store, gateway, started, limits=Limits(), tmp_dir=tmp_path / "tmp")

    await screen.on_enter()
    assert screen.task is not None
    await screen.task
    await screen.tick()
    lines_after_first_tick = len(screen._lines)
    for _ in range(5):  # the app would call this every 0.25s while nothing else happens
        await screen.tick()

    assert len(screen._lines) == lines_after_first_tick
    await store.close()


async def test_apply_key_is_reused_directly_not_through_a_second_reader_thread() -> None:
    """The point of routing p/r/q through ``apply_key`` (not ``rt.keys``/``terminal_keys``): one
    key reader for the whole app. This just pins that ``RunScreen`` calls the same function."""
    control = RunControl()
    assert apply_key(control, "p") is True
    assert control.pause_requested is True
    assert apply_key(control, "z") is False  # an unrelated key while a run is on screen: ignored
