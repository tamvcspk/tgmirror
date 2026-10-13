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

Phase 19 arrives in slices (docs/06-lo-trinh.md): this one is the caption operations. The keys of
the other slices (topics, replies, forward header, links) are reserved and refused for now rather
than half used.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regex
import yaml

from tgmirror.core.gateway import (
    Append,
    CaptionOp,
    Clear,
    RemoveHashtags,
    RemoveMentions,
    Replace,
    StripLinks,
)
from tgmirror.filters.model import FilterError, normalize_hashtag

FORMAT_VERSION = 1
MAX_OPERATIONS = 50
_SCOPES = ("source", "all")
# keys of later slices of phase 19: refused with their own message, never silently ignored
_RESERVED = frozenset(
    {"topics", "hashtag_to_topic", "topic_map", "reply", "forward_header", "links"}
)
_KEYS = frozenset({"version", "caption"})
_OPERATIONS = frozenset(
    {"strip_links", "remove_hashtags", "remove_mentions", "replace", "append", "clear", "text_too"}
)


class TransformError(FilterError):
    """A transform cannot be used. ``detail`` says which part and why (exit code 2)."""


@dataclass(frozen=True, slots=True)
class Transform:
    """The operations of a transform. Empty (the default) changes nothing."""

    caption: tuple[CaptionOp, ...] = ()
    text_too: bool = False  # rewrite the text of plain text messages too, not only media captions

    @property
    def is_empty(self) -> bool:
        return not self.caption

    @property
    def rewrites_text(self) -> bool:
        """Plain text messages are rewritten too, so they cannot be forwarded unchanged."""
        return self.text_too and not self.is_empty

    # ---- the canonical form (what ``RunOptions.transform_json`` stores) -------------------------

    def to_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"version": FORMAT_VERSION}
        if self.is_empty:
            return data
        items: list[dict[str, Any]] = [_item_of(op) for op in self.caption]
        if self.text_too:
            items.append({"text_too": True})
        data["caption"] = items
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
            raise TransformError("a transform is a mapping (version, caption)")
        reserved = sorted(data.keys() & _RESERVED)
        if reserved:
            raise TransformError(
                f"{', '.join(reserved)}: not available yet (a later part of phase 19)"
            )
        unknown = data.keys() - _KEYS
        if unknown:
            raise TransformError(f"not a transform key: {', '.join(sorted(map(str, unknown)))}")
        version = data.get("version", FORMAT_VERSION)
        if version != FORMAT_VERSION:
            raise TransformError(f"transform format {version!r} is not supported")
        raw = data.get("caption")
        if raw is None:
            return cls()
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
        transform = cls(tuple(ops), bool(text_too))
        if transform.text_too and any(isinstance(op, Clear) for op in ops):
            raise TransformError("caption: `clear` with `text_too` would empty every text message")
        return transform


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
