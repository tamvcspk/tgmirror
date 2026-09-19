"""``tgmirror new``: choose the source and the destination, then save the job.

Wizard steps 1-2 (source, destination) and the "run it now?" question. Filters and the options
step arrive with phase 3 onwards; until then a job copies everything, in chronological order.
"""

from typing import Annotated

import typer

from tgmirror.cli import wizard
from tgmirror.cli.commands.run import execute
from tgmirror.cli.errors import UsageProblem, run
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.engine.endpoints import (
    NewChannelSpec,
    find_channel,
    materialize,
    plan_endpoints,
)
from tgmirror.engine.jobs import SUPPORTED_MODES, ModeUnsupported, NewJob, create_job
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
    name: Annotated[
        str | None, typer.Option("--name", help='Job name (default: "<source> → <destination>").')
    ] = None,
    mode: Annotated[
        str,
        typer.Option("--mode", help="auto or copy (server-side copy); reupload comes in phase 6."),
    ] = "auto",
    batch_size: Annotated[
        int | None,
        typer.Option(
            "--batch-size",
            min=1,
            max=100,
            help="Messages per copy call (default: batch_size from config.toml, 20).",
        ),
    ] = None,
    run_now: Annotated[
        bool | None,
        typer.Option("--run/--no-run", help="Run the job right after saving it (default: ask)."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Pick a source and a destination (existing, or newly created) and save the job.

    Without --src/--dst/--dst-new it asks; with them it never prompts.

    The job only runs if you say so (--run, or yes to the question); else `tgmirror run <job>`.

    Example: tgmirror new --src "@my_channel" --dst-new "My channel (copy)" --yes --run

    In PowerShell keep the quotes around @names: an unquoted @name is dropped by the shell.
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if dst is not None and dst_new is not None:
            raise UsageProblem("err.conflicting_flags", flags="--dst / --dst-new")
        if mode not in SUPPORTED_MODES:  # before anything is created on the account
            raise ModeUnsupported(mode)

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

            async with opened_store(rt) as store:
                endpoints = await materialize(conn.gateway, plan)
                typer.echo(t("new.src", channel=channel_label(endpoints.src)))
                key = "new.dst_created" if endpoints.created else "new.dst"
                typer.echo(t(key, channel=channel_label(endpoints.dst)))

                options = NewJob(name, mode, batch_size or rt.config().limits.batch_size)
                job = await create_job(store, conn.gateway, endpoints, options)
                typer.echo(t("new.saved", id=job.id, name=job.name))

                if await _run_now(rt, run_now, yes):
                    await execute(rt, store, conn.gateway, job)
                else:
                    typer.echo(t("new.run_hint", id=job.id))

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


async def _run_now(rt: Runtime, flag: bool | None, yes: bool) -> bool:
    """``--run``/``--no-run`` decide; otherwise ask on a terminal, never run unasked."""
    if flag is not None:
        return flag
    if yes or not rt.interactive:
        return False
    return await rt.prompter.confirm(t("new.confirm_run"), default=False)
