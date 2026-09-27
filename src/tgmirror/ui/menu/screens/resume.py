""" "Chạy tiếp" / "Thử lại tin lỗi": pick a pair (skipped when there is only one, same as the
classic CLI's ``tgmirror run``/``tgmirror retry`` without a number) and start the run screen.

Deliberately does not use ``cli/wizard.py``'s ``pick_run`` (which prompts through ``rt.prompter``,
i.e. ``questionary``): the pair list is a plain ``SelectList`` here, drawn in the app's own frame.
"""

from typing import Literal

from rich.console import RenderableType

from tgmirror.cli.commands.retry import retry_flow_for
from tgmirror.cli.commands.run import ReadyToRun, ResumeElsewhere, reader_override_for, resume_flow
from tgmirror.cli.errors import describe
from tgmirror.cli.keys import MenuKey
from tgmirror.cli.wizard import resolve_dir_answer
from tgmirror.core.errors import TgMirrorError
from tgmirror.engine.runs import BackupDirMissing, begin_run
from tgmirror.store.runs import Run, StartedRun
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.run_screen import RunScreen
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.menu.widgets import SelectList
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Choice

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
        except BackupDirMissing as exc:
            # N6, Phase 15b: the pair's backup directory (Phase 11b restore) is gone — offer a
            # way out instead of an ``InfoScreen`` dead end (`begin_run` refuses before writing
            # anything, so nothing needs to be undone here).
            return ("push", self._recover_screen(target, exc))
        except TgMirrorError as exc:
            return ("push", InfoScreen([describe(exc)]))
        return await self._after_prepare(target, outcome)

    async def _after_prepare(
        self, target: Run, outcome: StartedRun | ResumeElsewhere | None
    ) -> ScreenResult:
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
            reader_override=reader_override_for(outcome.run.options.from_backup),
        )
        return ("push", screen)

    async def _prepare(
        self, target: Run, *, from_backup: str | None = None, fresh: bool = False
    ) -> StartedRun | ResumeElsewhere | None:
        """``from_backup``/``fresh`` (N6, Phase 15b): only ever passed by ``_recover_screen``
        below, to repoint or restart a restore pair whose directory went missing — an ordinary
        "Chạy tiếp"/"Thử lại tin lỗi" never sets them, keeping the pair's own recorded values."""
        store, gateway = self._app.store, self._app.gateway
        if self._mode == "retry":
            ready = await retry_flow_for(store, target, from_backup=from_backup)
            if ready is None:
                return None
        else:
            result = await resume_flow(
                rt=self._app.rt,
                store=store,
                number=str(target.id),
                from_backup=from_backup,
                fresh=fresh,
            )
            if isinstance(result, ResumeElsewhere):
                return result
            ready = result
        assert isinstance(ready, ReadyToRun)
        return await begin_run(store, gateway, ready.src, ready.dst, ready.request)

    def _recover_screen(self, target: Run, exc: BackupDirMissing) -> WizardScreen:
        async def flow(prompter: MenuPrompter) -> ScreenResult:
            choices: list[Choice[str]] = [Choice(t("resume.pick_new_dir"), "new_dir")]
            if self._mode == "resume":  # "chạy lại từ đầu" has no meaning for a retry
                choices.append(Choice(t("resume.start_fresh"), "fresh"))
            choices.append(Choice(t("resume.cancel"), "cancel"))
            picked = await prompter.select(str(exc.directory), choices)
            if picked == "cancel":
                return "pop"
            if picked == "new_dir":
                typed = await prompter.path(t("restore.pick_dir"), only_directories=True)
                new_dir = await resolve_dir_answer(typed)  # expands "~", as the wizard does
                outcome = await self._prepare(target, from_backup=str(new_dir))
            else:
                outcome = await self._prepare(target, fresh=True)
            return await self._after_prepare(target, outcome)

        return WizardScreen(t("resume.backup_dir_missing_title", id=target.id), flow)
