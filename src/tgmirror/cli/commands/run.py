"""``tgmirror run <job>``: run or continue a job in the foreground.

``execute`` is shared with ``tgmirror new --run``, so both paths run a job the same way.
"""

from typing import Annotated

import typer

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
from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.core.gateway import TelegramGateway
from tgmirror.engine.jobs import check_runnable, resolve_job
from tgmirror.engine.runner import Runner, StopSignal
from tgmirror.store.db import Store, utc_now
from tgmirror.store.jobs import Job
from tgmirror.ui.messages import t
from tgmirror.ui.progress import LineReporter

EXIT_INTERRUPTED = 130


def run_job(
    ctx: typer.Context,
    job: Annotated[str, typer.Argument(help="Job id or exact name.")],
    force_takeover: Annotated[
        bool,
        typer.Option(
            "--force-takeover",
            help="Run even if another process seems to hold the job (only if it is dead).",
        ),
    ] = False,
    refilter: Annotated[
        bool,
        typer.Option(
            "--refilter",
            help="Replace the job's filter with the one given by the filter flags or "
            "--filter-file and scan the source again from the start; messages already "
            "copied are skipped.",
        ),
    ] = False,
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
) -> None:
    """Run a job, or continue it after a pause, stop, crash or flood wait.

    Ctrl+C finishes the current batch, saves and exits; a second Ctrl+C exits at once.
    From another terminal, `tgmirror pause <job>` / `tgmirror stop <job>` do the same.

    Example: tgmirror run 3

    To change what a job copies: tgmirror run 3 --refilter --media video --since 2024-01-01.
    Messages that now match are appended at the end of the destination.
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
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
        )
        if refilter and filters is None:
            raise UsageProblem("err.refilter_needs_filter")
        if filters is not None and not refilter:
            raise UsageProblem("err.filter_needs_refilter")

        async with opened_store(rt) as store:
            found = await resolve_job(store, job)
            check_runnable(found, utc_now())  # before connecting: refuse what Telegram will refuse
            async with authorized(rt) as conn:
                if filters is not None:
                    found = await store.replace_filters(found.id, filters.to_json())
                    typer.echo(t("run.refiltered", id=found.id))
                await execute(rt, store, conn.gateway, found, force_takeover=force_takeover)

    run(rt, command())


async def execute(
    rt: Runtime,
    store: Store,
    gateway: TelegramGateway,
    job: Job,
    *,
    force_takeover: bool = False,
) -> None:
    """Run ``job`` until it rests; print progress and the result. Errors propagate to ``run``."""
    stop = StopSignal()
    runner = Runner(
        store, gateway, rt.config().limits, reporter=LineReporter(typer.echo), stop=stop
    )
    typer.echo(t("run.start", id=job.id, name=job.name, cursor=job.cursor_src_id))
    with stop_on_interrupt(stop, lambda: typer.echo(t("run.stopping"), err=True)):
        final = await runner.run(job.id, force_takeover=force_takeover)
    typer.echo(
        t(
            "run.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            failed=final.failed,
        )
    )
    if final.skipped_filter:
        typer.echo(t("run.skipped", count=final.skipped_filter))
    if stop.requested:  # Ctrl+C: saved cleanly, but the job is not finished
        raise typer.Exit(EXIT_INTERRUPTED)
