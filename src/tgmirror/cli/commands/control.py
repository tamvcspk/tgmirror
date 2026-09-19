"""``tgmirror pause <job>`` / ``tgmirror stop <job>``: ask a running job to rest.

They only write ``jobs.control``; the runner (another process) reads it between batches, finishes
its batch, saves and exits (docs/04-state-checkpoint.md, "Điều khiển"). No Telegram connection.
"""

from typing import Annotated

import typer

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.engine.jobs import resolve_job
from tgmirror.store.jobs import Control
from tgmirror.ui.messages import t

_JOB = Annotated[str, typer.Argument(help="Job id or exact name.")]


def pause(ctx: typer.Context, job: _JOB) -> None:
    """Ask a running job to pause after its current batch. Continue it with `tgmirror run`.

    Example: tgmirror pause 3
    """
    _request(ctx.obj, job, Control.PAUSE)


def stop(ctx: typer.Context, job: _JOB) -> None:
    """Ask a running job to stop after its current batch. Continue it with `tgmirror run`.

    Example: tgmirror stop 3
    """
    _request(ctx.obj, job, Control.STOP)


def _request(rt: Runtime, ref: str, control: Control) -> None:
    async def command() -> None:
        async with opened_store(rt) as store:
            job = await resolve_job(store, ref)
            if not await store.set_control(job.id, control):
                typer.echo(
                    t("control.not_running", id=job.id, status=t(f"status.{job.status}")), err=True
                )
                raise typer.Exit(1)
            typer.echo(t(f"control.{control}_requested", id=job.id))

    run(rt, command())
