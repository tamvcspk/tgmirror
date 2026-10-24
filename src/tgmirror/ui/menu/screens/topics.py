""" "Topic của forum": what ``tgmirror topics`` does in place (Phase 20b), chosen from a list and
then asked step by step in the frame. Split and merge hand over to ``OpScreen`` like ``repost``
(they download and upload again, so decision D3 and the backup question apply); deleting a topic
is a ``delete`` operation; renaming, closing and reopening touch no message and end in a line.
"""

from dataclasses import replace
from typing import Literal

from tgmirror.cli.commands.delete import DeleteFlow, DeleteOptions
from tgmirror.cli.commands.topics import (
    MoveFlow,
    MoveOptions,
    TopicDeleteFlow,
    apply_change,
)
from tgmirror.cli.transform_options import resolve_typed
from tgmirror.engine.ops import begin_op_backup
from tgmirror.engine.transform import Transform
from tgmirror.store.ops import Op
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.op_screen import OpScreen
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Choice

Verb = Literal["split", "merge", "delete", "rename", "close", "reopen"]
VERBS: tuple[Verb, ...] = ("split", "merge", "delete", "rename", "close", "reopen")


def topics_screen(app: AppContext) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        verb: Verb = await prompter.select(
            t("menu.topics_pick"), [Choice(t(f"menu.topics_{v}"), v) for v in VERBS]
        )
        gateway = app.gateway
        channels = await gateway.list_channels()
        said: list[str] = []

        def echo(line: str) -> None:
            said.append(line)
            prompter.echo(line)

        rt = replace(app.rt, prompter=prompter, interactive=True)
        if verb in ("rename", "close", "reopen"):
            title = None
            if verb == "rename":
                title = (await prompter.text(t("topics.pick_new_title"))).strip()
            line = await apply_change(
                rt,
                app.store,
                gateway,
                channels,
                None,
                None,
                yes=False,
                title=title,
                closed=None if verb == "rename" else verb == "close",
            )
            return ("replace", InfoScreen([line]))

        flow_: DeleteFlow
        if verb == "delete":
            flow_ = TopicDeleteFlow(
                rt,
                app.store,
                gateway,
                channels,
                DeleteOptions(all_messages=True),
                prompter=prompter,
                interactive=True,
                echo=echo,
            )
        else:
            transform = Transform()
            if verb == "split":
                given = await prompter.path(t("menu.ask_transform"))
                transform = await resolve_typed(given)
            flow_ = MoveFlow(
                rt,
                app.store,
                gateway,
                channels,
                MoveOptions(),
                how=verb,
                transform=transform,
                prompter=prompter,
                interactive=True,
                echo=echo,
            )
        op = await flow_.planned()
        if op is None:
            return ("replace", InfoScreen(said or [t("topics.declined")]))
        await flow_.confirmed(op)
        return ("replace", await _screen_for(app, flow_, op))

    return WizardScreen(t("menu.item_topics"), flow)


async def _screen_for(app: AppContext, flow: DeleteFlow, op: Op) -> OpScreen:
    """The screen that works on ``op``: with decision D3 settled and the safety-net backup begun,
    as the CLI does between the confirmation and the first change."""
    ack = await flow.acknowledged(op) if op.kind.posts_again else False
    backup = await flow.backup_plan(op)
    started = (
        None
        if backup is None
        else await begin_op_backup(app.store, app.gateway, op, backup.directory, ack=backup.ack)
    )
    return OpScreen(
        app.store,
        app.gateway,
        op,
        limits=app.rt.config().limits,
        backup=started,
        ack=ack,
        tmp_dir=app.rt.paths.tmp_dir,
    )
