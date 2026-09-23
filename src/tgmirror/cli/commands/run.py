"""``tgmirror run [n]``: run a clone again (delta), or let a paused one carry on.

``execute`` is shared with ``tgmirror clone``, so both paths run a clone the same way, in the
foreground of this terminal.
"""

from typing import Annotated

import typer

from tgmirror.cli.errors import UsageProblem, run
from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.core.gateway import ChannelInfo, TelegramGateway
from tgmirror.engine.runner import RunControl, Runner
from tgmirror.engine.runs import RunRequest, begin_run, check_runnable, resolve_run
from tgmirror.store.db import Store, utc_now
from tgmirror.store.runs import Control, FilterChange, Run, RunStatus, StartedRun
from tgmirror.ui.messages import t
from tgmirror.ui.tables import channel_label
from tgmirror.ui.tui import TuiReporter

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
    fresh: Annotated[
        bool,
        typer.Option(
            "--fresh",
            help="Forget what this pair has copied and copy everything again from the "
            "start (the destination may get duplicates unless you emptied it). Asks first "
            "when there is something to forget; --yes agrees.",
        ),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="With --fresh: do not ask for confirmation.")
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
    --fresh starts the pair over instead (see `tgmirror clone --help`).

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
                and not fresh  # --fresh must not be swallowed by a resume: it ends in RunBusy
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
                fresh=fresh,
                caption=target.options.caption,
                caption_text=target.options.caption_text,
                reset_polls=target.options.reset_polls,
                ignore_unsupported=target.options.ignore_unsupported,
                placeholder=target.options.placeholder,
                protected_ack=target.options.protected_ack,
            )
            src, dst = pair_of(target)
            if fresh:
                copied = await store.count_copied(src.id, dst.id)
                await confirm_fresh(rt, copied, yes, channel_label(src), channel_label(dst))
            async with authorized(rt) as conn:
                started = await begin_run(store, conn.gateway, src, dst, request)
                await execute(rt, store, conn.gateway, started, wait=wait)

    run(rt, command())


def pair_of(earlier: Run) -> tuple[ChannelInfo, ChannelInfo]:
    """Source and destination of an earlier run, as ``begin_run`` takes them (a destination has
    the kind of its source: that is how the pair was chosen)."""
    return (
        ChannelInfo(earlier.src_id, earlier.src_title, earlier.src_kind),
        ChannelInfo(earlier.dst_id, earlier.dst_title, earlier.src_kind),
    )


async def confirm_fresh(rt: Runtime, copied: int, yes: bool, src: str, dst: str) -> None:
    """The question before a fresh start forgets ``copied`` messages (none: nothing to ask).

    ``--yes`` agrees; with no terminal and no ``--yes`` it is a usage error, so a script never
    starts a second copy of a destination by accident.
    """
    if copied == 0 or yes:
        return
    if not rt.interactive:
        raise UsageProblem("err.fresh_needs_yes", count=copied)
    if not await rt.prompter.confirm(t("clone.confirm_fresh", count=copied, src=src, dst=dst)):
        typer.echo(t("err.aborted"), err=True)
        raise typer.Exit(1)


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
    limits = rt.config().limits
    if current.options.protected_ack:  # decision D3: the user answers for this copy
        typer.echo(t("warn.responsibility"), err=True)
    retry_of = current.options.retry_of
    if retry_of is not None:  # no source cursor or filter to talk about
        count = await store.count_failed(retry_of)
        typer.echo(
            t(
                "run.retry_start",
                id=current.id,
                of=retry_of,
                count=count,
                src=current.src_title,
                dst=current.dst_title,
            )
        )
    else:
        typer.echo(
            t(
                "run.start",
                id=current.id,
                src=current.src_title,
                dst=current.dst_title,
                cursor=current.cursor_from,
            )
        )
        if started.forgot is not None:
            typer.echo(t("run.fresh_started", count=started.forgot))
        elif started.filters is FilterChange.CHANGED:
            typer.echo(t("run.filter_changed"))
        if started.filters is FilterChange.SAME and current.filters_json != "{}":
            typer.echo(t("run.filter_reused"))
    with (
        rt.reporter(limits, current.src_title, current.dst_title) as reporter,
        stop_on_interrupt(control, lambda: typer.echo(t("run.stopping"), err=True)) as interrupt,
        rt.keys(control) as listening,
    ):
        runner = Runner(
            store, gateway, limits, reporter=reporter, control=control, wait=wait,
            tmp_dir=rt.paths.tmp_dir,
        )  # fmt: skip
        if listening and not isinstance(reporter, TuiReporter):  # the TUI shows the keys itself
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
    if final.skipped_unsupported:
        typer.echo(t("run.unsupported_total", count=final.skipped_unsupported, id=final.id))
    if final.gone:
        typer.echo(t("retry.gone", count=final.gone))
    if final.failed:
        key = "retry.still_failing" if retry_of is not None else "run.retry_hint"
        typer.echo(t(key, count=final.failed, id=final.id))
    if final.status is RunStatus.STOPPED:
        if retry_of is not None:  # `run` would start a delta: the messages left are retry's job
            typer.echo(t("run.retry_continue_hint", of=retry_of))
        else:
            typer.echo(t("run.continue_hint", id=final.id))
    if interrupt.hit:  # Ctrl+C: saved cleanly, but the clone is not finished
        raise typer.Exit(EXIT_INTERRUPTED)
