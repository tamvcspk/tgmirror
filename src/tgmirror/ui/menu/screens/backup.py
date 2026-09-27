""" "Backup": the ``tgmirror backup`` wizard, drawn in the frame, then the backup progress screen
(Phase 15). The same ``BackupFlow`` (``cli/commands/backup.py``) the CLI runs with no flags given,
through a ``MenuPrompter`` where Esc goes back one question — mirrors ``screens/clone.py``.
"""

from tgmirror.cli.commands.backup import BackupFlow, BackupOptions
from tgmirror.engine.backup import begin_backup
from tgmirror.ui.menu.backup_screen import BackupScreen
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import run_steps


def backup_screen(app: AppContext) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        gateway = app.gateway
        channels = await gateway.list_channels()
        if not channels:
            return ("replace", InfoScreen([t("channels.empty")]))
        backup_flow = BackupFlow(
            app.rt,
            app.store,
            gateway,
            channels,
            BackupOptions(),
            prompter=prompter,
            interactive=True,
            echo=prompter.echo,
        )
        await run_steps(prompter, backup_flow.steps())
        ready = backup_flow.ready()
        backup_row, manifest = await begin_backup(
            app.store,
            gateway,
            ready.source,
            ready.directory,
            filters_json=ready.filters_json,
            protected_ack=ready.protected_ack,
        )
        screen = BackupScreen(
            app.store,
            gateway,
            backup_row,
            ready.directory,
            manifest,
            limits=app.rt.config().limits,
        )
        return ("replace", screen)

    return WizardScreen(t("menu.item_backup"), flow)
