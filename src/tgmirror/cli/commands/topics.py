"""``tgmirror topics``: a forum's topics, for ``--topic``/``tgmirror clone``'s wizard (phase 8).

Rule 1's exception: a one-shot read the user started, like ``channels``, not part of a run.
"""

from typing import Annotated

import typer
from rich.console import Console

from tgmirror.cli.errors import UsageProblem, run
from tgmirror.cli.runtime import Runtime, authorized
from tgmirror.core.gateway import ChatKind
from tgmirror.engine.endpoints import find_channel
from tgmirror.ui.tables import print_topics, topics_json


def topics(
    ctx: typer.Context,
    src: Annotated[
        str, typer.Argument(help='Forum: "@username" (quoted in PowerShell), id or exact title.')
    ],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List a forum's topics (id, title, closed).

    Example: tgmirror topics "@my_forum"
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            channel = find_channel(channels, src)
            if channel.kind is not ChatKind.FORUM:
                raise UsageProblem("err.not_a_forum", title=channel.title)
            found = await conn.gateway.list_topics(channel.id)
        if as_json:
            typer.echo(topics_json(found))
        else:
            print_topics(Console(), found)

    run(rt, command())
