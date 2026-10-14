"""Transform (docs/07-inspect-transform.md, phase 19): what a clone or restore changes on the way.

A transform is a declarative list of operations, the same one whether it comes from a
``--transform`` file, the ``transform:`` section of a selection file, or the older ``--caption``
flag (a shorthand, ``core.gateway.ops_of_mode``)::

    version: 1
    caption:                          # in this order
      - strip_links: source           # source | all
      - remove_hashtags: ["#ads"]     # or: all
      - remove_mentions: source       # source | all
      - replace: {regex: "(?i)contact @\\w+", with: ""}   # ``with`` is literal text
      - append: "\\n\\n- Channel B"
      - text_too: true                # also rewrite plain text messages (default: media captions)

Only declarative operations, no expression language: a transform can be checked before anything
runs, tested and explained in one line. Pure (no I/O besides ``load``); the operations themselves
are applied by the gateway (``core/telethon_gateway.py::rewrite_caption``), which owns the
entity types.

Phase 19 arrives in slices (docs/06-lo-trinh.md): 19a is the caption operations, 19b the ``topics``
section (where a message lands in a forum, and how a topic is named)::

    topics:
      hashtag_to_topic:               # the destination must be a forum
        map:                          # in this order: the first rule a message matches wins
          "#python": "Python"
          "#rust": "Rust lang"
        auto: ["#go", "#zig"]         # the topic is named after the tag: "Go", "Zig"
                                      # or `auto: all`: every hashtag gets its own topic, up to
        limit: 30                     #   ``limit`` new ones per run (then General); skip: [...]
        default: "Misc"               # no rule matched and the message sits in no source topic
        strip: true                   # take the hashtag that routed a message out of its caption
      topic_map:                      # a source topic (title or id) -> the destination topic's name
        "Old name": "New name"        # several into one = merged; also names the topic hashtag
      hashtag_format: "#t_{slug}"     # a topic kept as a hashtag in a destination without topics

Slice 19c, the references between messages, arrives a key at a time::

    reply: keep                       # a reply answers the copy of the message it answered

A message sent again answers the message at the destination that stands for the one it answered
here (``msg_map``); a reply to a message that was not copied loses its reply, and a forward cannot
carry a reply at all, so these messages are sent again (decision D3 as for any message that is).

    forward_header: keep              # "Forwarded from <source>" stays on what is forwarded

The default (``drop``) is the forward without author of decision D2. The header is a property of a
forward, so ``keep`` is the one key that asks for the opposite of the others: it cannot go together
with anything that sends a message again (a caption operation, ``strip``, ``reply: keep``), and a
transform that asks for both is refused rather than left to lose one of them silently.
Slice 19c3, the links between messages::

    links: rewrite                    # a link to a message of the source -> to its copy

A link written as ``t.me/<source>/<id>`` (or ``t.me/c/<number>/<id>``) in a caption or a text
becomes a link to the message that stands for ``<id>`` at the destination, found through
``msg_map`` like a reply's. A link to a message that has no copy *yet* (a later one, or one the
filter left out) stays as it is, and the run counts those. Rewriting changes the text, so the
messages that carry such a link are sent again.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import regex
import yaml

from tgmirror.core.gateway import (
    GENERAL_TOPIC_ID,
    Append,
    CaptionOp,
    Clear,
    RemoveHashtags,
    RemoveMentions,
    Replace,
    StripLinks,
    Unit,
)
from tgmirror.filters.model import FilterError, normalize_hashtag

FORMAT_VERSION = 1
MAX_OPERATIONS = 50
_SCOPES = ("source", "all")
_KEYS = frozenset({"version", "caption", "topics", "reply", "forward_header", "links"})
_TOPICS_KEYS = frozenset({"hashtag_to_topic", "topic_map", "hashtag_format"})
_TOPICS_KEY_HINTS = {  # what a key needs under it (a present key with no value is an error)
    "hashtag_to_topic": 'give `auto: all` (a topic per hashtag), `auto: ["#a"]` or `map:`',
    "topic_map": 'give `"Old name": "New name"` lines',
    "hashtag_format": 'give a format like "#t_{slug}"',
}
_ROUTING_KEYS = frozenset({"map", "auto", "default", "strip", "limit", "skip"})
MAX_TOPIC_TITLE = 128  # Telegram's limit for a forum topic's title
MAX_TOPIC_RULES = 200
DEFAULT_AUTO_LIMIT = 30  # topics ``auto: all`` may create in one run: a guard, not a target
MAX_AUTO_LIMIT = 1000
_HASHTAG_FORMAT_SAMPLE = re.compile(r"#\w+")
_OPERATIONS = frozenset(
    {"strip_links", "remove_hashtags", "remove_mentions", "replace", "append", "clear", "text_too"}
)


class TransformError(FilterError):
    """A transform cannot be used. ``detail`` says which part and why (exit code 2)."""


@dataclass(frozen=True, slots=True)
class HashtagRoute:
    """``hashtag_to_topic``: a message carrying ``tag`` lands in the topic titled ``topic``."""

    tag: str  # lower-case, with ``#``
    topic: str


@dataclass(frozen=True, slots=True)
class TopicRules:
    """Where a message lands in a forum and how a topic is named (phase 19b). Empty (the default)
    leaves everything to ``engine/topics.py``'s own mapping, as before."""

    routes: tuple[HashtagRoute, ...] = ()  # in priority order
    auto_all: bool = False  # after ``routes``: the first other hashtag of a message names a topic
    auto_limit: int = DEFAULT_AUTO_LIMIT  # new topics ``auto_all`` may create in one run
    auto_skip: tuple[str, ...] = ()  # hashtags ``auto_all`` leaves alone
    default: str | None = None  # for a message with no matching tag and no source topic
    strip: bool = False  # take the hashtag that routed a message out of its caption
    # a source topic (its id, or its title case-folded) -> the name of the destination topic
    renames: tuple[tuple[int | str, str], ...] = ()
    hashtag_format: str | None = None  # ``"#t_{slug}"``: how a topic kept as a hashtag is written

    @property
    def routing(self) -> bool:
        """Messages go to topics by their hashtag, so the destination must be a forum."""
        return bool(self.routes) or self.auto_all

    @property
    def is_empty(self) -> bool:
        return not (self.routing or self.renames or self.hashtag_format)

    @property
    def strips(self) -> bool:
        """Some message is sent again without its routing hashtag."""
        return self.strip and self.routing

    # ---- what a unit gets -----------------------------------------------------------------------

    def match(self, unit: Unit) -> HashtagRoute | None:
        """The first rule (rules are in priority order) one of the unit's hashtags matches. The
        hashtags of an album are those of all its messages: the caption sits on one of them."""
        if not self.routing:
            return None
        order = [h.casefold() for m in unit.messages for h in m.hashtags]
        if not order:
            return None
        found = set(order)
        route = next((r for r in self.routes if r.tag in found), None)
        if route is not None or not self.auto_all:
            return route
        # no rule of its own: the first hashtag written in the message names the topic
        tag = next((h for h in order if h not in self.auto_skip), None)
        return None if tag is None else HashtagRoute(tag, _title_of_tag(tag))

    def destination(self, unit: Unit) -> str | None:
        """Title of the topic ``unit`` goes to because of a rule (or the default), or ``None``:
        it then follows the source topic's own mapping, or lands in General. ``default`` only
        takes a message that sits in no source topic: one in a topic of a forum source keeps its
        topic, which is the better answer to "where does it belong"."""
        route = self.match(unit)
        if route is not None:
            return route.topic
        return self.default if unit.topic_id is None else None

    def strip_tag(self, unit: Unit) -> str | None:
        """The hashtag to take out of the caption of ``unit`` (``strip: true``), if a rule
        routed it."""
        if not self.strip:
            return None
        route = self.match(unit)
        return None if route is None else route.tag

    # ---- topic names ----------------------------------------------------------------------------

    def rename(self, topic_id: int, title: str) -> str:
        """The name a source topic gets: ``topic_map`` by id first, then by title, else its own."""
        for key, name in self.renames:
            if key == topic_id:
                return name
        folded = title.casefold()
        for key, name in self.renames:
            if key == folded:
                return name
        return title

    def hashtag_of(self, title: str) -> str:
        """A hashtag Telegram will actually parse (word characters only) standing in for the topic
        ``title``. Not ``filters.model.normalize_hashtag``: that one validates a user-typed filter
        value and rejects anything with whitespace, which most topic titles have."""
        slug = re.sub(r"\W+", "_", title, flags=re.UNICODE).strip("_").casefold() or "topic"
        return (self.hashtag_format or "#{slug}").format(slug=slug)

    # ---- flags on top of a file -----------------------------------------------------------------

    def layered(
        self,
        *,
        renames: Sequence[tuple[int | str, str]] = (),
        default: str | None = None,
        strip: bool = False,
    ) -> "TopicRules":
        """``self`` (what a transform file says) with the command-line flags put on top, the same
        way ``--hashtag-to-topic`` is: a single value replaces the file's (``default``), a switch
        only turns on (``strip``), and ``renames`` (``--topic-map``: a source topic id or title and
        the name it gets) add to the file's, winning for a topic both name. Raises
        ``TransformError`` when a flag has nothing to act on."""
        rules = self
        if default is not None or strip:
            if not self.routing:
                raise TransformError(
                    "--topic-default / --strip-routing-hashtag: only mean something with hashtag "
                    "routing (--hashtag-to-topic, or `hashtag_to_topic` in the transform file)"
                )
            if default is not None:
                rules = replace(rules, default=_topic_title("--topic-default", default))
            if strip:
                rules = replace(rules, strip=True)
        if renames:
            entries = [
                {"id": key, "to": name} if isinstance(key, int) else {"title": key, "to": name}
                for key, name in renames
            ]
            merged = dict(rules.renames)
            merged.update(_renames(entries))
            rules = replace(rules, renames=_in_canonical_order(merged))
        return rules

    # ---- the canonical form ---------------------------------------------------------------------

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.routing:
            routing: dict[str, Any] = {}
            if self.routes:
                routing["map"] = [{r.tag: r.topic} for r in self.routes]
            if self.auto_all:
                routing["auto"] = "all"
                routing["limit"] = self.auto_limit
                if self.auto_skip:
                    routing["skip"] = list(self.auto_skip)
            if self.default is not None:
                routing["default"] = self.default
            if self.strip:
                routing["strip"] = True
            data["hashtag_to_topic"] = routing
        if self.renames:
            data["topic_map"] = [
                {"id": key, "to": name} if isinstance(key, int) else {"title": key, "to": name}
                for key, name in self.renames
            ]
        if self.hashtag_format is not None:
            data["hashtag_format"] = self.hashtag_format
        return data

    @classmethod
    def from_data(cls, data: Any) -> "TopicRules":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise TransformError("topics: a mapping (hashtag_to_topic, topic_map, hashtag_format)")
        unknown = data.keys() - _TOPICS_KEYS
        if unknown:
            names = ", ".join(sorted(map(str, unknown)))
            raise TransformError(f"topics: not a topics key: {names}")
        for key, hint in _TOPICS_KEY_HINTS.items():
            if key in data and data[key] is None:  # `hashtag_to_topic:` with nothing under it
                raise TransformError(
                    f"topics.{key}: nothing under it, so it would do nothing — {hint}, "
                    "or remove the key"
                )
        found = _Routing()
        if (raw := data.get("hashtag_to_topic")) is not None:
            found = _routing(raw)
        renames = _renames(data.get("topic_map"))
        fmt = _hashtag_format(data.get("hashtag_format"))
        return cls(
            found.routes,
            found.auto_all,
            found.limit,
            found.skip,
            found.default,
            found.strip,
            renames,
            fmt,
        )


@dataclass(frozen=True, slots=True)
class Transform:
    """The operations of a transform. Empty (the default) changes nothing."""

    caption: tuple[CaptionOp, ...] = ()
    text_too: bool = False  # rewrite the text of plain text messages too, not only media captions
    topics: TopicRules = TopicRules()  # phase 19b
    keep_replies: bool = False  # phase 19c: a reply answers the copy of what it answered
    keep_forward_header: bool = False  # phase 19c2: forward with "Forwarded from" (drop_author off)
    rewrite_links: bool = False  # phase 19c3: links to messages of the source -> their copies

    def __post_init__(self) -> None:
        if not self.keep_forward_header:
            return
        sends_again = [
            name
            for name, on in (
                ("a `caption` operation", bool(self.caption)),
                ("`strip`", self.topics.strips),
                ("`reply: keep`", self.keep_replies),
                ("`links: rewrite`", self.rewrite_links),
            )
            if on
        ]
        if sends_again:
            raise TransformError(
                "forward_header: keep cannot go with "
                + ", ".join(sends_again)
                + ": the header only exists on a forward, and those send the message again"
            )

    @property
    def is_empty(self) -> bool:
        return (
            not self.caption
            and self.topics.is_empty
            and not self.keep_replies
            and not self.keep_forward_header
            and not self.rewrite_links
        )

    @property
    def rewrites_content(self) -> bool:
        """Some caption (or text) may be changed, which no forward does."""
        return bool(self.caption) or self.topics.strips

    @property
    def rewrites(self) -> bool:
        """Some message may have to be sent again to be changed (a forward cannot change one,
        nor give one a reply)."""
        return self.rewrites_content or self.keep_replies or self.rewrite_links

    @property
    def rewrites_text(self) -> bool:
        """Plain text messages are rewritten too, so they cannot be forwarded unchanged."""
        return self.text_too and self.rewrites_content

    @property
    def routes_to_topics(self) -> bool:
        """The destination has to be a forum: a message goes to a topic chosen by its hashtag."""
        return self.topics.routing

    # ---- the canonical form (what ``RunOptions.transform_json`` stores) -------------------------

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"version": FORMAT_VERSION}
        items: list[dict[str, Any]] = [_item_of(op) for op in self.caption]
        if self.text_too and self.rewrites_content:
            items.append({"text_too": True})
        if items:
            data["caption"] = items
        if not self.topics.is_empty:
            data["topics"] = self.topics.to_data()
        if self.keep_replies:
            data["reply"] = "keep"
        if self.keep_forward_header:
            data["forward_header"] = "keep"
        if self.rewrite_links:
            data["links"] = "rewrite"
        return data

    def to_json(self) -> str:
        """``""`` for no transform, so an unset option and an empty transform are the same."""
        if self.is_empty:
            return ""
        return json.dumps(self.to_data(), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Transform":
        if not text:
            return cls()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TransformError(f"stored transform is not valid JSON: {exc}") from None
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: Any) -> "Transform":
        """Validate parsed YAML/JSON. Raises ``TransformError`` naming the offending key."""
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise TransformError("a transform is a mapping (version, caption, topics)")
        unknown = data.keys() - _KEYS
        if unknown:
            raise TransformError(f"not a transform key: {', '.join(sorted(map(str, unknown)))}")
        version = data.get("version", FORMAT_VERSION)
        if version != FORMAT_VERSION:
            raise TransformError(f"transform format {version!r} is not supported")
        ops, text_too = _caption(data.get("caption"))
        topics = TopicRules.from_data(data.get("topics"))
        transform = cls(
            ops,
            text_too,
            topics,
            _reply(data.get("reply")),
            _forward_header(data.get("forward_header")),
            _links(data.get("links")),
        )
        if transform.text_too and any(isinstance(op, Clear) for op in ops):
            raise TransformError("caption: `clear` with `text_too` would empty every text message")
        return transform


def _caption(raw: Any) -> tuple[tuple[CaptionOp, ...], bool]:
    if raw is None:
        return (), False
    if not isinstance(raw, list):
        raise TransformError("caption: must be a list of operations")
    if len(raw) > MAX_OPERATIONS:
        raise TransformError(f"caption: at most {MAX_OPERATIONS} operations")
    ops: list[CaptionOp] = []
    text_too: bool | None = None
    for index, item in enumerate(raw):
        where = f"caption[{index}]"
        if not isinstance(item, dict) or len(item) != 1:
            raise TransformError(f"{where}: one operation per item, like `- strip_links: all`")
        ((name, value),) = item.items()
        if name == "text_too":
            if text_too is not None:
                raise TransformError(f"{where}: text_too given twice")
            if not isinstance(value, bool):
                raise TransformError(f"{where}.text_too: true or false")
            text_too = value
            continue
        ops.append(_operation(where, name, value))
    return tuple(ops), bool(text_too)


def _reply(raw: Any) -> bool:
    """``reply: keep`` (a reply answers the copy of its original) or ``drop`` (the default)."""
    if raw is None or raw == "drop":
        return False
    if raw == "keep":
        return True
    raise TransformError("reply: `keep` (answer the copy of the message) or `drop`")


def _forward_header(raw: Any) -> bool:
    """``forward_header: keep`` (forward with the source's name on it) or ``drop`` (the default)."""
    if raw is None or raw == "drop":
        return False
    if raw == "keep":
        return True
    raise TransformError("forward_header: `keep` (show where it was forwarded from) or `drop`")


def _links(raw: Any) -> bool:
    """``links: rewrite`` (links to messages of the source point at their copies) or ``keep``."""
    if raw is None or raw == "keep":
        return False
    if raw == "rewrite":
        return True
    raise TransformError("links: `rewrite` (point at the copies) or `keep` (the default)")


# ---- topics -------------------------------------------------------------------------------------


def _topic_title(where: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        raise TransformError(f"{where}: the name of a topic (one line of text)")
    title = value.strip()
    if len(title) > MAX_TOPIC_TITLE:
        raise TransformError(f"{where}: a topic name is at most {MAX_TOPIC_TITLE} characters")
    return title


def _title_of_tag(tag: str) -> str:
    """The topic ``auto`` names after a hashtag: ``#machine_learning`` -> ``Machine learning``."""
    name = tag.lstrip("#").replace("_", " ").strip()
    return (name[:1].upper() + name[1:])[:MAX_TOPIC_TITLE]


def _pairs(where: str, value: Any) -> list[tuple[Any, Any]]:
    """A mapping, or the list of one-key mappings the canonical form uses (order is a priority)."""
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        pairs: list[tuple[Any, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict) or len(item) != 1:
                raise TransformError(f'{where}[{index}]: one `"#tag": "Topic"` per item')
            pairs.extend(item.items())
        return pairs
    raise TransformError(f'{where}: a mapping like `"#python": "Python"`')


@dataclass(frozen=True, slots=True)
class _Routing:
    routes: tuple[HashtagRoute, ...] = ()
    auto_all: bool = False
    limit: int = DEFAULT_AUTO_LIMIT
    skip: tuple[str, ...] = ()
    default: str | None = None
    strip: bool = False


def _routing(raw: Any) -> _Routing:
    where = "topics.hashtag_to_topic"
    if not isinstance(raw, dict):
        raise TransformError(f"{where}: a mapping (map, auto, default, strip, limit, skip)")
    unknown = raw.keys() - _ROUTING_KEYS
    if unknown:
        raise TransformError(f"{where}: not a key: {', '.join(sorted(map(str, unknown)))}")
    routes: list[HashtagRoute] = []
    seen: set[str] = set()

    def add(tag_where: str, tag: Any, topic: str) -> None:
        try:
            normal = normalize_hashtag(str(tag))
        except ValueError as exc:
            raise TransformError(f"{tag_where}: {tag!r}: {exc}") from None
        if normal in seen:
            raise TransformError(f"{tag_where}: {normal} appears twice")
        seen.add(normal)
        routes.append(HashtagRoute(normal, topic))

    if (explicit := raw.get("map")) is not None:
        for tag, topic in _pairs(f"{where}.map", explicit):
            add(f"{where}.map", tag, _topic_title(f"{where}.map[{tag}]", topic))
    auto_all = False
    if (auto := raw.get("auto")) is not None:
        if auto is True or auto == "all":
            auto_all = True
        else:
            if isinstance(auto, str):
                auto = [auto]
            if not isinstance(auto, list) or not auto:
                raise TransformError(f"{where}.auto: `all`, or a list of hashtags")
            for tag in auto:
                try:
                    normal = normalize_hashtag(str(tag))
                except ValueError as exc:
                    raise TransformError(f"{where}.auto: {tag!r}: {exc}") from None
                title = _topic_title(f"{where}.auto", _title_of_tag(normal))
                add(f"{where}.auto", normal, title)
    if not routes and not auto_all:
        raise TransformError(f"{where}: give `map` or `auto` (a list of hashtags, or `all`)")
    if len(routes) > MAX_TOPIC_RULES:
        raise TransformError(f"{where}: at most {MAX_TOPIC_RULES} hashtags")
    limit = raw.get("limit", DEFAULT_AUTO_LIMIT)
    if "limit" in raw and not auto_all:
        raise TransformError(f"{where}.limit: only means something with `auto: all`")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_AUTO_LIMIT:
        raise TransformError(f"{where}.limit: a number from 1 to {MAX_AUTO_LIMIT}")
    skip: tuple[str, ...] = ()
    if (raw_skip := raw.get("skip")) is not None:
        if not auto_all:
            raise TransformError(f"{where}.skip: only means something with `auto: all`")
        tags = [raw_skip] if isinstance(raw_skip, str) else raw_skip
        if not isinstance(tags, list) or not tags:
            raise TransformError(f"{where}.skip: a list of hashtags")
        try:
            skip = tuple(dict.fromkeys(normalize_hashtag(str(t)) for t in tags))
        except ValueError as exc:
            raise TransformError(f"{where}.skip: {exc}") from None
    default = raw.get("default")
    if default is not None:
        default = _topic_title(f"{where}.default", default)
    strip = raw.get("strip", False)
    if not isinstance(strip, bool):
        raise TransformError(f"{where}.strip: true or false")
    return _Routing(tuple(routes), auto_all, limit, skip, default, strip)


def _renames(raw: Any) -> tuple[tuple[int | str, str], ...]:
    if raw is None:
        return ()
    where = "topics.topic_map"
    entries: list[tuple[Any, Any]] = []
    if isinstance(raw, dict):
        entries = list(raw.items())
    elif isinstance(raw, list):  # the canonical form: {id: N, to: ...} or {title: ..., to: ...}
        for index, item in enumerate(raw):
            ok = isinstance(item, dict) and "to" in item and len(item) == 2
            if not ok or not ({"id", "title"} & item.keys()):
                raise TransformError(f"{where}[{index}]: {{id or title: ..., to: ...}}")
            entries.append((item["id"] if "id" in item else item["title"], item["to"]))
    else:
        raise TransformError(f'{where}: a mapping like `"Old name": "New name"`')
    if len(entries) > MAX_TOPIC_RULES:
        raise TransformError(f"{where}: at most {MAX_TOPIC_RULES} topics")
    renames: list[tuple[int | str, str]] = []
    seen: set[int | str] = set()
    for key, name in entries:
        if name is None:
            raise TransformError(
                f"{where}[{key}]: a topic cannot be dropped here, exclude it with a filter"
            )
        title = _topic_title(f"{where}[{key}]", name)
        if isinstance(key, bool) or not isinstance(key, int | str):
            raise TransformError(f"{where}: {key!r} is neither a topic id nor a topic title")
        folded: int | str
        if isinstance(key, int):
            if key <= GENERAL_TOPIC_ID:
                raise TransformError(
                    f"{where}[{key}]: General has no topic id of its own; its messages cannot be "
                    "renamed"
                )
            folded = key
        else:
            folded = key.strip().casefold()
            if not folded:
                raise TransformError(f"{where}: a topic title is missing")
        if folded in seen:
            raise TransformError(f"{where}: {key!r} appears twice")
        seen.add(folded)
        renames.append((folded, title))
    return _in_canonical_order(dict(renames))


def _in_canonical_order(renames: Mapping[int | str, str]) -> tuple[tuple[int | str, str], ...]:
    """A topic_map has no order: sorted, so equal maps are equal and one canonical JSON."""
    return tuple(sorted(renames.items(), key=lambda r: (isinstance(r[0], str), r[0])))


def _hashtag_format(raw: Any) -> str | None:
    if raw is None:
        return None
    where = "topics.hashtag_format"
    if not isinstance(raw, str) or "{slug}" not in raw:
        raise TransformError(f'{where}: text with {{slug}} in it, like "#topic_{{slug}}"')
    try:
        sample = raw.format(slug="x")
    except (KeyError, IndexError, ValueError):
        raise TransformError(f"{where}: only {{slug}} may be a placeholder") from None
    if not _HASHTAG_FORMAT_SAMPLE.fullmatch(sample):
        raise TransformError(f"{where}: must give one hashtag, a # then word characters only")
    return raw


def _item_of(op: CaptionOp) -> dict[str, Any]:
    match op:
        case StripLinks():
            return {"strip_links": op.scope}
        case RemoveHashtags():
            return {"remove_hashtags": list(op.tags) if op.tags else "all"}
        case RemoveMentions():
            return {"remove_mentions": op.scope}
        case Replace():
            return {"replace": {"regex": op.pattern, "with": op.with_}}
        case Append():
            return {"append": op.text}
        case Clear():
            return {"clear": True}
    raise AssertionError(op)  # pragma: no cover


def _operation(where: str, name: Any, value: Any) -> CaptionOp:
    where = f"{where}.{name}"
    if name not in _OPERATIONS:
        raise TransformError(
            f"{where}: not an operation (one of {', '.join(sorted(_OPERATIONS - {'text_too'}))})"
        )
    if name in ("strip_links", "remove_mentions"):
        if value not in _SCOPES:
            raise TransformError(f"{where}: source or all")
        return StripLinks(value) if name == "strip_links" else RemoveMentions(value)
    if name == "remove_hashtags":
        return RemoveHashtags(_hashtags(where, value))
    if name == "replace":
        return _replace(where, value)
    if name == "append":
        if not isinstance(value, str) or not value.strip():
            raise TransformError(f"{where}: the text to add")
        return Append(value)
    # clear
    if value is not True:
        raise TransformError(f"{where}: write `clear: true`")
    return Clear()


def _hashtags(where: str, value: Any) -> tuple[str, ...]:
    if value == "all":
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise TransformError(f'{where}: a list of hashtags, or "all"')
    tags: list[str] = []
    for tag in value:
        try:
            tags.append(normalize_hashtag(str(tag)))
        except ValueError as exc:
            raise TransformError(f"{where}: {tag!r}: {exc}") from None
    return tuple(dict.fromkeys(tags))


def _replace(where: str, value: Any) -> Replace:
    if not isinstance(value, dict) or "regex" not in value or value.keys() - {"regex", "with"}:
        raise TransformError(f"{where}: {{regex: ..., with: ...}}")
    pattern, with_ = value["regex"], value.get("with", "")
    if not isinstance(pattern, str) or not pattern:
        raise TransformError(f"{where}.regex: a pattern")
    if not isinstance(with_, str):
        raise TransformError(f"{where}.with: text")
    try:
        regex.compile(pattern)
    except regex.error as exc:
        raise TransformError(f"{where}.regex: {exc}") from None
    return Replace(pattern, with_)


def load(path: Path) -> Transform:
    """A ``--transform`` file. (The ``transform:`` section of a selection file is read by
    ``engine/selection.py``, with ``Transform.from_data``.)"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TransformError(f"cannot read {path}: {exc}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or str(exc)
        hint = ' Hashtags need quotes ("#news").' if "#" in text else ""
        raise TransformError(f"{path} is not valid YAML: {problem}.{hint}") from None
    try:
        return Transform.from_data(data)
    except TransformError as exc:
        raise TransformError(f"{path}: {exc.detail}") from None
