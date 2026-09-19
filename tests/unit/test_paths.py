from pathlib import Path

import pytest

from tgmirror.core.paths import Paths


def test_layout_under_root(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)

    assert paths.config_file == tmp_path / "config" / "config.toml"
    assert paths.db_path == tmp_path / "data" / "tgmirror.db"
    assert paths.session_path("main") == tmp_path / "data" / "sessions" / "main.session"
    assert paths.tmp_dir == tmp_path / "data" / "tmp"


def test_ensure_creates_directories_and_is_idempotent(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)

    paths.ensure()
    paths.ensure()

    assert paths.config_dir.is_dir()
    assert paths.sessions_dir.is_dir()
    assert paths.tmp_dir.is_dir()


@pytest.mark.parametrize("name", ["", "../evil", "a/b", "a b", "x.session"])
def test_session_name_is_validated(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        Paths.under(tmp_path).session_path(name)


def test_default_paths_are_app_specific() -> None:
    paths = Paths.default()

    assert "tgmirror" in paths.data_dir.parts[-1].lower()
    assert "tgmirror" in paths.config_dir.parts[-1].lower()
