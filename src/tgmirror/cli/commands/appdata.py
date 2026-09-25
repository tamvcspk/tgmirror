"""``tgmirror appdata export|import``: move tgmirror's local state to another machine, without any
secret (docs/06-lo-trinh.md, Phase 10). No Telegram connection for ``import``; ``export`` only opens
the store to check no run is live and to snapshot it. Not in the full-screen menu yet.
"""

from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli.errors import Declined, UsageProblem, run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.store import appdata
from tgmirror.ui.messages import t

appdata_app = typer.Typer(help="Export/import tgmirror's local state (no secrets).")


@appdata_app.command("export")
def export_(
    ctx: typer.Context,
    dest: Annotated[Path, typer.Argument(help="Zip file to write.")],
) -> None:
    """Snapshot tgmirror.db and config.toml (api_id/api_hash stripped) into a zip. Refuses while a
    run is live (stop it first): a mid-run snapshot run again elsewhere would copy twice.

    Example: tgmirror appdata export tgmirror-backup.zip
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with opened_store(rt) as store:
            manifest = await appdata.export_appdata(store, rt.paths, dest)
        typer.echo(t("appdata.export_done", path=dest, files=len(manifest.files)))

    run(rt, command())


@appdata_app.command("import")
def import_(
    ctx: typer.Context,
    src: Annotated[Path, typer.Argument(help="Zip file made by `tgmirror appdata export`.")],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask before moving existing data aside.")
    ] = False,
) -> None:
    """Restore tgmirror.db and config.toml from a zip made by `export`. Any data this machine
    already has is moved aside, never deleted. Log in again afterwards.

    Example: tgmirror appdata import tgmirror-backup.zip
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        manifest = appdata.verify_archive(src)
        if appdata.existing_data(rt.paths) and not yes:
            if not rt.interactive:
                raise UsageProblem("err.appdata_import_needs_yes")
            if not await rt.prompter.confirm(t("appdata.confirm_overwrite")):
                raise Declined
        backed_up = appdata.apply_import(rt.paths, src, manifest)
        for path in backed_up:
            typer.echo(t("appdata.backed_up", path=path))
        typer.echo(t("appdata.import_done", files=len(manifest.files)))
        typer.echo(t("appdata.login_reminder"))

    run(rt, command())
