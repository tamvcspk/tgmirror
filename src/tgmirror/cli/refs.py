"""How a command names a channel: the one help text, and a friendly answer to the one mistake
the argument parser cannot tell from an option.

``find_channel`` (``engine/endpoints.py``) accepts ``@username``, a numeric id with or without the
``-100`` prefix, an exact title and a bare username. A positional argument that starts with ``-``
(a marked channel id such as ``-1002428805566``) is read by Click as an option, so the commands
that take the source as an argument use ``SourceCommand``, which turns that error into a hint.
"""

import re

import typer
from typer.core import TyperCommand

from tgmirror.ui.messages import t

SOURCE_HELP = (
    'A chat you joined: "@username" (keep the quotes in PowerShell), its id with or without '
    "-100 (write -- before an id that starts with -, or drop the minus), or its exact title."
)
_MARKED_ID = re.compile(r"-\d{5,}")


class SourceCommand(TyperCommand):
    """A command whose first argument names a channel."""

    def parse_args(self, ctx: typer.Context, args: list[str]) -> list[str]:
        given_args = list(args)  # the parser consumes ``args`` in place
        try:
            return super().parse_args(ctx, args)
        except Exception as exc:
            # typer vendors click; its NoSuchOption is not a public name to import
            if type(exc).__name__ != "NoSuchOption":
                raise
            given = next((a for a in given_args if _MARKED_ID.fullmatch(a)), None)
            if given is None:
                raise
            bare = given.lstrip("-")
            raise typer.BadParameter(
                t("err.channel_id_as_option", given=given, bare=bare), ctx=ctx
            ) from None
