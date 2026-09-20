"""``tgmirror clone``: choose the source, the destination and the filter, then copy, right now.

Wizard steps 1-3 (source, destination, filter), the preview of step 5 and one confirmation, then
the clone runs in the foreground like any other command: no background process, no schedule, and
Ctrl+C ends it with the progress saved. Cloning the same pair again copies only what is newer
(delta) with the filter of the previous run; the options step (4) arrives with later phases.
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
    Plan,
    find_channel,
    materialize,
    plan_endpoints,
)
from tgmirror.engine.runs import (
    SUPPORTED_MODES,
    ModeUnsupported,
    RunRequest,
    begin_run,
    check_runnable,
)
from tgmirror.filters.model import FilterSpec
from tgmirror.store.db import utc_now
from tgmirror.ui.messages import t
from tgmirror.ui.tables import channel_label


def clone(
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
    no_filter: Annotated[
        bool,
        typer.Option(
            "--no-filter",
            help="Drop the filter remembered from an earlier clone of this pair and copy "
            "everything (the source is read again from the start; nothing is copied twice).",
        ),
    ] = False,
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
    wait: Annotated[
        bool,
        typer.Option(
            "--wait",
            help="Sit out a FloodWait of any length instead of saving progress and exiting "
            "(waits longer than [limits] max_auto_wait normally end the run). "
            "Does not apply to the daily cap.",
        ),
    ] = False,
    force_takeover: Annotated[
        bool,
        typer.Option(
            "--force-takeover",
            help="Run even if another process seems to hold this clone (only if it is dead).",
        ),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Clone a source into a destination (existing, or newly created) and start copying now.

    Without --src/--dst/--dst-new it asks; with them it never prompts (only a new channel needs
    --yes when there is no terminal). Ctrl+C stops it, saving progress; `tgmirror run` continues.
    Cloning the same pair again copies only what is newer, with the same filter unless you give
    another one (then the source is read again from the start; nothing is copied twice).

    Keys while it runs: p pause, r resume, q stop.

    Example: tgmirror clone --src "@my_channel" --dst-new "My channel (copy)" --yes

    With a filter: tgmirror clone --src "@my_channel" --dst-new "Videos" --media video
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
        if no_filter:
            if filters is not None:
                raise UsageProblem("err.conflicting_flags", flags="--no-filter / filter flags")
            filters = FilterSpec()

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

            seen_before = False
            async with opened_store(rt) as store:
                if isinstance(plan.dst, ChannelInfo):  # fail before previewing or asking anything
                    if (last := await store.latest_run(plan.src.id, plan.dst.id)) is not None:
                        check_runnable(last, utc_now())
                    seen_before = await store.find_mirror(plan.src.id, plan.dst.id) is not None

                if filters is None:  # no flags: the wizard asks, but only if it asked for the rest
                    asked = (
                        rt.interactive
                        and not yes
                        and (src is None or (dst is None and not dst_new))
                    )
                    if asked:
                        filters = await wizard.pick_filters(rt.prompter, can_keep=seen_before)
                if filters is not None:
                    await _preview(rt, conn.gateway, source, filters, preview_flag, yes, pushdown)
                await _confirm_start(rt, plan, yes)

                endpoints = await materialize(conn.gateway, plan)
                typer.echo(t("clone.src", channel=channel_label(endpoints.src)))
                key = "clone.dst_created" if endpoints.created else "clone.dst"
                typer.echo(t(key, channel=channel_label(endpoints.dst)))

                request = RunRequest(
                    mode=mode,
                    batch_size=batch_size or rt.config().limits.batch_size,
                    pushdown=pushdown,
                    filters_json=None if filters is None else filters.to_json(),
                    force=force_takeover,
                )
                started = await begin_run(
                    store, conn.gateway, endpoints.src, endpoints.dst, request
                )
                await execute(rt, store, conn.gateway, started, wait=wait)

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
    """Wizard step 5: a sample of what the filter selects.

    Shown when asked for with ``--preview``, or by default on a terminal with a filter and no
    ``--yes``. It runs before anything is created, so declining the confirmation that follows
    leaves nothing behind.
    """
    shown = flag if flag is not None else (rt.interactive and not yes and not spec.is_empty)
    if not shown:
        return
    result = await preview.sample(gateway, source.id, spec, pushdown=pushdown)
    if result.scanned == 0:
        typer.echo(t("clone.preview_empty"))
    else:
        typer.echo(t("clone.preview", matched=result.matched, scanned=result.scanned))
        for text in result.examples:
            typer.echo(t("clone.preview_example", text=text))


async def _confirm_start(rt: Runtime, plan: Plan, yes: bool) -> None:
    """The one question before copying starts. ``--yes`` skips it; with no terminal it is not
    asked, except that creating a channel (a write on the account) then needs ``--yes``."""
    new_channel = isinstance(plan.dst, NewChannelSpec)
    if yes:
        return
    if not rt.interactive:
        if new_channel:
            raise UsageProblem("err.needs_yes", flag="--yes")
        return
    target = (
        t("clone.dst_will_be_created", title=plan.dst.title)
        if isinstance(plan.dst, NewChannelSpec)
        else channel_label(plan.dst)
    )
    question = t("clone.confirm_start", src=channel_label(plan.src), dst=target)
    if not await rt.prompter.confirm(question):
        typer.echo(t("err.aborted"), err=True)
        raise typer.Exit(1)
