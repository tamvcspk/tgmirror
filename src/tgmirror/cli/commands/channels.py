"""``tgmirror channels``: what the account has joined."""

from typing import Annotated

import typer
from rich.console import Console

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, authorized
from tgmirror.core.gateway import ChannelInfo
from tgmirror.ui.tables import channels_json, print_channels


def _matches(channel: ChannelInfo, needle: str) -> bool:
    needle = needle.casefold()
    return needle in channel.title.casefold() or needle in (channel.username or "").casefold()


def channels(
    ctx: typer.Context,
    search: Annotated[
        str | None,
        typer.Option("--search", "-s", help="Only titles or @usernames containing this."),
    ] = None,
    writable: Annotated[
        bool, typer.Option("--writable", help="Only chats where you are an admin who can post.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List the channels, groups and forums you have joined.

    Example: tgmirror channels --search news --writable
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with authorized(rt) as conn:
            found = await conn.gateway.list_channels()
        if search:
            found = [c for c in found if _matches(c, search)]
        if writable:
            found = [c for c in found if c.is_admin and c.can_post]
        if as_json:
            typer.echo(channels_json(found))
        else:
            print_channels(Console(), found)

    run(rt, command())
