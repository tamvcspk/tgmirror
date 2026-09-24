"""``ResumeScreen``: picking a pair for "Chạy tiếp"/"Thử lại tin lỗi" — including the case that
matters most for a real regression found by hand (2026-09-23): exactly one pair, so ``on_enter()``
decides on its own to start the run instead of showing a one-item list to pick from.
"""

from collections.abc import Callable
from pathlib import Path

from tests.fakes import FakeGateway
from tgmirror.cli.runtime import Runtime
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store.db import Store
from tgmirror.store.runs import RunStatus
from tgmirror.ui.menu.run_screen import RunScreen
from tgmirror.ui.menu.screens.resume import ResumeScreen

MakeRuntime = Callable[..., Runtime]


class _App:
    """Just enough of ``AppContext`` for ``ResumeScreen``: ``store``/``gateway``/``rt``."""

    def __init__(self, rt: Runtime, store: Store, gateway: FakeGateway) -> None:
        self.rt = rt
        self.store = store
        self.gateway = gateway


async def test_a_single_pair_auto_starts_the_run_screen_via_on_enter(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """Regression: this fast path used to call ``_start()`` and drop its result on the floor —
    ``RunScreen`` was built but never pushed, so ``RunScreen.on_enter()`` (which creates the task
    that actually drives the run) was never called either: the screen looked reachable but nothing
    was really running. ``MenuApp`` now applies whatever ``on_enter()`` returns, exactly like a
    key's result (see ``ui/menu/app.py::_apply``'s "push" case and ``ui/menu/screen.py``)."""
    gateway = FakeGateway()
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    gateway.add_message(src.id, "m1")
    store = await Store.open(tmp_path / "t.db")
    started = await begin_run(store, gateway, src, dst, RunRequest())
    await store.finish(started.run.id, RunStatus.DONE)  # a finished pair: `list_pairs` sees it,
    # `active_run` does not — otherwise the fresh `begin_run` below would hit `RunBusy`
    rt = make_runtime(gateway=gateway, root=tmp_path)
    screen = ResumeScreen(_App(rt, store, gateway), mode="resume")  # type: ignore[arg-type]

    result = await screen.on_enter()

    assert result is not None and result[0] == "push"
    pushed = result[1]
    assert isinstance(pushed, RunScreen)
    assert pushed.task is None  # `on_enter()` (which starts it) is `MenuApp`'s job, not this one's
    await store.close()


async def test_multiple_pairs_show_a_list_instead_of_auto_starting(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    store = await Store.open(tmp_path / "t.db")
    for i in range(2):
        src, dst = gateway.add_channel(f"Source{i}"), gateway.add_channel(f"Copy{i}")
        started = await begin_run(store, gateway, src, dst, RunRequest())
        await store.finish(started.run.id, RunStatus.DONE)
    rt = make_runtime(gateway=gateway, root=tmp_path)
    screen = ResumeScreen(_App(rt, store, gateway), mode="resume")  # type: ignore[arg-type]

    result = await screen.on_enter()

    assert result is None  # a real choice to make: stay on the list, do not auto-start anything
    assert len(screen._list.items) == 2
    await store.close()
