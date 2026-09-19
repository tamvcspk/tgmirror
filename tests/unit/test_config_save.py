import tomllib
from pathlib import Path

import pytest

from tgmirror.core.config import load_config, save_credentials
from tgmirror.core.errors import ConfigError
from tgmirror.core.paths import Paths

HASH = "0123456789abcdef0123456789abcdef"


def test_creates_config_file(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)

    save_credentials(paths, 12345, HASH)

    config = load_config(paths, env={})
    assert config.api_id == 12345
    assert config.api_hash is not None and config.api_hash.get_secret_value() == HASH


def test_keeps_limits_comments_and_replaces_old_credentials(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(
        '# my notes\napi_id = 1\napi_hash = "old"\n\n[limits]  # see docs\nbatch_size = 50\n'
        "long_pause_range = [10, 20]\n",
        encoding="utf-8",
    )

    save_credentials(paths, 999, HASH)

    text = paths.config_file.read_text(encoding="utf-8")
    assert "# my notes" in text and "# see docs" in text
    assert text.count("api_id") == 1 and "old" not in text
    config = load_config(paths, env={})
    assert config.api_id == 999
    assert config.limits.batch_size == 50
    assert config.limits.long_pause_range == (10, 20)


def test_credentials_go_above_the_first_table(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text("[limits]\nbatch_size = 5\n", encoding="utf-8")

    save_credentials(paths, 7, HASH)

    data = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    assert data["api_id"] == 7  # top level, not inside [limits]
    assert data["limits"] == {"batch_size": 5}


def test_hash_with_special_characters_is_escaped(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)

    save_credentials(paths, 1, 'ab"c\\d')

    config = load_config(paths, env={})
    assert config.api_hash is not None and config.api_hash.get_secret_value() == 'ab"c\\d'


@pytest.mark.parametrize(("api_id", "api_hash"), [(0, HASH), (-5, HASH), (1, ""), (1, "a b")])
def test_rejects_bad_values(tmp_path: Path, api_id: int, api_hash: str) -> None:
    paths = Paths.under(tmp_path)

    with pytest.raises(ConfigError):
        save_credentials(paths, api_id, api_hash)

    assert not paths.config_file.exists()


def test_broken_existing_file_is_left_untouched(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.config_dir.mkdir(parents=True)
    broken = "[limits\nbatch_size = = 5\n"
    paths.config_file.write_text(broken, encoding="utf-8")

    with pytest.raises(ConfigError):
        save_credentials(paths, 1, HASH)

    assert paths.config_file.read_text(encoding="utf-8") == broken
