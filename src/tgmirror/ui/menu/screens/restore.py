""" "Restore": the ``tgmirror restore`` wizard, drawn in the frame, then the run screen (Phase 15).
The same ``RestoreFlow`` (``cli/commands/restore.py``) the CLI runs, through a ``MenuPrompter`` —
mirrors ``screens/clone.py``, but hands off to ``RunScreen`` with a ``reader_override`` (the source
side reads the backup directory, not the gateway), exactly as ``cli/commands/restore.py`` calls
``cli/commands/run.py::execute``.
"""

from tgmirror.cli.commands.restore import RestoreFlow, RestoreOptions
from tgmirror.engine.endpoints import materialize
from tgmirror.engine.runs import begin_run
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.run_screen import RunScreen
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import run_steps
from tgmirror.ui.tables import channel_label


def restore_screen(app: AppContext) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        gateway = app.gateway
        channels = await gateway.list_channels()
        restore_flow = RestoreFlow(
            app.rt,
            app.store,
            gateway,
            channels,
            RestoreOptions(),
            prompter=prompter,
            interactive=True,
            echo=prompter.echo,
        )
        await run_steps(prompter, restore_flow.steps())
        ready = restore_flow.ready()
        endpoints = await materialize(gateway, ready.plan)
        started = await begin_run(app.store, gateway, endpoints.src, endpoints.dst, ready.request)
        key = "restore.dst_created" if endpoints.created else "restore.dst"
        screen = RunScreen(
            app.store,
            gateway,
            started,
            limits=app.rt.config().limits,
            tmp_dir=app.rt.paths.tmp_dir,
            intro=[t(key, channel=channel_label(endpoints.dst))],
            reader_override=ready.reader,
        )
        return ("replace", screen)

    return WizardScreen(t("menu.item_restore"), flow)
