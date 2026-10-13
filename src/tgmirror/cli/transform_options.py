"""``--transform`` of ``clone``/``restore`` (phase 19, docs/07-inspect-transform.md): declared
once, like ``filter_options`` and ``selection_options``, so the commands that apply a transform
offer the same flag and the same rules.

The transform can also arrive in the ``transform:`` section of a ``--selection`` file. Both at
once is a conflict, not a merge: two lists of operations in an order nobody wrote down would be
a guess.
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli.errors import UsageProblem
from tgmirror.engine import transform as tf
from tgmirror.engine.transform import Transform

TransformOption = Annotated[
    Path | None,
    typer.Option(
        "--transform",
        help="A transform file: a list of operations applied to captions (strip_links, "
        "remove_hashtags, remove_mentions, replace, append, ...; docs/07-inspect-transform.md). "
        "The messages it changes are sent again (by file id when the source allows saving "
        "content, otherwise downloaded and re-uploaded). Not with --caption.",
        rich_help_panel="Transform",
    ),
]


async def resolve(flag: Path | None, from_selection: Transform | None = None) -> Transform:
    """The transform to use: the ``--transform`` file, or the one a selection file carried.
    Reads a file, so it is awaited like the other flag parsing steps that touch the disk."""
    given = from_selection is not None and not from_selection.is_empty
    if flag is None:
        return from_selection if from_selection is not None else Transform()
    if given:
        raise UsageProblem(
            "err.conflicting_flags", flags="--transform / --selection (its transform)"
        )
    return await asyncio.to_thread(_load, flag)


def _load(path: Path) -> Transform:
    return tf.load(path.expanduser())
