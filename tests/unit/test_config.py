from pathlib import Path

import pytest

from tgmirror.core.config import Limits, load_config
from tgmirror.core.errors import ConfigError
from tgmirror.core.paths import Paths


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    p = Paths.under(tmp_path)
    p.config_dir.mkdir(parents=True)
    return p


def test_defaults_without_file_or_env(paths: Paths) -> None:
    cfg = load_config(paths, env={})

    assert cfg.api_id is None and cfg.api_hash is None
    assert cfg.limits == Limits()
    assert cfg.limits.batch_size == 20
    assert cfg.limits.long_pause_range == (30.0, 90.0)
    # download and upload each get their own request budget (docs/06-lo-trinh.md, 2026-09-23):
    # a real run found download broke at 8 in flight where upload did not
    assert (cfg.limits.download_requests, cfg.limits.upload_requests) == (4, 8)


def test_reads_toml(paths: Paths) -> None:
    paths.config_file.write_text(
        'api_id = 123\napi_hash = "abc"\n\n[limits]\nbatch_size = 50\nlong_pause_range = [10, 20]\n',  # noqa: E501
        encoding="utf-8",
    )

    cfg = load_config(paths, env={})

    assert cfg.api_id == 123
    assert cfg.api_hash is not None and cfg.api_hash.get_secret_value() == "abc"
    assert cfg.limits.batch_size == 50
    assert cfg.limits.long_pause_range == (10.0, 20.0)
    assert cfg.limits.min_delay == 2.0  # untouched keys keep their defaults


def test_env_overrides_file(paths: Paths) -> None:
    paths.config_file.write_text('api_id = 1\napi_hash = "from-file"\n', encoding="utf-8")

    cfg = load_config(paths, env={"TGMIRROR_API_ID": "42", "TGMIRROR_API_HASH": "from-env"})

    assert cfg.api_id == 42
    assert cfg.api_hash is not None and cfg.api_hash.get_secret_value() == "from-env"


def test_api_hash_never_appears_in_repr(paths: Paths) -> None:
    cfg = load_config(paths, env={"TGMIRROR_API_HASH": "super-secret-hash"})

    assert "super-secret-hash" not in repr(cfg)
    assert "super-secret-hash" not in str(cfg)


def test_invalid_env_api_id(paths: Paths) -> None:
    with pytest.raises(ConfigError, match="TGMIRROR_API_ID"):
        load_config(paths, env={"TGMIRROR_API_ID": "abc"})


def test_broken_toml(paths: Paths) -> None:
    paths.config_file.write_text("this is = = not toml", encoding="utf-8")

    with pytest.raises(ConfigError, match="cannot read"):
        load_config(paths, env={})


@pytest.mark.parametrize(
    "limits",
    [
        "batch_size = 101",
        "batch_size = 0",
        "min_delay = 10\nmax_delay = 5",
        "long_pause_range = [90, 30]",
        "jitter = 1.0",
        "read_delay = 0",
        "unknown_key = 1",
    ],
)
def test_invalid_limits_are_rejected(paths: Paths, limits: str) -> None:
    paths.config_file.write_text(f"[limits]\n{limits}\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="limits"):
        load_config(paths, env={})


def test_validation_error_does_not_echo_secret(paths: Paths) -> None:
    paths.config_file.write_text(
        'api_id = "not-a-number"\napi_hash = "leaky-secret"\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError) as exc:
        load_config(paths, env={})

    assert "leaky-secret" not in str(exc.value)
