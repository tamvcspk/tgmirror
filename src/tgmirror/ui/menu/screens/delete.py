""" "Xoá tin": the ``tgmirror delete`` wizard, drawn in the frame, then the progress screen
(Phase 20.2). The same ``DeleteFlow`` (``cli/commands/delete.py``) the CLI runs with no flags given
— plan, the number typed back, the question about a backup — through a ``MenuPrompter`` where Esc
goes back one question. Nothing is deleted before the number is typed; the "Khám phá" screen hands
over a chat and a filter (``d``), so those two questions are not asked again.
"""

from tgmirror.cli.commands.delete import DeleteFlow, DeleteOptions
from tgmirror.engine.ops import begin_op_backup
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.op_screen import OpScreen
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t


def delete_screen(app: AppContext, options: DeleteOptions | None = None) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        gateway = app.gateway
        channels = await gateway.list_channels()
        said: list[str] = []

        def echo(line: str) -> None:
            said.append(line)
            prompter.echo(line)

        delete_flow = DeleteFlow(
            app.rt,
            app.store,
            gateway,
            channels,
            options or DeleteOptions(),
            prompter=prompter,
            interactive=True,
            echo=echo,
        )
        op = await delete_flow.planned()
        if op is None:  # the filter selected nothing: say so where it can be read
            return ("replace", InfoScreen(said))
        await delete_flow.confirmed(op)
        backup = await delete_flow.backup_plan(op)
        started = (
            None
            if backup is None
            else await begin_op_backup(app.store, gateway, op, backup.directory, ack=backup.ack)
        )
        screen = OpScreen(app.store, gateway, op, limits=app.rt.config().limits, backup=started)
        return ("replace", screen)

    return WizardScreen(t("menu.item_delete"), flow)
