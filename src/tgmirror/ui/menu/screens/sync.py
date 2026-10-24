""" "Đồng bộ thay đổi của nguồn": the ``tgmirror sync-changes`` wizard in the frame (Phase 17), then
the progress screen. Which pair (when the history has several), the two plans, the one count typed
back and the question about backing up the copies that would be deleted are the CLI's
(``SyncFlow``); the screen then does the backup, the edits and the deletes in that order, and the
deletes only if the edits finished.
"""

from dataclasses import replace

from tgmirror.cli.commands.delete import DeleteOptions
from tgmirror.cli.commands.sync import SyncFlow, SyncRequest, pick_run
from tgmirror.engine.ops import begin_op_backup
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.op_screen import OpScreen
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t


def sync_screen(app: AppContext) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        gateway = app.gateway
        channels = await gateway.list_channels()
        said: list[str] = []

        def echo(line: str) -> None:
            said.append(line)
            prompter.echo(line)

        rt = replace(app.rt, prompter=prompter, interactive=True)
        target = await pick_run(rt, app.store, channels, None)
        sync_flow = SyncFlow(
            rt,
            app.store,
            gateway,
            channels,
            DeleteOptions(),
            prompter=prompter,
            interactive=True,
            echo=echo,
        )
        plan = await sync_flow.planned_sync(target, SyncRequest())
        if plan is None:
            return ("replace", InfoScreen(said))
        await sync_flow.confirmed_sync(plan)
        backup = await sync_flow.backup_plan(plan.delete) if plan.delete is not None else None
        started = (
            None
            if backup is None or plan.delete is None
            else await begin_op_backup(
                app.store, gateway, plan.delete, backup.directory, ack=backup.ack
            )
        )
        first, *rest = plan.ops
        screen = OpScreen(
            app.store,
            gateway,
            first,
            limits=app.rt.config().limits,
            backup=started,
            then=rest,
            backup_of=plan.delete,
        )
        return ("replace", screen)

    return WizardScreen(t("menu.item_sync"), flow)
