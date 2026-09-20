"""The client matcher: does a unit pass the filter? Pure, no I/O (docs/03-filters.md).

A unit is cloned iff ``global_ok AND (no include OR one include rule matches) AND NOT any exclude
rule matches``. Inside a rule the predicates are ANDed and all look at *one message*; a predicate
about something the message lacks (``size`` of a text, ``views`` of a group post) is false. For an
album, ``album`` picks how the members are judged; excludes always use ``any``, so one excluded
member excludes the album. ``date`` and ``id`` judge the unit by its first message.
"""

from collections.abc import Callable
from datetime import UTC
from fnmatch import fnmatchcase

import regex

from tgmirror.core.gateway import SrcMessage, Unit
from tgmirror.filters.model import (
    CountRange,
    DurationRange,
    FilterError,
    FilterSpec,
    Rule,
    SizeRange,
)

REGEX_TIMEOUT = 0.5  # seconds for one search; filters may come from a shared YAML file

Predicate = Callable[[SrcMessage], bool]


def _within(value: float | None, bounds: SizeRange | DurationRange | CountRange) -> bool:
    if value is None:
        return False
    return (bounds.min is None or value >= bounds.min) and (
        bounds.max is None or value <= bounds.max
    )


def _compile(rule: Rule) -> list[Predicate]:
    """One callable per predicate the rule sets."""
    checks: list[Predicate] = []
    if rule.media is not None:
        kinds = frozenset(rule.media)
        checks.append(lambda m: m.media in kinds)
    if rule.hashtag is not None:
        tags = frozenset(rule.hashtag)
        checks.append(lambda m: any(h.casefold() in tags for h in m.hashtags))
    if rule.contains is not None:
        needles = tuple(n.casefold() for n in rule.contains)
        checks.append(lambda m: any(n in m.text.casefold() for n in needles))
    if rule.regex is not None:
        pattern = regex.compile(rule.regex)
        checks.append(lambda m: _search(pattern, m))
    if rule.has_caption is not None:
        wanted = rule.has_caption
        checks.append(lambda m: bool(m.text.strip()) == wanted)
    if rule.size is not None:
        size = rule.size
        checks.append(lambda m: _within(m.size, size))
    if rule.duration is not None:
        duration = rule.duration
        checks.append(lambda m: _within(m.duration, duration))
    if rule.mime is not None:
        globs = rule.mime
        checks.append(lambda m: m.mime is not None and any(_mime(m.mime, g) for g in globs))
    if rule.views is not None:
        views = rule.views
        checks.append(lambda m: _within(m.views, views))
    return checks


def _mime(mime: str, pattern: str) -> bool:
    return fnmatchcase(mime.lower(), pattern)


def _search(pattern: "regex.Pattern[str]", message: SrcMessage) -> bool:
    try:
        return pattern.search(message.text, timeout=REGEX_TIMEOUT) is not None
    except TimeoutError:
        raise FilterError(
            f"the regex {pattern.pattern!r} took more than {REGEX_TIMEOUT}s on message "
            f"{message.id}; simplify it (nested repetition such as (a+)+ backtracks badly)"
        ) from None


class Matcher:
    """A ``FilterSpec`` compiled once; ``matches`` is then cheap enough for every message."""

    def __init__(self, spec: FilterSpec) -> None:
        self._spec = spec
        self._include = [_compile(r) for r in spec.include]
        self._exclude = [_compile(r) for r in spec.exclude]

    def matches(self, unit: Unit) -> bool:
        if not self._global_ok(unit.messages[0]):
            return False
        if any(_hit(rule, m) for rule in self._exclude for m in unit.messages):
            return False
        return not self._include or self._included(unit)

    def _global_ok(self, first: SrcMessage) -> bool:
        spec = self._spec
        if spec.ids is not None:
            if spec.ids.from_ is not None and first.id < spec.ids.from_:
                return False
            if spec.ids.to is not None and first.id > spec.ids.to:
                return False
        if spec.when is not None:
            date = first.date if first.date.tzinfo else first.date.replace(tzinfo=UTC)
            if spec.when.from_ is not None and date < spec.when.from_:
                return False
            if spec.when.to is not None and date >= spec.when.to:
                return False
        return True

    def _included(self, unit: Unit) -> bool:
        def hit(m: SrcMessage) -> bool:
            return any(_hit(rule, m) for rule in self._include)

        match self._spec.album:
            case "all":
                return all(hit(m) for m in unit.messages)
            case "first":
                return hit(unit.messages[0])
            case _:
                return any(hit(m) for m in unit.messages)


def _hit(checks: list[Predicate], message: SrcMessage) -> bool:
    return all(check(message) for check in checks)
