"""``tgmirror pause`` / ``tgmirror stop``: ask the clone (or backup, phase 11) running in another
terminal to rest.

They only write ``runs.control``/``backups.control``; the other process reads it between batches,
finishes its batch and holds (pause) or saves and exits (stop)
(docs/04-state-checkpoint.md, "Điều khiển"). No Telegram connection. In the terminal of the clone
or backup itself the keys p/r/q and Ctrl+C do the same.
"""

import typer

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.store.runs import Control
from tgmirror.ui.messages import t


def pause(ctx: typer.Context) -> None:
    """Ask the running clone or backup to pause after its current batch. `tgmirror run`/
    `tgmirror backup` resumes it.

    It holds in place, keeping its terminal, until resumed (key r, or running it again) or stopped.

    Example: tgmirror pause
    """
    _request(ctx.obj, Control.PAUSE)


def stop(ctx: typer.Context) -> None:
    """Ask the running clone or backup to stop after its current batch. Running it again continues
    it later.

    Example: tgmirror stop
    """
    _request(ctx.obj, Control.STOP)


def _request(rt: Runtime, control: Control) -> None:
    async def command() -> None:
        async with opened_store(rt) as store:
            live = await store.active_run()
            if live is not None and await store.set_control(live.id, control):
                typer.echo(t(f"control.{control}_requested", id=live.id))
                return
            live_backup = await store.active_backup()
            if live_backup is not None and await store.set_backup_control(live_backup.id, control):
                typer.echo(t(f"control.{control}_requested", id=live_backup.id))
                return
            typer.echo(t("control.nothing_running"), err=True)
            raise typer.Exit(1)

    run(rt, command())
