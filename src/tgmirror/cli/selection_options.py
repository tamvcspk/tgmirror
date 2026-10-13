"""``--selection`` and ``--exact`` of ``clone``/``backup``/``restore`` (phase 18a,
docs/07-inspect-transform.md): the filter that ``tgmirror inspect`` tried, used for the real thing.

Declared once, like ``filter_options``, so the three commands offer the same two flags and the same
rules:

- ``--selection FILE`` is a selection file (or a plain filter file); ``--selection @last`` is what
  ``inspect`` last made of the command's source.
- It is the base: filter flags given as well narrow it (``filters.parser.layer``). Together with
  ``--filter-file`` it is two files, and which is the base would be a guess. It fills in the
  source when the command was given none.
- A selection made for another source is refused, not used: it would select by ids that mean
  something else there.
- ``--exact`` takes exactly the messages the selection froze, instead of judging the filter again.
"""

from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli.errors import UsageProblem
from tgmirror.core.gateway import ChannelInfo
from tgmirror.core.paths import Paths
from tgmirror.engine import selection as sel
from tgmirror.engine.endpoints import find_channel
from tgmirror.engine.transform import Transform
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.parser import layer

PANEL = "Filters"

SelectionOption = Annotated[
    str | None,
    typer.Option(
        "--selection",
        help="Use the filter of a selection file `tgmirror inspect --save` wrote, or @last for "
        "what inspect last made of this source. Filter flags given as well narrow it; not with "
        "--filter-file.",
        rich_help_panel=PANEL,
    ),
]
ExactOption = Annotated[
    bool,
    typer.Option(
        "--exact",
        help="With --selection: take exactly the messages the selection froze (inspect "
        "--freeze, or @last), instead of judging its filter again. Skips what was posted since.",
        rich_help_panel=PANEL,
    ),
]


def check_args(selection: str | None, exact: bool, filter_file: Path | None) -> None:
    """The mistakes that need no file read: ``--exact`` alone, or a selection next to a
    ``--filter-file`` (two files, no base)."""
    if selection is None:
        if exact:
            raise UsageProblem("selection.exact_needs_selection")
        return
    if filter_file is not None:
        raise UsageProblem("err.conflicting_flags", flags="--selection / --filter-file")


def narrowed(chosen: FilterSpec, flags: FilterSpec | None) -> FilterSpec:
    """The selection's filter with the filter flags of the same command put on top."""
    return chosen if flags is None else layer(chosen, flags)


def _transform_of(loaded: sel.Selection, takes_transform: bool, path: Path) -> Transform:
    """The transform a selection file carries, refused where the command cannot apply one
    (a file must not be half-used)."""
    if loaded.transform.is_empty:
        return loaded.transform
    if not takes_transform:
        raise UsageProblem("selection.no_transform", path=str(path))
    return loaded.transform


def _describe(source: sel.SelectionSource) -> str:
    if source.backup:
        return source.backup
    return f"{source.title} ({source.id})" if source.title else str(source.id)


def for_channel(
    paths: Paths,
    channels: list[ChannelInfo] | tuple[ChannelInfo, ...],
    *,
    src: str | None,
    selection: str,
    exact: bool,
    takes_transform: bool = False,
) -> tuple[FilterSpec, str | None, Transform]:
    """The filter to use for a command that reads a channel, the source to use when the command
    was not given one, and the transform the file carries (empty when it has none; refused when
    the command does not ``takes_transform``). Reads files, so callers run it in a thread."""
    if selection == sel.LAST:
        if src is None:
            raise UsageProblem("selection.last_needs_src")
        channel = find_channel(channels, src)
        path = sel.last_path(paths.selections_dir, channel.id)
        if not path.exists():
            raise UsageProblem("selection.no_last", title=channel.title)
        loaded = sel.load(path, with_ids=exact)
    else:
        path = Path(selection).expanduser()
        loaded = sel.load(path, with_ids=exact)
        origin = loaded.source
        if origin is not None and origin.backup:
            raise UsageProblem("selection.source_mismatch", theirs=_describe(origin))
        if origin is not None and origin.id is not None:
            channel = find_channel(channels, src if src is not None else str(origin.id))
            if channel.id != origin.id:
                raise UsageProblem("selection.source_mismatch", theirs=_describe(origin))
            src = src if src is not None else str(origin.id)
    chosen = sel.exact(loaded, path=path) if exact else loaded.filters
    return chosen, src, _transform_of(loaded, takes_transform, path)


def for_backup(
    paths: Paths,
    directory: Path | None,
    *,
    src_id: int | None,
    selection: str,
    exact: bool,
    takes_transform: bool = False,
) -> tuple[FilterSpec, Path | None, Transform]:
    """As ``for_channel`` for ``restore``, whose source is a backup directory (``src_id`` is the
    original channel's id, what ``@last`` is keyed by)."""
    if selection == sel.LAST:
        if directory is None or src_id is None:
            raise UsageProblem("selection.last_needs_src")
        path = sel.last_path(paths.selections_dir, src_id)
        if not path.exists():
            raise UsageProblem("selection.no_last", title=str(directory))
        loaded = sel.load(path, with_ids=exact)
    else:
        path = Path(selection).expanduser()
        loaded = sel.load(path, with_ids=exact)
        origin = loaded.source
        if origin is not None and origin.backup:
            named = Path(origin.backup).expanduser()
            if directory is None:
                directory = named
            elif named.resolve() != directory.resolve():
                raise UsageProblem("selection.source_mismatch", theirs=_describe(origin))
        elif origin is not None and origin.id is not None and src_id is not None:
            if origin.id != src_id:
                raise UsageProblem("selection.source_mismatch", theirs=_describe(origin))
    chosen = sel.exact(loaded, path=path) if exact else loaded.filters
    return chosen, directory, _transform_of(loaded, takes_transform, path)
