"""Selection files (docs/07-inspect-transform.md, phase 18a): a filter that outlives one command.

``tgmirror inspect`` is where a filter is tried, adjusted and counted; ``clone``/``backup``/
``restore`` are where it is used once. A selection file carries it from one to the other —
readable, editable, and shareable — instead of a hidden "current selection" in the program:

::

    version: 1
    source: {id: -1001234567890, title: "Channel A"}   # or {backup: ./backups/channel_a}
    filter:                                            # the language of docs/03-filters.md
      include: [{media: [video], hashtag: ["#tutorial"]}]
    snapshot:                                          # only when frozen
      taken_at: 2026-10-12T10:31:00+00:00
      upto_id: 58211
      count: 412
      ids_file: chosen.ids                             # one id a line; delete a line = drop it

A file with none of ``version``/``filter``/``source``/``snapshot`` is a plain ``--filter-file``
and is read as one, so nothing written before this phase needs migrating.

Pure I/O and (de)serialisation, like ``engine/backupdir.py``. Not a job and not remembered by the
program: the one exception is ``last-<source id>.yaml``, the file ``inspect`` overwrites on each
complete scan so ``--selection @last`` has something to name.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from tgmirror.engine.transform import Transform, TransformError
from tgmirror.filters.model import FilterError, FilterSpec
from tgmirror.filters.parser import FlagFilters, from_flags

FORMAT_VERSION = 1
LAST = "@last"
IDS_SUFFIX = ".ids"
_SELECTION_KEYS = frozenset({"version", "filter", "source", "snapshot", "transform"})
_SAFE_ARG = re.compile(r"[A-Za-z0-9_@%+=:,./\\-]+")


class SelectionError(FilterError):
    """A selection file cannot be used. ``detail`` says why (exit code 2, like a bad filter)."""


@dataclass(frozen=True, slots=True)
class SelectionSource:
    """Where the filter was tried: a channel (``id`` is what counts, ``title`` is for the reader)
    or a backup directory."""

    id: int | None = None
    title: str | None = None
    backup: str | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The messages that were selected at one moment, kept as a list of ids."""

    taken_at: datetime
    upto_id: int  # the highest message id read: what the list is complete up to
    count: int
    ids: tuple[int, ...] = ()
    ids_file: str | None = None  # next to the YAML; ``ids`` is what it held when loaded


@dataclass(frozen=True, slots=True)
class Selection:
    filters: FilterSpec
    source: SelectionSource | None = None
    snapshot: Snapshot | None = None
    transform: Transform = Transform()  # phase 19: what the commands that take one also apply


def last_path(selections_dir: Path, src_id: int) -> Path:
    return selections_dir / f"last-{src_id}.yaml"


@dataclass(frozen=True, slots=True)
class LastDiscovery:
    """What the wizard offers as "use the latest result of ``inspect``": its filter and how many
    messages it selected, when."""

    filters: FilterSpec
    count: int
    taken_at: datetime


def last_discovery(selections_dir: Path, src_id: int | None) -> LastDiscovery | None:
    """``@last`` of ``src_id`` for the wizard, or ``None`` when there is none or it cannot be read
    (a wizard never fails because an old file did)."""
    if src_id is None:
        return None
    try:
        loaded = load(last_path(selections_dir, src_id))
    except (FilterError, OSError):
        return None
    snap = loaded.snapshot
    if snap is None:
        return None
    return LastDiscovery(loaded.filters, snap.count, snap.taken_at)


# ---- writing ----------------------------------------------------------------------------------


def _to_data(selection: Selection, *, ids_file: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {"version": FORMAT_VERSION}
    if selection.source is not None:
        src: dict[str, Any] = {}
        if selection.source.id is not None:
            src["id"] = selection.source.id
        if selection.source.title:
            src["title"] = selection.source.title
        if selection.source.backup:
            src["backup"] = selection.source.backup
        data["source"] = src
    data["filter"] = yaml.safe_load(selection.filters.to_json()) or {}
    if not selection.transform.is_empty:
        data["transform"] = selection.transform.to_data()
    if (snap := selection.snapshot) is not None:
        data["snapshot"] = {
            "taken_at": snap.taken_at.astimezone(UTC).isoformat(timespec="seconds"),
            "upto_id": snap.upto_id,
            "count": snap.count,
            **({"ids_file": ids_file} if ids_file else {}),
        }
    return data


def dump(selection: Selection, path: Path, *, now: datetime | None = None) -> None:
    """Write ``path`` (and, for a snapshot with ids, the ``.ids`` file beside it).

    Both files are written to a temporary name and renamed, the ids first: a crash can leave a
    new ``.ids`` next to the old YAML, never a YAML that names a missing list.
    """
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    path.parent.mkdir(parents=True, exist_ok=True)
    ids_name: str | None = None
    if selection.snapshot is not None and selection.snapshot.ids:
        ids_name = path.with_suffix(IDS_SUFFIX).name
        ids_tmp = path.with_suffix(IDS_SUFFIX + ".tmp")
        ids_tmp.write_text("".join(f"{i}\n" for i in selection.snapshot.ids), encoding="utf-8")
        ids_tmp.replace(path.with_suffix(IDS_SUFFIX))
    body = yaml.safe_dump(
        _to_data(selection, ids_file=ids_name), sort_keys=False, allow_unicode=True
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"# tgmirror selection - made by `tgmirror inspect` {stamp}\n{body}", "utf-8")
    tmp.replace(path)


# ---- reading ----------------------------------------------------------------------------------


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SelectionError(f"cannot read {path}: {exc}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or str(exc)
        hint = ' Hashtags need quotes ("#news").' if "#" in text else ""
        raise SelectionError(f"{path} is not valid YAML: {problem}.{hint}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SelectionError(f"{path} must hold a mapping")
    return data


def is_selection_file(data: dict[str, Any]) -> bool:
    return bool(_SELECTION_KEYS & data.keys())


def load(path: Path, *, with_ids: bool = False) -> Selection:
    """A selection file, or a plain filter file read as a selection with no source/snapshot.

    ``with_ids`` also reads the snapshot's ids (they can be many, and only ``--exact`` needs them).
    Anything this version does not understand is refused (``version`` newer, a part of a
    transform it does not have yet, or an unknown key): a file must not be half-used. A
    ``transform`` is read here; whether the command taking the file can apply one is that
    command's to say (``cli/selection_options.py``).
    """
    data = _read_mapping(path)
    if not is_selection_file(data):
        try:
            return Selection(FilterSpec.from_data(data))
        except FilterError as exc:
            raise SelectionError(f"{path}: {exc.detail}") from None
    unknown = data.keys() - _SELECTION_KEYS
    if unknown:
        raise SelectionError(f"{path}: not a selection key: {', '.join(sorted(map(str, unknown)))}")
    version = data.get("version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        raise SelectionError(f"{path}: selection format {version!r} is not supported")
    try:
        filters = FilterSpec.from_data(data.get("filter"))
    except FilterError as exc:
        raise SelectionError(f"{path}: filter: {exc.detail}") from None
    try:
        transform = Transform.from_data(data.get("transform"))
    except TransformError as exc:
        raise SelectionError(f"{path}: transform: {exc.detail}") from None
    return Selection(
        filters,
        _source_of(path, data.get("source")),
        _snapshot_of(path, data, with_ids),
        transform,
    )


def _source_of(path: Path, raw: Any) -> SelectionSource | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.keys() - {"id", "title", "backup"}:
        raise SelectionError(f"{path}: source takes id, title or backup")
    ident = raw.get("id")
    if ident is not None and (not isinstance(ident, int) or isinstance(ident, bool)):
        raise SelectionError(f"{path}: source.id must be a number")
    title, backup = raw.get("title"), raw.get("backup")
    return SelectionSource(ident, None if title is None else str(title), backup and str(backup))


def _snapshot_of(path: Path, data: dict[str, Any], with_ids: bool) -> Snapshot | None:
    raw = data.get("snapshot")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SelectionError(f"{path}: snapshot must be a mapping")
    try:
        taken = raw["taken_at"]
        taken_at = taken if isinstance(taken, datetime) else datetime.fromisoformat(str(taken))
        upto, count = int(raw["upto_id"]), int(raw["count"])
    except (KeyError, ValueError, TypeError) as exc:
        raise SelectionError(
            f"{path}: snapshot needs taken_at, upto_id and count ({exc})"
        ) from None
    ids_file = raw.get("ids_file")
    ids: tuple[int, ...] = ()
    if with_ids:
        if not ids_file:
            raise SelectionError(f"{path}: this selection has no list of ids (it was not frozen)")
        ids = _read_ids(path.parent / str(ids_file))
    return Snapshot(
        taken_at if taken_at.tzinfo else taken_at.replace(tzinfo=UTC),
        upto,
        count,
        ids,
        None if ids_file is None else str(ids_file),
    )


def _read_ids(path: Path) -> tuple[int, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise SelectionError(f"cannot read the list of ids {path}: {exc}") from None
    found: list[int] = []
    for number, line in enumerate(lines, 1):
        text = line.split("#", 1)[0].strip()  # a hand-edited list may carry comments
        if not text:
            continue
        if not text.isdigit() or int(text) < 1:
            raise SelectionError(f"{path}: line {number} is not a message id")
        found.append(int(text))
    if not found:
        raise SelectionError(f"{path} holds no message ids")
    return tuple(sorted(set(found)))


def exact(selection: Selection, *, path: Path | None = None) -> FilterSpec:
    """The filter that takes exactly the snapshot's messages (``--exact``). ``selection`` must
    have been loaded ``with_ids``."""
    snap = selection.snapshot
    where = f"{path}: " if path is not None else ""
    if snap is None or not snap.ids:
        raise SelectionError(f"{where}--exact needs a frozen selection (inspect --freeze)")
    return FilterSpec.from_data({"message_ids": list(snap.ids)})


# ---- filter -> flags ---------------------------------------------------------------------------

_RULE_KEYS = frozenset(
    {
        "media",
        "hashtag",
        "contains",
        "regex",
        "from_user",
        "topic",
        "size",
        "forwarded",
        "forwarded_from",
        "link",
    }
)


def _flags_of(spec: FilterSpec) -> FlagFilters | None:
    """The flags a spec would be typed as, or ``None`` when some part has no flag."""
    if spec.message_ids is not None or spec.ids is not None or len(spec.include) > 1:
        return None
    values: dict[str, Any] = {}
    if spec.include:
        rule = spec.include[0].model_dump(mode="json", exclude_none=True)
        if rule.keys() - _RULE_KEYS:
            return None
        for key in ("hashtag", "contains", "regex", "from_user", "topic", "forwarded_from"):
            if key in rule:
                values[key] = rule[key]
        if "media" in rule:
            values["media"] = ",".join(rule["media"])
        if "forwarded" in rule:
            values["forwarded"] = rule["forwarded"]
        if "size" in rule:
            values["min_size"] = rule["size"].get("min")
            values["max_size"] = rule["size"].get("max")
        if "link" in rule:
            values["link_domain" if rule["link"] else "has_link"] = rule["link"] or True
    regexes: list[str] = []
    for exclude in (r.model_dump(mode="json", exclude_none=True) for r in spec.exclude):
        if exclude.keys() == {"regex"}:
            regexes.append(exclude["regex"])
        elif exclude.keys() == {"media"} and "exclude_media" not in values:
            values["exclude_media"] = ",".join(exclude["media"])
        else:
            return None
    if regexes:
        values["exclude_regex"] = regexes
    if spec.when is not None:
        if spec.when.from_ is not None:
            values["since"] = spec.when.from_.date().isoformat()
        if spec.when.to is not None:
            values["until"] = spec.when.to.date().isoformat()
    if spec.album != "any":
        values["album"] = spec.album
    return FlagFilters(**values)


def _render(flags: FlagFilters) -> list[str]:
    tokens: list[str] = []

    def one(flag: str, value: object) -> None:
        if value is not None:
            tokens.extend([flag, str(value)])

    def many(flag: str, values: Sequence[object]) -> None:
        for value in values:
            tokens.extend([flag, str(value)])

    one("--media", flags.media)
    many("--hashtag", flags.hashtag)
    many("--contains", flags.contains)
    one("--regex", flags.regex)
    many("--exclude-regex", flags.exclude_regex)
    one("--exclude-media", flags.exclude_media)
    one("--since", flags.since)
    one("--until", flags.until)
    one("--min-size", flags.min_size)
    one("--max-size", flags.max_size)
    one("--album", flags.album)
    many("--from-user", flags.from_user)
    many("--topic", flags.topic)
    if flags.forwarded is not None:
        tokens.append("--forwarded" if flags.forwarded else "--not-forwarded")
    many("--forwarded-from", flags.forwarded_from)
    if flags.has_link:
        tokens.append("--has-link")
    many("--link-domain", flags.link_domain)
    return tokens


def to_flags(spec: FilterSpec) -> list[str] | None:
    """The CLI flags that mean ``spec``, or ``None`` when the flags cannot say it (several include
    rules, a ``mime`` predicate, a frozen list, ...). Always checked by building the spec back from
    the flags: what is returned is exactly ``spec``, never a near miss."""
    flags = _flags_of(spec)
    if flags is None:
        return None
    try:
        return _render(flags) if from_flags(flags) == spec else None
    except FilterError:
        return None


def quote(token: str) -> str:
    """A token as a shell argument that reads the same in bash and PowerShell."""
    if _SAFE_ARG.fullmatch(token):
        return token
    return '"' + token.replace('"', '\\"') + '"'
