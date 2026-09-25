"""The filter flags of ``tgmirror clone`` (docs/03-filters.md).

Declared once as Typer option types so every command that takes them offers exactly the same
flags, and ``collect``
turns them into a ``FilterSpec`` through the parser the wizard and YAML files also use.
"""

from pathlib import Path
from typing import Annotated

import typer

from tgmirror.filters.model import FilterSpec
from tgmirror.filters.parser import FlagFilters, resolve

PANEL = "Filters"

MediaOption = Annotated[
    str | None,
    typer.Option(
        "--media",
        help="Only these media types, comma-separated: photo, video, audio, voice, document, "
        "gif, sticker, video_note, poll, geo, contact, game, invoice, webpage, text.",
        rich_help_panel=PANEL,
    ),
]
HashtagOption = Annotated[
    list[str] | None,
    typer.Option(
        "--hashtag",
        help='Only messages with this hashtag ("#news"); repeat for any-of.',
        rich_help_panel=PANEL,
    ),
]
ContainsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--contains",
        help="Only messages whose text or caption contains this (any case); repeat for any-of.",
        rich_help_panel=PANEL,
    ),
]
RegexOption = Annotated[
    str | None,
    typer.Option(
        "--regex",
        help="Only messages whose text or caption matches this regular expression.",
        rich_help_panel=PANEL,
    ),
]
ExcludeRegexOption = Annotated[
    list[str] | None,
    typer.Option(
        "--exclude-regex",
        help="Leave out messages matching this regular expression; repeatable.",
        rich_help_panel=PANEL,
    ),
]
ExcludeMediaOption = Annotated[
    str | None,
    typer.Option(
        "--exclude-media",
        help="Leave out these media types, comma-separated.",
        rich_help_panel=PANEL,
    ),
]
SinceOption = Annotated[
    str | None,
    typer.Option(
        "--since", help="Only messages from this date on (YYYY-MM-DD, UTC).", rich_help_panel=PANEL
    ),
]
UntilOption = Annotated[
    str | None,
    typer.Option(
        "--until",
        help="Only messages before this date (YYYY-MM-DD, UTC; that day is not included).",
        rich_help_panel=PANEL,
    ),
]
MinSizeOption = Annotated[
    str | None,
    typer.Option(
        "--min-size", help="Only media of at least this size, e.g. 10MB.", rich_help_panel=PANEL
    ),
]
MaxSizeOption = Annotated[
    str | None,
    typer.Option(
        "--max-size", help="Only media of at most this size, e.g. 2GB.", rich_help_panel=PANEL
    ),
]
AlbumOption = Annotated[
    str | None,
    typer.Option(
        "--album",
        help="How an album is judged: any member matches (default), all of them, or the first.",
        rich_help_panel=PANEL,
    ),
]
FilterFileOption = Annotated[
    Path | None,
    typer.Option(
        "--filter-file",
        help="YAML file with the filter (include, exclude, date, id, album); "
        "not with other filter flags.",
        rich_help_panel=PANEL,
    ),
]
FromUserOption = Annotated[
    list[int] | None,
    typer.Option(
        "--from-user",
        help="Only messages from this sender id (group/forum sources); repeat for any-of. "
        "See `tgmirror topics` for ids.",
        rich_help_panel=PANEL,
    ),
]
TopicOption = Annotated[
    list[int] | None,
    typer.Option(
        "--topic",
        help="Only messages in this forum topic id; repeat for any-of. `tgmirror topics <src>` "
        "lists them.",
        rich_help_panel=PANEL,
    ),
]


def collect(
    *,
    media: str | None,
    hashtag: list[str] | None,
    contains: list[str] | None,
    regex: str | None,
    exclude_regex: list[str] | None,
    exclude_media: str | None,
    since: str | None,
    until: str | None,
    min_size: str | None,
    max_size: str | None,
    album: str | None,
    filter_file: Path | None,
    from_user: list[int] | None = None,
    topic: list[int] | None = None,
) -> FilterSpec | None:
    """The filter the flags describe, or ``None`` when none was given. Raises ``FilterError``."""
    flags = FlagFilters(
        media=media,
        hashtag=hashtag or [],
        contains=contains or [],
        regex=regex,
        exclude_regex=exclude_regex or [],
        exclude_media=exclude_media,
        since=since,
        until=until,
        min_size=min_size,
        max_size=max_size,
        album=album,
        from_user=from_user or [],
        topic=topic or [],
    )
    return resolve(flags, filter_file)
