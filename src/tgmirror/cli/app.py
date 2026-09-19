"""Typer entry point for the ``tgmirror`` command. Commands are added phase by phase."""

import sys
from dataclasses import replace
from typing import Annotated

import typer

from tgmirror import __version__
from tgmirror.cli.commands import auth, channels, control, new, run
from tgmirror.cli.runtime import Runtime, default_runtime

app = typer.Typer(
    name="tgmirror",
    help="Clone a Telegram channel you joined into another channel.",
    no_args_is_help=True,
    add_completion=False,
)


def _use_utf8_output() -> None:
    """Messages are Vietnamese by default and titles can be any script, but Python writes to a
    redirected stdout in the locale code page on Windows (cp1252): Rich then crashes and
    ``typer.echo`` prints garbage. A real console is unaffected. ``replace`` keeps a stream that
    still cannot take a character from raising."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"tgmirror {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_show_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
    debug: Annotated[
        bool, typer.Option("--debug", help="Show tracebacks instead of one-line errors.")
    ] = False,
) -> None:
    # Tests pass their own Runtime through ``obj``; otherwise talk to the real Telegram.
    if isinstance(ctx.obj, Runtime):
        rt = ctx.obj
    else:
        _use_utf8_output()
        rt = default_runtime()
    ctx.obj = replace(rt, debug=rt.debug or debug)


app.command("login")(auth.login)
app.command("logout")(auth.logout)
app.command("whoami")(auth.whoami)
app.command("channels")(channels.channels)
app.command("new")(new.new)
app.command("run")(run.run_job)
app.command("pause")(control.pause)
app.command("stop")(control.stop)
