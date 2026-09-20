"""``tgmirror pause`` / ``tgmirror stop``: ask the clone running in another terminal to rest.

They only write ``runs.control``; the runner (another process) reads it between batches, finishes
its batch and holds (pause) or saves and exits (stop) (docs/04-state-checkpoint.md, "Điều khiển").
No Telegram connection. In the terminal of the clone itself the keys p/r/q and Ctrl+C do the same.
"""

import typer

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.store.runs import Control
from tgmirror.ui.messages import t


def pause(ctx: typer.Context) -> None:
    """Ask the running clone to pause after its current batch. `tgmirror run` resumes it.

    It holds in place, keeping its terminal, until resumed (key r, or `tgmirror run`) or stopped.

    Example: tgmirror pause
    """
    _request(ctx.obj, Control.PAUSE)


def stop(ctx: typer.Context) -> None:
    """Ask the running clone to stop after its current batch. `tgmirror run` continues it later.

    Example: tgmirror stop
    """
    _request(ctx.obj, Control.STOP)


def _request(rt: Runtime, control: Control) -> None:
    async def command() -> None:
        async with opened_store(rt) as store:
            live = await store.active_run()
            if live is None or not await store.set_control(live.id, control):
                typer.echo(t("control.nothing_running"), err=True)
                raise typer.Exit(1)
            typer.echo(t(f"control.{control}_requested", id=live.id))

    run(rt, command())
