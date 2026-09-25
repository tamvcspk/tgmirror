"""Configuration: ``config.toml`` plus environment overrides (docs/02-cli-ux.md, "Config").

The numbers in ``Limits`` are conservative starting points, not documented Telegram limits
(docs/05-chong-flood.md).
"""

import contextlib
import json
import os
import re
import tomllib
from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from tgmirror.core.errors import ConfigError
from tgmirror.core.paths import Paths
from tgmirror.core.secrets import (
    keyring_backend_label,
    keyring_usable,
    resolve_credentials,
    write_keyring,
)

_CREDENTIAL_LINE = re.compile(r"\s*(api_id|api_hash)\s*=")


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_size: int = Field(20, ge=1, le=100)
    min_delay: float = Field(2.0, gt=0)
    max_delay: float = Field(60.0, gt=0)
    read_delay: float = Field(0.5, gt=0)  # floor between read requests; grows with the write delay
    jitter: float = Field(0.3, ge=0, lt=1)
    long_pause_every: int = Field(200, ge=1)
    long_pause_range: tuple[float, float] = (30.0, 90.0)
    daily_cap: int = Field(5000, ge=1)
    max_auto_wait: float = Field(900.0, ge=0)
    # units downloaded ahead while one uploads (0 = none). Default 0: measuring several files'
    # parts moving at once over the production connection shape (a small set of connections made
    # once and reused, not one set per file) found no gain and an earlier, harder break than doing
    # one unit at a time — see docs/06-lo-trinh.md, "chạy lại với hình dạng đúng" (2026-09-22).
    prefetch: int = Field(0, ge=0, le=3)
    tmp_budget_mb: int = Field(2048, ge=1)  # disk the downloaded-ahead files may take together
    # File transfers of strategy B (docs/05): requests in flight at once, one budget for downloads
    # and a separate one for uploads (0 = one request at a time, Telethon's own way; each budget
    # starts at 2 and grows toward its ceiling while things go well). They used to be one shared
    # number: 8/8 (one request per connection), one file at a time with no download running
    # alongside, measured clean twice in a row (~24-28 MB/s upload) — but a real ``--mode reupload``
    # run (2026-09-23) hit repeated transport 429s and dead connections downloading at 8, something
    # the earlier bench never tried (single file, no real network conditions). Lowered downloads to
    # 4 on that evidence; uploads stay at 8, unaffected by the same run.
    download_requests: int = Field(4, ge=0, le=16)
    upload_requests: int = Field(8, ge=0, le=16)
    upload_connections: int = Field(8, ge=1, le=16)  # connections uploads are spread over
    pool_min_mb: int = Field(10, ge=1)  # smaller files keep Telethon's own transfer

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        if self.max_delay < self.min_delay:
            raise ValueError("max_delay must be >= min_delay")
        low, high = self.long_pause_range
        if not 0 <= low <= high:
            raise ValueError("long_pause_range must be [low, high] with 0 <= low <= high")
        return self


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_id: int | None = None
    api_hash: SecretStr | None = None  # SecretStr keeps it out of repr()/logs (hard rule 6)
    limits: Limits = Limits()


LIMIT_KEYS: tuple[str, ...] = tuple(Limits.model_fields)  # ``tgmirror config``'s editable keys
_LIMITS_TABLE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]")


def _read_toml(paths: Paths) -> dict[str, object]:
    if not paths.config_file.exists():
        return {}
    try:
        with paths.config_file.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {paths.config_file}: {exc}") from exc


def load_config(paths: Paths, env: Mapping[str, str] | None = None) -> Config:
    """``config.toml`` merged with every other place ``api_id``/``api_hash`` can live — env vars,
    ``*_FILE``, the OS keyring — highest priority first (``core.secrets``, Phase 9)."""
    env = os.environ if env is None else env
    data = dict(_read_toml(paths))
    creds = resolve_credentials(
        env, config_api_id=data.get("api_id"), config_api_hash=data.get("api_hash")
    )
    data["api_id"] = creds.api_id
    data["api_hash"] = creds.api_hash

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        # Report locations and messages only: pydantic's default text echoes input values,
        # which could include the api_hash.
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"invalid configuration: {problems}") from None


def credential_source(paths: Paths, env: Mapping[str, str] | None = None) -> str:
    """Where ``api_id``/``api_hash`` currently resolve from, for ``tgmirror doctor`` — never the
    values themselves (hard rule 6)."""
    env = os.environ if env is None else env
    try:
        data = _read_toml(paths)
    except ConfigError:
        data = {}
    creds = resolve_credentials(
        env, config_api_id=data.get("api_id"), config_api_hash=data.get("api_hash")
    )
    return creds.source


def config_has_credentials(paths: Paths) -> bool:
    """Whether ``config.toml`` itself still holds ``api_id``/``api_hash`` — regardless of whether
    another tier currently wins — so ``doctor`` can suggest moving them to a keyring that showed up
    later. Tolerates a broken file: that is a different problem, reported elsewhere."""
    try:
        data = _read_toml(paths)
    except ConfigError:
        return False
    return data.get("api_id") is not None or data.get("api_hash") is not None


def validate_credentials(api_id: int, api_hash: str) -> tuple[int, str]:
    api_hash = api_hash.strip()
    if api_id <= 0 or not api_hash or any(ch.isspace() for ch in api_hash):
        raise ConfigError("api_id must be a positive integer and api_hash must not be empty")
    return api_id, api_hash


def store_credentials(paths: Paths, api_id: int, api_hash: str) -> tuple[str, str]:
    """Save where ``login`` should: the keyring if one is usable on this machine (and remove any
    copy left in ``config.toml``), else ``config.toml`` as before (docs/06-lo-trinh.md, Phase 9,
    "Ghi"). Returns ``("keyring", <backend name>)`` or ``("config", str(config_file))``."""
    api_id, api_hash = validate_credentials(api_id, api_hash)
    if keyring_usable():
        write_keyring(api_id, api_hash)
        strip_credentials(paths)
        return "keyring", keyring_backend_label()
    save_credentials(paths, api_id, api_hash)
    return "config", str(paths.config_file)


def _strip_credential_lines(text: str) -> tuple[str, bool]:
    """The text with any top-level ``api_id``/``api_hash`` lines removed, and whether anything
    changed (comments, ``[limits]`` and everything else are always kept as they are)."""
    first_table = re.search(r"^\s*\[", text, flags=re.MULTILINE)
    head, tail = (
        (text[: first_table.start()], text[first_table.start() :]) if first_table else (text, "")
    )
    kept = [ln for ln in head.splitlines() if not _CREDENTIAL_LINE.match(ln)]
    changed = len(kept) != len(head.splitlines())
    new_text = ("\n".join(kept).rstrip("\n") + "\n" if kept else "") + tail
    return new_text, changed


def strip_credentials(paths: Paths) -> None:
    """Remove any top-level ``api_id``/``api_hash`` lines from ``config.toml``, e.g. after moving
    them to the keyring. A no-op if the file doesn't exist or has neither line."""
    if not paths.config_file.exists():
        return
    try:
        text = paths.config_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {paths.config_file}: {exc}") from exc

    new_text, changed = _strip_credential_lines(text)
    if not changed:
        return
    _write_config(paths, new_text)


def config_text_without_credentials(paths: Paths) -> str | None:
    """The text of ``config.toml`` with any ``api_id``/``api_hash`` lines removed, for
    ``tgmirror appdata export`` (Phase 10) — the real file on disk is never touched. ``None`` when
    there is no config file to export."""
    if not paths.config_file.exists():
        return None
    try:
        text = paths.config_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {paths.config_file}: {exc}") from exc
    return _strip_credential_lines(text)[0]


def save_credentials(paths: Paths, api_id: int, api_hash: str) -> None:
    """Write ``api_id``/``api_hash`` into ``config.toml``, keeping every other line as it is.

    The two keys are top-level, so they go above the first ``[table]`` header. The result is parsed
    before it replaces the file, so a bad edit can never leave a broken config behind.
    """
    api_id, api_hash = validate_credentials(api_id, api_hash)

    try:
        text = paths.config_file.read_text(encoding="utf-8") if paths.config_file.exists() else ""
    except OSError as exc:
        raise ConfigError(f"cannot read {paths.config_file}: {exc}") from exc

    first_table = re.search(r"^\s*\[", text, flags=re.MULTILINE)
    head, tail = (
        (text[: first_table.start()], text[first_table.start() :]) if first_table else (text, "")
    )
    kept = [ln for ln in head.splitlines() if not _CREDENTIAL_LINE.match(ln)]
    lines = [f"api_id = {api_id}", f"api_hash = {json.dumps(api_hash)}", *kept]
    new_text = "\n".join(lines).rstrip("\n") + "\n" + (("\n" + tail) if tail else "")
    _write_config(paths, new_text)


def _write_config(paths: Paths, new_text: str) -> None:
    """Validate ``new_text`` as TOML, then replace ``config.toml`` with it atomically (a bad edit
    can never leave a broken config behind). Shared by every writer of the file."""
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"refusing to write an invalid {paths.config_file.name}: {exc}") from exc

    paths.config_dir.mkdir(parents=True, exist_ok=True)
    tmp = paths.config_file.with_suffix(".toml.tmp")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        with contextlib.suppress(OSError):  # not supported on every filesystem (e.g. Windows ACLs)
            tmp.chmod(0o600)
        tmp.replace(paths.config_file)
    except OSError as exc:
        raise ConfigError(f"cannot write {paths.config_file}: {exc}") from exc


def format_limit(value: object) -> str:
    """How ``tgmirror config get``/the menu show a ``Limits`` value; ``long_pause_range`` (a
    ``tuple``) as the two numbers separated by a comma, matching what ``set_limit`` parses back."""
    if isinstance(value, tuple):
        return ", ".join(format_limit(v) for v in value)
    return str(value)


def _toml_value(value: object) -> str:
    if isinstance(value, tuple):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _parse_limit_value(key: str, raw: str, current: object) -> object:
    raw = raw.strip()
    if isinstance(current, tuple):
        parts = raw.split(",")
        if len(parts) != 2:
            raise ConfigError(f"{key} needs two numbers separated by a comma, e.g. 30,90")
        try:
            return (float(parts[0]), float(parts[1]))
        except ValueError:
            raise ConfigError(f"{key} needs two numbers separated by a comma, e.g. 30,90") from None
    if isinstance(current, int):
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"{key} must be a whole number") from None
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a number") from None


def set_limit(paths: Paths, key: str, raw: str) -> Limits:
    """Validate and persist one ``[limits]`` key, keeping every other line of ``config.toml`` as it
    is (same care as ``save_credentials`` for the top-level keys). Returns the new ``Limits``
    (validated with the model's cross-field rules too, e.g. ``max_delay >= min_delay``) so a caller
    can show what was actually saved.
    """
    if key not in LIMIT_KEYS:
        raise ConfigError(f"unknown config key {key!r}; choices: {', '.join(LIMIT_KEYS)}")

    current = load_config(paths).limits
    parsed = _parse_limit_value(key, raw, getattr(current, key))
    merged = current.model_dump() | {key: parsed}
    try:
        new_limits = Limits.model_validate(merged)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or key}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"invalid configuration: {problems}") from None

    _write_limit(paths, key, _toml_value(getattr(new_limits, key)))
    return new_limits


def _write_limit(paths: Paths, key: str, value_text: str) -> None:
    try:
        text = paths.config_file.read_text(encoding="utf-8") if paths.config_file.exists() else ""
    except OSError as exc:
        raise ConfigError(f"cannot read {paths.config_file}: {exc}") from exc

    lines = text.splitlines()
    key_line = re.compile(rf"^(\s*){re.escape(key)}(\s*=\s*)([^#]*)(.*)$")

    limits_at: int | None = None
    end_at = len(lines)
    for i, line in enumerate(lines):
        m = _LIMITS_TABLE.match(line)
        if m is None:
            continue
        if limits_at is None and m.group("name").strip() == "limits":
            limits_at = i
        elif limits_at is not None:
            end_at = i
            break

    if limits_at is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["[limits]", f"{key} = {value_text}"]
    else:
        for i in range(limits_at + 1, end_at):
            m = key_line.match(lines[i])
            if m:
                lines[i] = f"{m.group(1)}{key}{m.group(2)}{value_text}{m.group(4)}"
                break
        else:
            lines.insert(limits_at + 1, f"{key} = {value_text}")

    new_text = "\n".join(lines) + "\n"
    _write_config(paths, new_text)
