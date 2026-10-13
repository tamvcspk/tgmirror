"""YAML files and CLI flags to a ``FilterSpec`` (docs/03-filters.md, "CLI shorthand").

The wizard collects the same strings the flags carry and builds the same ``FlagFilters``, so all
three sources end in ``FilterSpec`` validation and there is one set of error messages.

Flag semantics: the "positive" flags describe *one* include rule (``--media video --hashtag #x``
means video AND #x; repeating ``--hashtag`` is any-of), and every ``--exclude-*`` flag is its own
exclude rule. Anything more elaborate (several include rules, ``mime``, ``duration``, ...) is YAML.
"""

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from tgmirror.filters.model import FilterError, FilterSpec

_SELECTION_KEYS = frozenset({"version", "filter", "source", "snapshot", "transform"})


class FilterFileError(FilterError):
    """The ``--filter-file`` could not be read or is not YAML."""


@dataclass(frozen=True, slots=True)
class FlagFilters:
    """The filter flags as typed. ``media`` values are comma-separated."""

    media: str | None = None
    hashtag: list[str] = field(default_factory=list)
    contains: list[str] = field(default_factory=list)
    regex: str | None = None
    exclude_regex: list[str] = field(default_factory=list)
    exclude_media: str | None = None
    since: str | None = None
    until: str | None = None
    min_size: str | None = None
    max_size: str | None = None
    album: str | None = None
    from_user: list[int] = field(default_factory=list)  # group/forum sources only (phase 8)
    topic: list[int] = field(default_factory=list)  # forum sources only (phase 8)
    # phase 18a. ``forwarded=False`` is a real answer (``--not-forwarded``), unlike an empty value.
    forwarded: bool | None = None
    forwarded_from: list[int] = field(default_factory=list)
    has_link: bool = False  # any link at all; ``link_domain`` narrows it
    link_domain: list[str] = field(default_factory=list)

    @property
    def given(self) -> bool:
        # ``forwarded=False`` counts (it is a tri-state); ``has_link=False`` is just "not asked"
        return self.forwarded is not None or any(
            getattr(self, f.name) for f in fields(self) if f.name != "forwarded"
        )


def _split(text: str | None) -> list[str] | None:
    if text is None:
        return None
    return [part.strip() for part in text.split(",") if part.strip()] or None


def from_flags(flags: FlagFilters) -> FilterSpec:
    rule: dict[str, Any] = {}
    if (media := _split(flags.media)) is not None:
        rule["media"] = media
    if flags.hashtag:
        rule["hashtag"] = flags.hashtag
    if flags.contains:
        rule["contains"] = flags.contains
    if flags.regex is not None:
        rule["regex"] = flags.regex
    if flags.from_user:
        rule["from_user"] = flags.from_user
    if flags.topic:
        rule["topic"] = flags.topic
    if flags.forwarded is not None:
        rule["forwarded"] = flags.forwarded
    if flags.forwarded_from:
        rule["forwarded_from"] = flags.forwarded_from
    if flags.link_domain:
        rule["link"] = flags.link_domain
    elif flags.has_link:
        rule["link"] = []
    if flags.min_size is not None or flags.max_size is not None:
        size = {"min": flags.min_size, "max": flags.max_size}
        rule["size"] = {k: v for k, v in size.items() if v is not None}

    exclude: list[dict[str, Any]] = [{"regex": pattern} for pattern in flags.exclude_regex]
    if (excluded := _split(flags.exclude_media)) is not None:
        exclude.append({"media": excluded})

    data: dict[str, Any] = {}
    if rule:
        data["include"] = [rule]
    if exclude:
        data["exclude"] = exclude
    if flags.since is not None or flags.until is not None:
        span = {"from": flags.since, "to": flags.until}
        data["date"] = {k: v for k, v in span.items() if v is not None}
    if flags.album is not None:
        data["album"] = flags.album
    return FilterSpec.from_data(data)


def from_file(path: Path) -> FilterSpec:
    """A YAML (or JSON) filter file, e.g. ``include: [{media: [video]}]``."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FilterFileError(f"cannot read {path}: {exc}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or str(exc)
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        # the classic slip: an unquoted #tag is a comment, which leaves the list unfinished
        hint = (
            ' Hashtags need quotes ("#news"): "#" starts a comment in YAML.' if "#" in text else ""
        )
        raise FilterFileError(f"{path} is not valid YAML{where}: {problem}.{hint}") from None
    if data is not None and not isinstance(data, dict):
        raise FilterFileError(f"{path} must hold a mapping (include:, exclude:, date:, ...)")
    if data is not None and _SELECTION_KEYS & data.keys():
        # a file `tgmirror inspect --save` wrote: it names a source and may freeze a list of ids,
        # which --filter-file (the filter alone) would silently drop
        raise FilterFileError(f"{path} is a selection file: give it to --selection instead")
    try:
        return FilterSpec.from_data(data)
    except FilterError as exc:
        raise FilterFileError(f"{path}: {exc.detail}") from None


def layer(base: FilterSpec, over: FilterSpec) -> FilterSpec:
    """``over`` (what the filter flags say) put on top of ``base`` (a filter or selection file):
    the file is the starting point and a flag only ever *narrows* it, so what a flag adds can be
    read off the command line without opening the file.

    - include rules: the flags make one rule, which is added (AND) to **each** rule of the file —
      a file's rules are alternatives, and the flag holds for every one of them. For a predicate
      both name (``hashtag`` in each), the flag's value replaces the file's: two any-of lists
      cannot be ANDed inside one rule. A file with no include rule is simply narrowed by the flag.
    - exclude rules: both apply.
    - ``date``: ``--since`` / ``--until`` replace that end only, the other end stays the file's.
    - ``album``: the flag's, if one was given.
    """
    if over.is_empty and "album" not in over.model_fields_set:
        return base
    mine = base.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    flags = over.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    data = dict(mine)
    if flags.get("include"):
        rules = mine.get("include") or [{}]
        data["include"] = [{**rule, **flag} for rule in rules for flag in flags["include"]]
    if flags.get("exclude"):
        kept = list(mine.get("exclude", []))
        data["exclude"] = kept + [r for r in flags["exclude"] if r not in kept]
    if flags.get("date"):
        data["date"] = {**mine.get("date", {}), **flags["date"]}
    if "album" in over.model_fields_set:
        data["album"] = over.album
    return FilterSpec.from_data(data)


def resolve(flags: FlagFilters, file: Path | None) -> FilterSpec | None:
    """The filter the user asked for, or ``None`` when they gave none: the ``--filter-file`` with
    the flags layered on top of it (``layer``), or the flags alone."""
    if file is None:
        return from_flags(flags) if flags.given else None
    base = from_file(file)
    return layer(base, from_flags(flags)) if flags.given else base
