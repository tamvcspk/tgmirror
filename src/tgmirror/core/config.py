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

ENV_API_ID = "TGMIRROR_API_ID"
ENV_API_HASH = "TGMIRROR_API_HASH"
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
    upload_concurrency: int = Field(1, ge=1)

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


def load_config(paths: Paths, env: Mapping[str, str] | None = None) -> Config:
    """Read ``config.toml`` (if present); ``TGMIRROR_API_ID``/``TGMIRROR_API_HASH`` win over it."""
    env = os.environ if env is None else env
    data: dict[str, object] = {}
    if paths.config_file.exists():
        try:
            with paths.config_file.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read {paths.config_file}: {exc}") from exc

    if (raw_id := env.get(ENV_API_ID)) is not None:
        try:
            data["api_id"] = int(raw_id)
        except ValueError:
            raise ConfigError(f"{ENV_API_ID} must be an integer") from None
    if (raw_hash := env.get(ENV_API_HASH)) is not None:
        data["api_hash"] = raw_hash

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        # Report locations and messages only: pydantic's default text echoes input values,
        # which could include the api_hash.
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"invalid configuration: {problems}") from None


def save_credentials(paths: Paths, api_id: int, api_hash: str) -> None:
    """Write ``api_id``/``api_hash`` into ``config.toml``, keeping every other line as it is.

    The two keys are top-level, so they go above the first ``[table]`` header. The result is parsed
    before it replaces the file, so a bad edit can never leave a broken config behind.
    """
    api_hash = api_hash.strip()
    if api_id <= 0 or not api_hash or any(ch.isspace() for ch in api_hash):
        raise ConfigError("api_id must be a positive integer and api_hash must not be empty")

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
