import os
import stat
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_ensure_tightens_an_already_existing_sessions_dir(tmp_path: Path) -> None:
    """T5, Phase 15b: ``Path.mkdir(mode=..., exist_ok=True)`` only applies ``mode`` the moment it
    actually creates the directory — silently ignored once the directory is already there with
    wider permissions, so a first ``ensure()`` used to tighten it but a later one, on a directory
    someone else had already widened, did not."""
    paths = Paths.under(tmp_path)
    paths.ensure()
    paths.sessions_dir.chmod(0o755)  # widen it, as if something else had loosened it

    paths.ensure()

    assert stat.S_IMODE(paths.sessions_dir.stat().st_mode) == 0o700


@pytest.mark.parametrize("name", ["", "../evil", "a/b", "a b", "x.session"])
def test_session_name_is_validated(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        Paths.under(tmp_path).session_path(name)


def test_default_paths_are_app_specific() -> None:
    paths = Paths.default()

    assert "tgmirror" in paths.data_dir.parts[-1].lower()
    assert "tgmirror" in paths.config_dir.parts[-1].lower()
