""" "Sửa tin": the ``tgmirror edit`` wizard in the frame (Phase 20.3), then the progress screen.
The transform file is asked for by path; the plan with its before/after diff, the count typed back
and the screen that works are the CLI's (``EditFlow`` and ``OpScreen``). There is no backup
question: the originals are in the plan and ``tgmirror undo`` puts them back.
"""

from dataclasses import replace

from tgmirror.cli.commands.delete import DeleteOptions
from tgmirror.cli.commands.edit import EditFlow, unsupported_in_edit
from tgmirror.cli.errors import UsageProblem
from tgmirror.cli.transform_options import resolve_typed
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.op_screen import OpScreen
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t


def edit_screen(app: AppContext, options: DeleteOptions | None = None) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        gateway = app.gateway
        channels = await gateway.list_channels()
        said: list[str] = []

        def echo(line: str) -> None:
            said.append(line)
            prompter.echo(line)

        given = await prompter.path(t("menu.ask_transform"))
        transform = await resolve_typed(given)
        if unsupported := unsupported_in_edit(transform):
            raise UsageProblem("edit.unsupported", what=unsupported)
        if not transform.caption:
            raise UsageProblem("edit.needs_transform")
        rt = replace(app.rt, prompter=prompter, interactive=True)
        edit_flow = EditFlow(
            rt,
            app.store,
            gateway,
            channels,
            replace(options or DeleteOptions(), no_backup=True),
            transform=transform,
            prompter=prompter,
            interactive=True,
            echo=echo,
        )
        op = await edit_flow.planned()
        if op is None:
            return ("replace", InfoScreen(said))
        await edit_flow.confirmed(op)
        return ("replace", OpScreen(app.store, gateway, op, limits=app.rt.config().limits))

    return WizardScreen(t("menu.item_edit"), flow)
