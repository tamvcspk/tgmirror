"""Typer entry point for the ``tgmirror`` command. Commands are added phase by phase."""

import logging
import sys
from dataclasses import replace
from typing import Annotated

import typer

from tgmirror import __version__
from tgmirror.cli.commands import auth, channels, clone, control, history, retry, run, status
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
    _show_warnings()
    ctx.obj = replace(rt, debug=rt.debug or debug)


class _Warnings(logging.Handler):
    """Prints a warning to the *current* stderr (so it follows a redirect or a re-encoded stream),
    and never fails on a console that cannot show Vietnamese: it falls back to ASCII."""

    def emit(self, record: logging.LogRecord) -> None:
        line = f"[cảnh báo] {record.getMessage()}"
        try:
            print(line, file=sys.stderr, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), file=sys.stderr, flush=True)


def _show_warnings() -> None:
    """Print what the library warns about (the transfer pool backing off, ...) as it happens.

    Nothing else in the program configures logging, so without this a warning would come out
    bare from Python's last-resort handler, or not at all under a test runner."""
    logger = logging.getLogger("tgmirror")
    if not any(isinstance(h, _Warnings) for h in logger.handlers):
        logger.addHandler(_Warnings())
    logger.setLevel(logging.WARNING)


app.command("login")(auth.login)
app.command("logout")(auth.logout)
app.command("whoami")(auth.whoami)
app.command("channels")(channels.channels)
app.command("clone")(clone.clone)
app.command("run")(run.run_clone)
app.command("pause")(control.pause)
app.command("stop")(control.stop)
app.command("history")(history.history)
app.command("retry")(retry.retry)
app.command("status")(status.status)
