"""``tgmirror run [n]``: run a clone again (delta), or let a paused one carry on.

``execute`` is shared with ``tgmirror clone``, so both paths run a clone the same way, in the
foreground of this terminal.
"""

from typing import Annotated

import typer

from tgmirror.cli.errors import run
from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.core.gateway import ChannelInfo, TelegramGateway
from tgmirror.engine.runner import RunControl, Runner
from tgmirror.engine.runs import RunRequest, begin_run, check_runnable, resolve_run
from tgmirror.store.db import Store, utc_now
from tgmirror.store.runs import Control, FilterChange, RunStatus, StartedRun
from tgmirror.ui.messages import t
from tgmirror.ui.progress import LineReporter

EXIT_INTERRUPTED = 130


def run_clone(
    ctx: typer.Context,
    number: Annotated[
        str | None,
        typer.Argument(
            metavar="[RUN]",
            help="Run number from `tgmirror history` (default: the latest run). "
            "The same source and destination are cloned again.",
        ),
    ] = None,
    force_takeover: Annotated[
        bool,
        typer.Option(
            "--force-takeover",
            help="Run even if another process seems to hold this clone (only if it is dead).",
        ),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait",
            help="Sit out a FloodWait of any length instead of saving progress and exiting "
            "(waits longer than [limits] max_auto_wait normally end the run). "
            "Does not apply to the daily cap.",
        ),
    ] = False,
) -> None:
    """Clone the same source and destination again: only what is newer, or where a stop left off.

    Uses the filter and options of that run. If that clone is paused in another terminal, this
    resumes it there instead. Ctrl+C stops it, saving progress; keys: p pause, r resume, q stop.

    A FloodWait up to [limits] max_auto_wait seconds is waited out and the same batch is sent
    again. A longer one ends the run as waiting (exit code 3) unless --wait is given. The daily
    cap ([limits] daily_cap) always ends it until the next midnight.

    To change the filter, use `tgmirror clone` with the same --src/--dst and the new filter.

    Example: tgmirror run        (or: tgmirror run 3)
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with opened_store(rt) as store:
            target = await resolve_run(store, number)
            live = await store.active_run()
            if (
                live is not None
                and live.mirror_id == target.mirror_id
                and live.status is RunStatus.PAUSED
                and not force_takeover
            ):  # paused in another terminal: carry on there, do not start a second one
                await store.set_control(live.id, Control.NONE)
                typer.echo(t("run.resumed_elsewhere", id=live.id))
                return
            if (last := await store.latest_run(target.src_id, target.dst_id)) is not None:
                check_runnable(last, utc_now())  # before connecting: refuse what Telegram refuses
            request = RunRequest(
                mode=target.mode,
                batch_size=target.options.batch_size,
                pushdown=target.options.pushdown,
                force=force_takeover,
            )
            async with authorized(rt) as conn:
                src = ChannelInfo(target.src_id, target.src_title, target.src_kind)
                dst = ChannelInfo(target.dst_id, target.dst_title, target.src_kind)
                started = await begin_run(store, conn.gateway, src, dst, request)
                await execute(rt, store, conn.gateway, started, wait=wait)

    run(rt, command())


async def execute(
    rt: Runtime,
    store: Store,
    gateway: TelegramGateway,
    started: StartedRun,
    *,
    wait: bool = False,
) -> None:
    """Carry out a started run in the foreground; print progress and the result.

    Errors propagate to ``run`` (which prints them). Ctrl+C saves and exits with 130; ``q`` and
    ``tgmirror stop`` end the run as ``stopped`` and exit 0.
    """
    current = started.run
    control = RunControl()
    runner = Runner(
        store,
        gateway,
        rt.config().limits,
        reporter=LineReporter(typer.echo),
        control=control,
        wait=wait,
    )
    typer.echo(
        t(
            "run.start",
            id=current.id,
            src=current.src_title,
            dst=current.dst_title,
            cursor=current.cursor_from,
        )
    )
    if started.filters is FilterChange.CHANGED:
        typer.echo(t("run.filter_changed"))
    elif started.filters is FilterChange.SAME and current.filters_json != "{}":
        typer.echo(t("run.filter_reused"))
    with (
        stop_on_interrupt(control, lambda: typer.echo(t("run.stopping"), err=True)) as interrupt,
        rt.keys(control) as listening,
    ):
        if listening:
            typer.echo(t("run.keys_hint"))
        final = await runner.run(current)
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
    if final.status is RunStatus.STOPPED:
        typer.echo(t("run.continue_hint", id=final.id))
    if interrupt.hit:  # Ctrl+C: saved cleanly, but the clone is not finished
        raise typer.Exit(EXIT_INTERRUPTED)
