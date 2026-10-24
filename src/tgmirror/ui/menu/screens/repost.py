""" "Đăng lại tin": the ``tgmirror repost`` wizard in the frame (Phase 20.4), then the progress
screen. It first asks whether the originals are to be deleted once their copies exist (that is
what makes the safety-net backup worth asking about); the plan, the count typed back, decision D3
for a chat that restricts saving content and the backup question are the CLI's (``RepostFlow``).
"""

from dataclasses import replace

from tgmirror.cli.commands.repost import RepostFlow, RepostOptions
from tgmirror.engine.ops import begin_op_backup
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.op_screen import OpScreen
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t


def repost_screen(app: AppContext, options: RepostOptions | None = None) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        gateway = app.gateway
        channels = await gateway.list_channels()
        said: list[str] = []

        def echo(line: str) -> None:
            said.append(line)
            prompter.echo(line)

        chosen = options or RepostOptions()
        if options is None:
            chosen = replace(
                chosen, delete_original=await prompter.confirm(t("menu.ask_delete_original"), False)
            )
        repost_flow = RepostFlow(
            replace(app.rt, prompter=prompter, interactive=True),
            app.store,
            gateway,
            channels,
            chosen,
            prompter=prompter,
            interactive=True,
            echo=echo,
        )
        op = await repost_flow.planned()
        if op is None:
            return ("replace", InfoScreen(said))
        await repost_flow.confirmed(op)
        ack = await repost_flow.acknowledged(op)
        backup = await repost_flow.backup_plan(op)
        started = (
            None
            if backup is None
            else await begin_op_backup(app.store, gateway, op, backup.directory, ack=backup.ack)
        )
        screen = OpScreen(
            app.store,
            gateway,
            op,
            limits=app.rt.config().limits,
            backup=started,
            ack=ack,
            tmp_dir=app.rt.paths.tmp_dir,
        )
        return ("replace", screen)

    return WizardScreen(t("menu.item_repost"), flow)
