"""``tgmirror new``: choose the source and the destination (wizard steps 1-2).

Phase 1 stops after the two ends are settled and, if asked, the destination channel is created:
jobs, filters and running the clone need the SQLite store and the engine (phase 2 onwards).
"""

from typing import Annotated

import typer

from tgmirror.cli import wizard
from tgmirror.cli.errors import UsageProblem, run
from tgmirror.cli.runtime import Runtime, authorized
from tgmirror.engine.endpoints import (
    NewChannelSpec,
    find_channel,
    materialize,
    plan_endpoints,
)
from tgmirror.ui.messages import t
from tgmirror.ui.tables import channel_label


def new(
    ctx: typer.Context,
    src: Annotated[
        str | None,
        typer.Option(
            "--src", help='Source: "@username" (quoted in PowerShell), id (-100...) or exact title.'
        ),
    ] = None,
    dst: Annotated[
        str | None, typer.Option("--dst", help="Existing destination (same kind as the source).")
    ] = None,
    dst_new: Annotated[
        str | None,
        typer.Option("--dst-new", help="Create a new destination channel with this title."),
    ] = None,
    about: Annotated[str, typer.Option("--about", help="Description of the new channel.")] = "",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Pick a source and a destination (existing, or newly created).

    Without --src/--dst/--dst-new it asks; with them it never prompts.

    Example: tgmirror new --src "@my_channel" --dst-new "My channel (copy)" --yes

    In PowerShell keep the quotes around @names: an unquoted @name is dropped by the shell.
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if dst is not None and dst_new is not None:
            raise UsageProblem("err.conflicting_flags", flags="--dst / --dst-new")

        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            if not channels:
                typer.echo(t("channels.empty"))
                raise typer.Exit(1)

            if src is not None:
                source = find_channel(channels, src)
            elif rt.interactive:
                source = await wizard.pick_source(rt.prompter, channels)
            else:
                raise UsageProblem("err.missing_flag", flag="--src")

            if dst is not None:
                destination = find_channel(channels, dst)
            elif dst_new is not None:
                destination = NewChannelSpec(dst_new, about)
            elif rt.interactive:
                destination = await wizard.pick_destination(rt.prompter, source, channels)
            else:
                raise UsageProblem("err.missing_flag", flag="--dst or --dst-new")

            plan = plan_endpoints(source, destination)  # every refusal happens before any write
            for code in plan.warnings:
                typer.echo(t(f"warn.{code}"), err=True)

            if isinstance(plan.dst, NewChannelSpec):
                await _confirm_creation(rt, plan.dst, yes)

            endpoints = await materialize(conn.gateway, plan)

        typer.echo(t("new.src", channel=channel_label(endpoints.src)))
        key = "new.dst_created" if endpoints.created else "new.dst"
        typer.echo(t(key, channel=channel_label(endpoints.dst)))
        typer.echo(t("new.not_saved"))

    run(rt, command())


async def _confirm_creation(rt: Runtime, spec: NewChannelSpec, yes: bool) -> None:
    """Creating a channel is a write on the user's account: confirm unless ``--yes``."""
    if yes:
        return
    if not rt.interactive:
        raise UsageProblem("err.needs_yes", flag="--yes")
    if not await rt.prompter.confirm(t("new.confirm_create", title=spec.title)):
        typer.echo(t("err.aborted"), err=True)
        raise typer.Exit(1)
