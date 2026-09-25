"""The filter model: what a clone copies (docs/03-filters.md).

Pydantic models that validate and *normalise* on the way in (hashtags lower-cased with ``#``,
sizes in bytes, dates in UTC, regexes compiled once to prove they are valid), so what ``to_json``
stores in ``mirrors.filters_json`` is canonical and the matcher never has to second-guess it. Stored
JSON must keep loading: add fields with defaults, never rename (skill ``filter-dsl``).
"""

import json
import re
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Self

import regex
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    ValidationError,
    field_validator,
    model_validator,
)

from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import MediaKind


class FilterError(TgMirrorError):
    """A filter is invalid or unreadable. ``detail`` says which part and why."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# ---- value parsers --------------------------------------------------------------------------

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]?)i?B\s*$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)([smh])")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600}


def parse_size(value: Any) -> int:
    """``500MB`` / ``2GB`` / ``1.5 GiB`` to bytes (1 KB = 1024 B). A bare number is ambiguous."""
    if isinstance(value, str) and (m := _SIZE.match(value)):
        return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).upper()])
    if isinstance(value, str):
        raise ValueError(f"{value!r} is not a size like 500MB or 2GB")
    raise ValueError("a size needs a unit, e.g. 500MB or 2GB")


def parse_duration(value: Any) -> float:
    """Seconds as a number, or ``90s`` / ``5m`` / ``1h30m``."""
    if isinstance(value, bool):
        raise ValueError("a duration is a number of seconds or text like 5m")
    if isinstance(value, int | float):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip().lower().replace(" ", "")
        parts = _DURATION_PART.findall(text)
        if not parts or "".join(n + u for n, u in parts) != text:
            raise ValueError(f"{value!r} is not a duration like 90s, 5m or 1h30m")
        seconds = sum(float(n) * _DURATION_UNITS[u] for n, u in parts)
    else:
        raise ValueError("a duration is a number of seconds or text like 5m")
    if seconds < 0:
        raise ValueError("a duration cannot be negative")
    return seconds


def parse_moment(value: Any) -> datetime:
    """A date or datetime (or its ISO text) as an aware UTC datetime; naive means UTC."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            raise ValueError(f"{value!r} is not a date like 2024-01-01") from None
    else:
        raise ValueError("a date looks like 2024-01-01")
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _listify(value: Any) -> Any:
    """``media: video`` (or a single id, e.g. ``topic: 7``) is a one-item list."""
    scalar = isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))
    return [value] if scalar else value


def normalize_hashtag(tag: str) -> str:
    """``Foo`` / ``#Foo`` to ``#foo``."""
    tag = tag.strip().lstrip("#").casefold()
    if not tag or any(c.isspace() or c == "#" for c in tag):
        raise ValueError("a hashtag is a single word like #news")
    return "#" + tag


# Stored as ``"<bytes>B"``: a bare number is rejected on input, so the canonical form must be
# text with a unit to load again.
Size = Annotated[
    int,
    BeforeValidator(parse_size),
    Field(ge=0),
    PlainSerializer(lambda size: f"{size}B", return_type=str),
]
Duration = Annotated[float, BeforeValidator(parse_duration)]
Moment = Annotated[datetime, BeforeValidator(parse_moment)]
Names = Annotated[tuple[str, ...], BeforeValidator(_listify), Field(min_length=1)]
# ``from_user``/``topic`` (phase 8): Telegram ids only in v1, never a username or topic name, so
# the matcher stays pure I/O-free (docs/01-kien-truc.md); ``tgmirror topics`` looks up the id.
Ids = Annotated[
    tuple[Annotated[int, Field(gt=0)], ...], BeforeValidator(_listify), Field(min_length=1)
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# ---- ranges ---------------------------------------------------------------------------------


class _Bounds(_Model):
    """``{min, max}`` with both ends optional, at least one given, ``min <= max``."""

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        low, high = getattr(self, "min", None), getattr(self, "max", None)
        if low is None and high is None:
            raise ValueError("give min, max or both")
        if low is not None and high is not None and low > high:
            raise ValueError("min is larger than max")
        return self


class SizeRange(_Bounds):
    min: Size | None = None
    max: Size | None = None


class DurationRange(_Bounds):
    min: Duration | None = None
    max: Duration | None = None


class CountRange(_Bounds):
    min: Annotated[int, Field(ge=0)] | None = None
    max: Annotated[int, Field(ge=0)] | None = None


class DateRange(_Model):
    """``from`` included, ``to`` excluded (so a year is ``2024-01-01`` to ``2025-01-01``); UTC."""

    from_: Moment | None = Field(None, alias="from")
    to: Moment | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.from_ is None and self.to is None:
            raise ValueError("give from, to or both")
        if self.from_ is not None and self.to is not None and self.from_ >= self.to:
            raise ValueError("from must be before to")
        return self


class IdRange(_Model):
    """Message ids, both ends included."""

    from_: Annotated[int, Field(ge=1)] | None = Field(None, alias="from")
    to: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.from_ is None and self.to is None:
            raise ValueError("give from, to or both")
        if self.from_ is not None and self.to is not None and self.from_ > self.to:
            raise ValueError("from is larger than to")
        return self


# ---- rules ----------------------------------------------------------------------------------


class Rule(_Model):
    """Predicates that must all hold for one message (AND). ``hashtag``, ``contains``, ``media``
    and ``mime`` are any-of lists."""

    media: (
        Annotated[tuple[MediaKind, ...], BeforeValidator(_listify), Field(min_length=1)] | None
    ) = None
    hashtag: Names | None = None
    contains: Names | None = None
    regex: str | None = None
    has_caption: bool | None = None
    size: SizeRange | None = None
    duration: DurationRange | None = None
    mime: Names | None = None
    views: CountRange | None = None
    from_user: Ids | None = None  # group/forum sources only (phase 8)
    topic: Ids | None = None  # forum sources only (phase 8)

    @field_validator("from_user", "topic")
    @classmethod
    def _dedup_ids(cls, ids: tuple[int, ...] | None) -> tuple[int, ...] | None:
        return None if ids is None else tuple(dict.fromkeys(ids))

    @field_validator("hashtag")
    @classmethod
    def _hashtags(cls, tags: tuple[str, ...] | None) -> tuple[str, ...] | None:
        return None if tags is None else tuple(dict.fromkeys(normalize_hashtag(t) for t in tags))

    @field_validator("contains")
    @classmethod
    def _needles(cls, needles: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if needles is not None and any(not n.strip() for n in needles):
            raise ValueError("a keyword cannot be empty")
        return needles

    @field_validator("mime")
    @classmethod
    def _mimes(cls, mimes: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if mimes is None:
            return None
        if any("/" not in m for m in mimes):
            raise ValueError("a mime type looks like video/mp4 (or video/*)")
        return tuple(dict.fromkeys(m.strip().lower() for m in mimes))

    @field_validator("regex")
    @classmethod
    def _valid_regex(cls, pattern: str | None) -> str | None:
        if pattern is not None:
            try:
                regex.compile(pattern)
            except regex.error as exc:
                raise ValueError(f"not a valid regular expression: {exc}") from None
        return pattern

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("a rule needs at least one predicate")
        return self


AlbumMode = Literal["any", "all", "first"]


class FilterSpec(_Model):
    """Everything that decides whether a unit is cloned. An empty spec clones everything."""

    include: tuple[Rule, ...] = ()
    exclude: tuple[Rule, ...] = ()
    when: DateRange | None = Field(None, alias="date")
    ids: IdRange | None = Field(None, alias="id")
    album: AlbumMode = "any"

    @property
    def is_empty(self) -> bool:
        return not (self.include or self.exclude or self.when or self.ids)

    def to_json(self) -> str:
        """The canonical form stored in ``mirrors.filters_json`` (``{}`` for no filter)."""
        data = self.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Self:
        try:
            return cls.from_data(json.loads(text))
        except json.JSONDecodeError as exc:
            raise FilterError(f"stored filter is not valid JSON: {exc}") from None

    @classmethod
    def from_data(cls, data: Any) -> Self:
        """Validate parsed YAML/JSON. Raises ``FilterError`` naming the offending key."""
        if data is None:
            data = {}
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise FilterError(explain(exc)) from None


def explain(exc: ValidationError) -> str:
    """One line per problem: ``include[0].media[1]: 'vidoe' is not ...``."""
    lines = []
    for err in exc.errors(include_url=False):
        where = ""
        for part in err["loc"]:
            where += f"[{part}]" if isinstance(part, int) else f".{part}"
        where = where.lstrip(".")
        if err["type"] == "extra_forbidden":
            msg = "not a filter key"
        else:
            msg = err["msg"].removeprefix("Value error, ")
        lines.append(f"{where}: {msg}" if where else msg)
    return "; ".join(lines)
