"""``core.config.set_limit``: ``tgmirror config set``'s validation and surgical write (same care as
``save_credentials`` for the top-level keys, see ``test_config_save.py``)."""

from pathlib import Path

import pytest

from tgmirror.core.config import format_limit, load_config, set_limit
from tgmirror.core.errors import ConfigError
from tgmirror.core.paths import Paths


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    p = Paths.under(tmp_path)
    p.config_dir.mkdir(parents=True)
    return p


def test_format_limit_scalar_and_range() -> None:
    assert format_limit(20) == "20"
    assert format_limit(2.0) == "2.0"
    assert format_limit((30.0, 90.0)) == "30.0, 90.0"


def test_sets_a_new_key_on_an_empty_file(paths: Paths) -> None:
    new_limits = set_limit(paths, "batch_size", "30")

    assert new_limits.batch_size == 30
    assert load_config(paths, env={}).limits.batch_size == 30


def test_updates_an_existing_key_keeping_comments_and_the_rest_of_the_file(paths: Paths) -> None:
    paths.config_file.write_text(
        '# my notes\napi_id = 1\napi_hash = "h"\n\n[limits]  # see docs\n'
        "batch_size = 50  # was tuned by hand\nmin_delay = 3.0\n",
        encoding="utf-8",
    )

    set_limit(paths, "batch_size", "40")

    text = paths.config_file.read_text(encoding="utf-8")
    assert "# my notes" in text and "# see docs" in text and "# was tuned by hand" in text
    assert "batch_size = 40" in text
    config = load_config(paths, env={})
    assert config.api_id == 1  # untouched
    assert config.limits.batch_size == 40
    assert config.limits.min_delay == 3.0  # untouched


def test_adds_limits_table_when_none_exists_yet(paths: Paths) -> None:
    paths.config_file.write_text('api_id = 1\napi_hash = "h"\n', encoding="utf-8")

    set_limit(paths, "jitter", "0.2")

    config = load_config(paths, env={})
    assert config.api_id == 1
    assert config.limits.jitter == 0.2


def test_adds_a_key_missing_from_an_existing_limits_table(paths: Paths) -> None:
    paths.config_file.write_text("[limits]\nbatch_size = 50\n", encoding="utf-8")

    set_limit(paths, "min_delay", "5")

    config = load_config(paths, env={})
    assert config.limits.batch_size == 50
    assert config.limits.min_delay == 5.0


def test_range_key_takes_two_comma_separated_numbers(paths: Paths) -> None:
    new_limits = set_limit(paths, "long_pause_range", "10, 20")

    assert new_limits.long_pause_range == (10.0, 20.0)


def test_unknown_key_is_rejected(paths: Paths) -> None:
    with pytest.raises(ConfigError, match="unknown config key"):
        set_limit(paths, "not_a_real_key", "1")

    assert not paths.config_file.exists()


def test_bad_value_is_rejected_before_writing(paths: Paths) -> None:
    with pytest.raises(ConfigError):
        set_limit(paths, "batch_size", "not-a-number")

    assert not paths.config_file.exists()


def test_cross_field_rule_is_enforced(paths: Paths) -> None:
    set_limit(paths, "min_delay", "10")

    with pytest.raises(ConfigError, match="max_delay"):
        set_limit(paths, "max_delay", "1")

    assert load_config(paths, env={}).limits.max_delay == 60.0  # unchanged


def test_out_of_range_value_is_rejected(paths: Paths) -> None:
    with pytest.raises(ConfigError):
        set_limit(paths, "batch_size", "0")
