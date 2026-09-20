"""``tgmirror new``: choose the source, the destination and the filter, then save the job.

Wizard steps 1-3 (source, destination, filter), the preview of step 5 and the "run it now?"
question. The options step (4) arrives with later phases; until then a job uses the defaults.
"""

from typing import Annotated

import typer

from tgmirror.cli import wizard
from tgmirror.cli.commands.run import execute
from tgmirror.cli.errors import UsageProblem, run
from tgmirror.cli.filter_options import (
    AlbumOption,
    ContainsOption,
    ExcludeMediaOption,
    ExcludeRegexOption,
    FilterFileOption,
    HashtagOption,
    MaxSizeOption,
    MediaOption,
    MinSizeOption,
    RegexOption,
    SinceOption,
    UntilOption,
    collect,
)
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.core.gateway import ChannelInfo, TelegramGateway
from tgmirror.engine import preview
from tgmirror.engine.endpoints import (
    NewChannelSpec,
    find_channel,
    materialize,
    plan_endpoints,
)
from tgmirror.engine.jobs import SUPPORTED_MODES, JobExists, ModeUnsupported, NewJob, create_job
from tgmirror.filters.model import FilterSpec
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
    media: MediaOption = None,
    hashtag: HashtagOption = None,
    contains: ContainsOption = None,
    regex: RegexOption = None,
    exclude_regex: ExcludeRegexOption = None,
    exclude_media: ExcludeMediaOption = None,
    since: SinceOption = None,
    until: UntilOption = None,
    min_size: MinSizeOption = None,
    max_size: MaxSizeOption = None,
    album: AlbumOption = None,
    filter_file: FilterFileOption = None,
    pushdown: Annotated[
        bool,
        typer.Option(
            "--pushdown/--no-pushdown",
            help="Let Telegram narrow the reading by filter (default). --no-pushdown reads "
            "everything and filters here: slower, for checking that nothing is missed.",
        ),
    ] = True,
    preview_flag: Annotated[
        bool | None,
        typer.Option(
            "--preview/--no-preview",
            help="Show how many of the first messages match (default: on a terminal, when "
            "a filter is set and --yes is not given).",
        ),
    ] = None,
    run_now: Annotated[
        bool | None,
        typer.Option("--run/--no-run", help="Run the job right after saving it (default: ask)."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Pick a source, a destination (existing, or newly created) and a filter; save the job.

    Without --src/--dst/--dst-new it asks; with them it never prompts.

    The job only runs if you say so (--run, or yes to the question); else `tgmirror run <job>`.

    Example: tgmirror new --src "@my_channel" --dst-new "My channel (copy)" --yes --run

    With a filter: tgmirror new --src "@my_channel" --dst-new "Videos" --media video
    --hashtag "#news" --since 2024-01-01 --yes

    In PowerShell keep the quotes around @names: an unquoted @name is dropped by the shell.
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if dst is not None and dst_new is not None:
            raise UsageProblem("err.conflicting_flags", flags="--dst / --dst-new")
        if mode not in SUPPORTED_MODES:  # before anything is created on the account
            raise ModeUnsupported(mode)
        filters = collect(
            media=media,
            hashtag=hashtag,
            contains=contains,
            regex=regex,
            exclude_regex=exclude_regex,
            exclude_media=exclude_media,
            since=since,
            until=until,
            min_size=min_size,
            max_size=max_size,
            album=album,
            filter_file=filter_file,
        )  # a bad filter is a usage error before anything is asked or written

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

            if isinstance(plan.dst, ChannelInfo):  # fail before previewing or asking anything
                async with opened_store(rt) as store:
                    if (old := await store.find_job_for_pair(plan.src.id, plan.dst.id)) is not None:
                        raise JobExists(old, refilter=filters is not None and not filters.is_empty)

            if filters is None:  # no flags: the wizard asks, but only if it asked for the rest too
                asked = (
                    rt.interactive and not yes and (src is None or (dst is None and not dst_new))
                )
                filters = await wizard.pick_filters(rt.prompter) if asked else FilterSpec()
            await _preview(rt, conn.gateway, source, filters, preview_flag, yes, pushdown)

            if isinstance(plan.dst, NewChannelSpec):
                await _confirm_creation(rt, plan.dst, yes)

            async with opened_store(rt) as store:
                endpoints = await materialize(conn.gateway, plan)
                typer.echo(t("new.src", channel=channel_label(endpoints.src)))
                key = "new.dst_created" if endpoints.created else "new.dst"
                typer.echo(t(key, channel=channel_label(endpoints.dst)))

                options = NewJob(
                    name, mode, batch_size or rt.config().limits.batch_size, filters, pushdown
                )
                job = await create_job(store, conn.gateway, endpoints, options)
                typer.echo(t("new.saved", id=job.id, name=job.name))

                if await _run_now(rt, run_now, yes):
                    await execute(rt, store, conn.gateway, job)
                else:
                    typer.echo(t("new.run_hint", id=job.id))

    run(rt, command())


async def _preview(
    rt: Runtime,
    gateway: TelegramGateway,
    source: ChannelInfo,
    spec: FilterSpec,
    flag: bool | None,
    yes: bool,
    pushdown: bool,
) -> None:
    """Wizard step 5: a sample of what the filter selects, and (on a terminal) a last yes/no.

    Shown when asked for with ``--preview``, or by default on a terminal with a filter and no
    ``--yes``. Declining leaves nothing behind: it runs before any channel is created.
    """
    shown = flag if flag is not None else (rt.interactive and not yes and not spec.is_empty)
    if not shown:
        return
    result = await preview.sample(gateway, source.id, spec, pushdown=pushdown)
    if result.scanned == 0:
        typer.echo(t("new.preview_empty"))
    else:
        typer.echo(t("new.preview", matched=result.matched, scanned=result.scanned))
        for text in result.examples:
            typer.echo(t("new.preview_example", text=text))
    if rt.interactive and not yes and not await rt.prompter.confirm(t("new.confirm_save")):
        typer.echo(t("err.aborted"), err=True)
        raise typer.Exit(1)


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
