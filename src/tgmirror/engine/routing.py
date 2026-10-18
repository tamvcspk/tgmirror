"""Topic routing rules (docs/07-inspect-transform.md, section 11.2, phase 21): where a message goes
in a forum, or which hashtag it gets in a chat without topics.

A rule is a ``when`` (a filter rule: the same predicates as ``--filter-file``), a place to take a
candidate from (``from``), what to keep of it (``regex``, ``pick``, ``skip``) and a name template
(``to``)::

    - when: {hashtag: ["#vip"]}                     # a fixed name for what the condition selects
      to: "VIP"
    - from: hashtags                                # a candidate per hashtag, in written order
      pick: last                                    # first (default) | last
      skip: ["#YYLive"]
      to: "{value|tag_title}"
    - from: text                                    # a candidate per match of the regex
      regex: '(?m)^(?P<name>[^\\s.]+?)(?:[_-]\\d+)*\\.mp4\\s*$'
      to: "{name|title}"

The first rule that yields a name wins; a name that comes out empty is no name, and the next rule
is tried. Declarative on purpose (no expression language, no user-defined filters): every rule can
be checked when the file is loaded and explained in one line. Pure, no I/O.

``TransformError`` lives here because the rules raise it and ``engine/transform.py`` (which
re-exports it) imports this module.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import regex
from pydantic import ValidationError

from tgmirror.core.gateway import GENERAL_TOPIC_ID, CaptionOp, RemoveHashtags, Replace, Unit
from tgmirror.filters.matcher import rule_matcher
from tgmirror.filters.model import FilterError, Rule, explain, normalize_hashtag

MAX_TOPIC_TITLE = 128  # Telegram's limit for a forum topic's title
SOURCES = ("hashtags", "text", "file_name", "topic", "forwarded_from")
PICKS = ("first", "last")
REGEX_TIMEOUT = 0.5  # seconds for one search; a transform file may be shared
_RULE_KEYS = frozenset({"when", "from", "regex", "pick", "skip", "to"})


class TransformError(FilterError):
    """A transform cannot be used. ``detail`` says which part and why (exit code 2)."""


# ---- names --------------------------------------------------------------------------------------


def title_of_tag(tag: str) -> str:
    """``#machine_learning`` -> ``Machine learning``: the name ``auto`` gives a hashtag's topic."""
    name = tag.lstrip("#").replace("_", " ").strip()
    return (name[:1].upper() + name[1:])[:MAX_TOPIC_TITLE]


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


def _slug(text: str) -> str:
    return re.sub(r"\W+", "_", text, flags=re.UNICODE).strip("_").casefold()


# The filters a template may apply, with `|`. A fixed set: nothing the user writes is evaluated.
NAME_FILTERS: dict[str, Callable[[str], str]] = {
    "title": _capitalised,
    "lower": str.lower,
    "upper": str.upper,
    "tag_title": title_of_tag,
    "slug": _slug,
}
_TOKEN = re.compile(r"\{\{|\}\}|\{([^{}]*)\}|[{}]")
_SLOT_NAME = re.compile(r"value|\d+|[^\W\d]\w*")


@dataclass(frozen=True, slots=True)
class Slot:
    """``{name|filter|filter}`` in a template."""

    name: str
    filters: tuple[str, ...] = ()


Template = tuple["str | Slot", ...]


@lru_cache(maxsize=512)
def parse_template(text: str) -> Template:
    """``"{name|title} - part {1}"`` to its pieces. ``{{`` and ``}}`` are the braces themselves.
    Raises ``ValueError`` (the caller says where)."""
    parts: list[str | Slot] = []
    pos = 0

    def literal(chunk: str) -> None:
        if chunk:
            if parts and isinstance(parts[-1], str):
                parts[-1] += chunk
            else:
                parts.append(chunk)

    for found in _TOKEN.finditer(text):
        literal(text[pos : found.start()])
        pos = found.end()
        token = found.group(0)
        if token in ("{{", "}}"):
            literal(token[0])
        elif found.group(1) is None:
            raise ValueError(f"a lone {token!r}: write {token}{token} for the character itself")
        else:
            name, *filters = (piece.strip() for piece in found.group(1).split("|"))
            if not _SLOT_NAME.fullmatch(name):
                raise ValueError(f"{{{found.group(1)}}}: `{name}` is not a name a template can use")
            if unknown := [f for f in filters if f not in NAME_FILTERS]:
                known = ", ".join(NAME_FILTERS)
                raise ValueError(
                    f"{{{found.group(1)}}}: not a filter: {unknown[0]!r} (use {known})"
                )
            parts.append(Slot(name, tuple(filters)))
    literal(text[pos:])
    return tuple(parts)


def escaped(text: str) -> str:
    """``text`` as a template that renders as itself (a name that is a name, not a pattern)."""
    return text.replace("{", "{{").replace("}", "}}")


def render(template: Template, values: Mapping[str, str]) -> str | None:
    """The name a template makes: spaces collapsed, cut to a topic title's length. ``None`` when
    nothing is left (a rule that names nothing does not apply)."""
    out: list[str] = []
    for part in template:
        if isinstance(part, str):
            out.append(part)
            continue
        value = values.get(part.name, "")
        for name in part.filters:
            value = NAME_FILTERS[name](value)
        out.append(value)
    title = " ".join("".join(out).split())[:MAX_TOPIC_TITLE].strip()
    return title or None


# ---- one rule -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Routed:
    """The outcome for a unit: the name, which rule gave it, and what that rule took the name from
    (``strip: matched`` takes it out of the caption)."""

    title: str
    rule: int
    tags: tuple[str, ...] = ()  # hashtags the name came from
    segments: tuple[str, ...] = ()  # text the regex matched

    def strip_ops(self) -> tuple[CaptionOp, ...]:
        ops: list[CaptionOp] = []
        if self.tags:
            ops.append(RemoveHashtags(self.tags))
        for segment in self.segments:
            # alone on its line it takes the line with it; anywhere else just itself
            body = regex.escape(segment)
            ops.append(Replace(rf"(?m)^[ \t]*{body}[ \t]*(?:\n|$)|{body}", ""))
        return tuple(ops)


@dataclass(frozen=True, slots=True)
class RouteRule:
    to: str  # a template; without ``source`` it has no slot (a fixed name)
    when: Rule | None = None
    source: str | None = None  # one of ``SOURCES``
    pattern: str | None = None  # the ``regex`` key
    pick: str = "first"
    skip: tuple[str, ...] = ()  # candidates to leave alone (hashtags with ``#``), case-folded

    @property
    def variable(self) -> bool:
        """The name is made from the message (a slot in ``to``), so every message may mean a new
        topic: what ``limit`` guards."""
        return any(isinstance(part, Slot) for part in parse_template(self.to))

    @property
    def fixed_name(self) -> str | None:
        return None if self.variable else render(parse_template(self.to), {})

    @property
    def is_auto_all(self) -> bool:
        """The rule ``auto: all`` / ``--hashtag-to-topic`` stands for (its ``skip`` may differ)."""
        return self.source == "hashtags" and self.when is None and self.to == AUTO_ALL.to

    @property
    def strippable(self) -> bool:
        """What routes a message by this rule can be taken out of its caption: a hashtag, or the
        text a pattern found (a file name, a topic's title or a forward's origin is not in it)."""
        if self.source is None:
            return self.when is not None and bool(self.when.hashtag)
        return self.source in ("hashtags", "text")

    def apply(self, unit: Unit, index: int, titles: Mapping[int, str] | None) -> Routed | None:
        """The name this rule gives ``unit``, or ``None`` when it gives none."""
        if self.when is not None and not rule_matcher(self.when)(unit):
            return None
        template = parse_template(self.to)
        if self.source is None:
            title = render(template, {})
            if title is None:
                return None
            wanted = self.when.hashtag if self.when is not None and self.when.hashtag else ()
            have = {h.casefold() for m in unit.messages for h in m.hashtags}
            return Routed(title, index, tuple(t for t in wanted if t in have))
        hits = self._hits(unit, titles)
        if not hits:
            return None
        value, values, segment = hits[-1] if self.pick == "last" else hits[0]
        title = render(template, values)
        if title is None:
            return None
        if self.source == "hashtags":
            return Routed(title, index, (value,))
        return Routed(title, index, (), (segment,) if segment else ())

    def _hits(
        self, unit: Unit, titles: Mapping[int, str] | None
    ) -> list[tuple[str, dict[str, str], str | None]]:
        """Each candidate that survives ``skip`` and ``regex``, in the order the message has them:
        the candidate, the values a template may use, and the matched text of ``from: text``."""
        assert self.source is not None
        compiled = _compiled(self.pattern) if self.pattern is not None else None
        hits: list[tuple[str, dict[str, str], str | None]] = []
        for candidate in _candidates(self.source, unit, titles):
            if compiled is None:
                found = [(candidate, {"value": candidate}, None)]
            else:
                found = _matches(compiled, candidate, text=self.source == "text")
            for value, values, segment in found:
                if value.casefold().strip() not in self.skip:
                    hits.append((value, values, segment))
        return hits

    # ---- the canonical form -------------------------------------------------------------------

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.when is not None:
            data["when"] = self.when.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        if self.source is not None:
            data["from"] = self.source
            if self.pattern is not None:
                data["regex"] = self.pattern
            if self.pick != "first":
                data["pick"] = self.pick
            if self.skip:
                data["skip"] = list(self.skip)
        data["to"] = self.to
        return data


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> "regex.Pattern[str]":
    return regex.compile(pattern)


def _candidates(source: str, unit: Unit, titles: Mapping[int, str] | None) -> list[str]:
    messages = unit.messages  # an album's candidates are those of all its messages, in order
    if source == "hashtags":
        return list(dict.fromkeys(h.casefold() for m in messages for h in m.hashtags))
    if source == "text":
        return [m.text for m in messages if m.text.strip()]
    if source == "file_name":
        return [m.file_name for m in messages if m.file_name]
    if source == "forwarded_from":
        return list(dict.fromkeys(m.fwd_from_name for m in messages if m.fwd_from_name))
    topic_id = unit.topic_id  # source == "topic": a message outside every topic has none
    if topic_id is None:
        return []
    title = (titles or {}).get(topic_id) or ("General" if topic_id == GENERAL_TOPIC_ID else None)
    return [title] if title else []


def _values(found: "regex.Match[str]", value: str) -> dict[str, str]:
    values = {str(i): found.group(i) or "" for i in range(found.re.groups + 1)}
    values.update({name: text or "" for name, text in found.groupdict().items()})
    values["value"] = value
    return values


def _matches(
    pattern: "regex.Pattern[str]", candidate: str, *, text: bool
) -> list[tuple[str, dict[str, str], str | None]]:
    """What ``pattern`` makes of one candidate. A caption is searched for every match (each is a
    candidate: its text is ``{value}``); any other candidate is kept whole when the pattern finds
    something in it, the groups and ``{0}`` being the part it found."""
    try:
        if not text:
            found = pattern.search(candidate, timeout=REGEX_TIMEOUT)
            return [] if found is None else [(candidate, _values(found, candidate), None)]
        return [
            (m.group(0), _values(m, m.group(0)), m.group(0))
            for m in pattern.finditer(candidate, timeout=REGEX_TIMEOUT)
            if m.group(0).strip()
        ]
    except TimeoutError:
        raise TransformError(
            f"the route regex {pattern.pattern!r} took more than {REGEX_TIMEOUT}s on a message; "
            "simplify it (nested repetition such as (a+)+ backtracks badly)"
        ) from None


# ---- reading rules ------------------------------------------------------------------------------


def parse_rule(where: str, raw: Any) -> RouteRule:
    if not isinstance(raw, dict):
        raise TransformError(f"{where}: a mapping with `to:` (and `when`, `from`, ...)")
    if unknown := raw.keys() - _RULE_KEYS:
        names = ", ".join(sorted(map(str, unknown)))
        raise TransformError(f"{where}: not a key of a rule: {names} (one of {sorted(_RULE_KEYS)})")
    if "to" not in raw:
        raise TransformError(f"{where}: `to:` is missing (the name of the topic)")
    to = raw["to"]
    if not isinstance(to, str) or not to.strip() or "\n" in to:
        raise TransformError(f"{where}.to: the name of a topic (one line of text)")
    try:
        template = parse_template(to)
    except ValueError as exc:
        raise TransformError(f"{where}.to: {exc}") from None
    when = _when(where, raw.get("when")) if raw.get("when") is not None else None
    source = raw.get("from")
    if source is not None and source not in SOURCES:
        raise TransformError(f"{where}.from: one of {', '.join(SOURCES)}")
    pattern = raw.get("regex")
    pick = raw.get("pick", "first")
    skip_raw = raw.get("skip")
    if source is None:
        for key in ("regex", "pick", "skip"):
            if key in raw:
                raise TransformError(f"{where}.{key}: only means something with `from:`")
        if any(isinstance(part, Slot) for part in template):
            raise TransformError(
                f"{where}.to: has a {{...}} but no `from:` to take it from "
                "(write {{ and }} for the braces themselves)"
            )
        name = " ".join("".join(p for p in template if isinstance(p, str)).split())
        if len(name) > MAX_TOPIC_TITLE:
            raise TransformError(
                f"{where}.to: a topic name is at most {MAX_TOPIC_TITLE} characters"
            )
        return RouteRule(to, when)
    groups: set[str] = {"value"}
    if pattern is not None:
        compiled = checked_pattern(where, pattern)
        groups |= {str(i) for i in range(compiled.groups + 1)} | set(compiled.groupindex)
    elif source == "text":
        raise TransformError(f"{where}.regex: `from: text` needs a pattern to find the part to use")
    for part in template:
        if isinstance(part, Slot) and part.name not in groups:
            have = ", ".join(sorted(groups, key=lambda n: (not n.isdigit(), n)))
            raise TransformError(f"{where}.to: {{{part.name}}} is not there (have: {have})")
    if pick not in PICKS:
        raise TransformError(f"{where}.pick: first or last")
    return RouteRule(to, when, source, pattern, pick, parse_skip(where, source, skip_raw))


def checked_pattern(where: str, pattern: Any) -> "regex.Pattern[str]":
    if not isinstance(pattern, str) or not pattern:
        raise TransformError(f"{where}.regex: a pattern")
    try:
        return regex.compile(pattern)
    except regex.error as exc:
        raise TransformError(f"{where}.regex: {exc}") from None


def parse_skip(where: str, source: str, raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    items = [raw] if isinstance(raw, str) else raw
    if not isinstance(items, list) or not items or not all(isinstance(i, str) for i in items):
        raise TransformError(f"{where}.skip: a list of the candidates to leave alone")
    try:
        names = [
            normalize_hashtag(i) if source == "hashtags" else i.casefold().strip() for i in items
        ]
    except ValueError as exc:
        raise TransformError(f"{where}.skip: {exc}") from None
    return tuple(dict.fromkeys(names))


def _when(where: str, raw: Any) -> Rule:
    if not isinstance(raw, dict):
        raise TransformError(
            f"{where}.when: a mapping of filter predicates, like {{hashtag: [..]}}"
        )
    try:
        return Rule.model_validate(raw)
    except ValidationError as exc:
        raise TransformError(f"{where}.when: {explain(exc)}") from None


def tag_rule(tag: str, topic: str) -> RouteRule:
    """A message with ``tag`` goes to the topic called ``topic``, taken literally (braces in it are
    kept as they are): the ``hashtag_to_topic`` shorthand's ``map`` and ``auto: [..]``."""
    return RouteRule(escaped(topic), Rule(hashtag=(tag,)))


def auto_rule(
    skip: tuple[str, ...] = (), pattern: str | None = None, pick: str = "first"
) -> RouteRule:
    """Every hashtag gets a topic named after it: ``auto: all``, ``--hashtag-to-topic``. ``pattern``
    (``--topic-regex``) keeps only the hashtags it matches, ``pick`` (``--topic-pick``) takes the
    first or the last of the ones left."""
    return RouteRule("{value|tag_title}", None, "hashtags", pattern, pick, skip)


AUTO_ALL = auto_rule()
