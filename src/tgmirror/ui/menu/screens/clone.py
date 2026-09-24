""" "Sao chép mới": the ``tgmirror clone`` wizard, drawn in the frame, then the run screen.

The same ``CloneFlow`` (``cli/commands/clone.py``) the CLI runs with no flags given — so the same
questions, rules, warnings and confirmations — through a ``MenuPrompter`` where Esc goes back one
question. Nothing is written before its last confirmation; then the destination is created if
asked for, the run begins, and this screen gives way to ``RunScreen``.
"""

from tgmirror.cli.commands.clone import CloneFlow, CloneOptions, endpoint_lines
from tgmirror.engine.endpoints import materialize
from tgmirror.engine.runs import begin_run
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.run_screen import RunScreen
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import run_steps


def clone_screen(app: AppContext) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        gateway = app.gateway
        channels = await gateway.list_channels()
        if not channels:
            return ("replace", InfoScreen([t("channels.empty")]))
        clone = CloneFlow(
            app.rt,
            app.store,
            gateway,
            channels,
            CloneOptions(),
            prompter=prompter,
            interactive=True,
            echo=prompter.echo,
        )
        await run_steps(prompter, clone.steps())
        ready = clone.ready()
        endpoints = await materialize(gateway, ready.plan)
        started = await begin_run(app.store, gateway, endpoints.src, endpoints.dst, ready.request)
        screen = RunScreen(
            app.store,
            gateway,
            started,
            limits=app.rt.config().limits,
            tmp_dir=app.rt.paths.tmp_dir,
            intro=endpoint_lines(endpoints),
        )
        return ("replace", screen)

    return WizardScreen(t("menu.item_clone"), flow)
