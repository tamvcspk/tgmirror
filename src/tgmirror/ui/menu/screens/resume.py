""" "Chạy tiếp" / "Thử lại tin lỗi": pick a pair (skipped when there is only one, same as the
classic CLI's ``tgmirror run``/``tgmirror retry`` without a number) and start the run screen.

Deliberately does not use ``cli/wizard.py``'s ``pick_run`` (which prompts through ``rt.prompter``,
i.e. ``questionary``): the pair list is a plain ``SelectList`` here, drawn in the app's own frame.
"""

from typing import Literal

from rich.console import RenderableType

from tgmirror.cli.commands.retry import retry_flow_for
from tgmirror.cli.commands.run import ReadyToRun, ResumeElsewhere, resume_flow
from tgmirror.cli.errors import describe
from tgmirror.cli.keys import MenuKey
from tgmirror.core.errors import TgMirrorError
from tgmirror.engine.runs import begin_run
from tgmirror.store.runs import Run, StartedRun
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.run_screen import RunScreen
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.widgets import SelectList
from tgmirror.ui.messages import t

PAIR_CHOICES = 10  # same as `cli/commands/run.py`'s wizard offer


class ResumeScreen(Screen):
    def __init__(self, app: AppContext, *, mode: Literal["resume", "retry"]) -> None:
        self._app = app
        self._mode = mode
        self._list: SelectList[Run] = SelectList(items=[])
        self.footer_hint = t("menu.footer_pick")

    async def on_enter(self) -> ScreenResult | None:
        pairs = await self._app.store.list_pairs(PAIR_CHOICES)
        items = [
            (
                t(
                    "run.pick_pair_line",
                    id=p.id,
                    src=p.src_title,
                    dst=p.dst_title,
                    mode=p.mode,
                    status=t(f"status.{p.status}"),
                ),
                p,
            )
            for p in pairs
        ]
        self._list = SelectList(items=items)
        if len(pairs) == 1:  # no real choice: go straight in, like the classic CLI
            return await self._start(pairs[0])
        return None

    def render(self) -> RenderableType:
        return self._list.render()

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if key == MenuKey.ESC:
            return "pop"
        chosen = self._list.handle_key(key)
        if chosen is None:
            return "stay"
        return await self._start(chosen)

    async def _start(self, target: Run) -> ScreenResult:
        try:
            outcome = await self._prepare(target)
        except TgMirrorError as exc:
            return ("push", InfoScreen([describe(exc)]))
        if outcome is None:
            return ("push", InfoScreen([t("retry.nothing", id=target.id)]))
        if isinstance(outcome, ResumeElsewhere):
            return ("push", InfoScreen([t("run.resumed_elsewhere", id=outcome.run_id)]))
        limits = self._app.rt.config().limits
        failed_count = (
            await self._app.store.count_failed(target.id) if self._mode == "retry" else None
        )
        screen = RunScreen(
            self._app.store,
            self._app.gateway,
            outcome,
            limits=limits,
            tmp_dir=self._app.rt.paths.tmp_dir,
            failed_count=failed_count,
        )
        return ("push", screen)

    async def _prepare(self, target: Run) -> StartedRun | ResumeElsewhere | None:
        store, gateway = self._app.store, self._app.gateway
        if self._mode == "retry":
            ready = await retry_flow_for(store, target)
            if ready is None:
                return None
        else:
            result = await resume_flow(rt=self._app.rt, store=store, number=str(target.id))
            if isinstance(result, ResumeElsewhere):
                return result
            ready = result
        assert isinstance(ready, ReadyToRun)
        return await begin_run(store, gateway, ready.src, ready.dst, ready.request)
