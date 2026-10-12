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

from tgmirror.core.gateway import GENERAL_TOPIC_ID, SrcMessage, Unit
from tgmirror.core.links import domain_matches
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
    if rule.from_user is not None:
        senders = frozenset(rule.from_user)
        checks.append(lambda m: m.from_user_id in senders)
    if rule.topic is not None:
        topics = frozenset(rule.topic)
        # a General message has no topic id of its own; ``topic: 1`` means it
        checks.append(lambda m: (m.topic_id or GENERAL_TOPIC_ID) in topics)
    checks.extend(_metadata_checks(rule))
    return checks


def _metadata_checks(rule: Rule) -> list[Predicate]:
    """The phase 18a predicates, over the optional metadata a message may carry."""
    checks: list[Predicate] = []
    if rule.forwarded is not None:
        forwarded = rule.forwarded
        checks.append(lambda m: m.is_forward == forwarded)
    if rule.forwarded_from is not None:
        origins = frozenset(rule.forwarded_from)
        checks.append(lambda m: m.fwd_from_id in origins)
    if rule.is_reply is not None:
        reply = rule.is_reply
        checks.append(lambda m: (m.reply_to_id is not None) == reply)
    if rule.edited is not None:
        edited = rule.edited
        checks.append(lambda m: (m.edit_date is not None) == edited)
    if rule.link is not None:
        domains = rule.link
        if domains:
            checks.append(
                lambda m: any(domain_matches(d, w) for d in m.link_domains for w in domains)
            )
        else:
            checks.append(lambda m: bool(m.link_domains))
    if rule.filename is not None:
        names = rule.filename
        checks.append(
            lambda m: (
                m.file_name is not None
                and any(fnmatchcase(m.file_name.casefold(), g) for g in names)
            )
        )
    if rule.forwards is not None:
        forwards = rule.forwards
        checks.append(lambda m: _within(m.forwards, forwards))
    if rule.reactions is not None:
        reactions = rule.reactions
        checks.append(lambda m: _within(m.reactions, reactions))
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
        self._only = None if spec.message_ids is None else frozenset(spec.message_ids)

    def matches(self, unit: Unit) -> bool:
        if not self._global_ok(unit.messages[0]):
            return False
        if self._only is not None and self._only.isdisjoint(unit.ids):
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
