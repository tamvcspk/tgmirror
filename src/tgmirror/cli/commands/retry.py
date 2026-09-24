"""``tgmirror retry [n]``: send again the messages a run failed to copy.

A retry is a run of its own (it appears in ``tgmirror history``, can be paused and stopped) that
sends only the messages still ``failed`` from run ``n``. It does not move the source cursor or look
at the filter. It runs in the foreground of this terminal, like ``clone`` and ``run``.

``retry_flow`` is the part before ``execute``, factored out (same reason as ``run.resume_flow``)
so the full-screen menu can drive a retry too.
"""

from typing import Annotated

import typer

from tgmirror.cli.commands.run import ReadyToRun, execute, pair_of
from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.engine.runs import RunRequest, begin_run, check_runnable, resolve_run
from tgmirror.store.db import Store, utc_now
from tgmirror.store.runs import Run
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
            result = await retry_flow(store, number, force_takeover=force_takeover)
            if result is None:
                typer.echo(t("retry.nothing", id=(await resolve_run(store, number)).id))
                return
            async with authorized(rt) as conn:
                started = await begin_run(
                    store, conn.gateway, result.src, result.dst, result.request
                )
                await execute(rt, store, conn.gateway, started, wait=wait)

    run(rt, command())


async def retry_flow(
    store: Store, number: str | None, *, force_takeover: bool = False
) -> ReadyToRun | None:
    """Everything ``tgmirror retry``/the menu's "Thử lại tin lỗi" does before ``execute()``.

    ``None``: nothing to retry (the caller decides how to say so — the CLI path re-resolves the
    run for its message; the menu already has it from picking the target).
    """
    target = await resolve_run(store, number)
    return await retry_flow_for(store, target, force_takeover=force_takeover)


async def retry_flow_for(
    store: Store, target: Run, *, force_takeover: bool = False
) -> ReadyToRun | None:
    """Like ``retry_flow``, but for an already-resolved ``target`` (the menu picks it itself)."""
    if await store.count_failed(target.id) == 0:  # nothing to do: no need to connect
        return None
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
    return ReadyToRun(src, dst, request)
