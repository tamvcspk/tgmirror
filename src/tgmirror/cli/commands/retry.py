"""``tgmirror retry [n]``: send again the messages a run failed to copy.

A retry is a run of its own (it appears in ``tgmirror history``, can be paused and stopped) that
sends only the messages still ``failed`` from run ``n``. It does not move the source cursor or look
at the filter. It runs in the foreground of this terminal, like ``clone`` and ``run``.
"""

from typing import Annotated

import typer

from tgmirror.cli.commands.run import execute, pair_of
from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.engine.runs import RunRequest, begin_run, check_runnable, resolve_run
from tgmirror.store.db import utc_now
from tgmirror.ui.messages import t


def retry(
    ctx: typer.Context,
    number: Annotated[
        str | None,
        typer.Argument(
            metavar="[RUN]",
            help="Run number from `tgmirror history` (default: the latest run).",
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
            "(see `tgmirror run --help`).",
        ),
    ] = False,
) -> None:
    """Copy again the messages that a run failed to copy (`tgmirror history N` lists them).

    Only those still failing are sent, however old they are. A message deleted from the source
    since is left out for good, and so are types that cannot be copied at all. Messages that
    fail again now belong to the new run: `tgmirror retry` (no number) tries them once more.

    Example: tgmirror retry        (or: tgmirror retry 3)
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with opened_store(rt) as store:
            target = await resolve_run(store, number)
            if await store.count_failed(target.id) == 0:  # nothing to do: no need to connect
                typer.echo(t("retry.nothing", id=target.id))
                return
            if (last := await store.latest_run(target.src_id, target.dst_id)) is not None:
                check_runnable(last, utc_now())  # before connecting: refuse what Telegram refuses
            request = RunRequest(
                mode=target.mode,
                batch_size=target.options.batch_size,
                pushdown=target.options.pushdown,
                force=force_takeover,
                retry_of=target.id,
                caption=target.options.caption,
                caption_text=target.options.caption_text,
                reset_polls=target.options.reset_polls,
                ignore_unsupported=target.options.ignore_unsupported,
                placeholder=target.options.placeholder,
                protected_ack=target.options.protected_ack,
            )
            src, dst = pair_of(target)
            async with authorized(rt) as conn:
                started = await begin_run(store, conn.gateway, src, dst, request)
                await execute(rt, store, conn.gateway, started, wait=wait)

    run(rt, command())
