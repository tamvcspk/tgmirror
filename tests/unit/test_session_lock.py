"""``core/session_lock.py``: the OS-level exclusive lock behind N7 (Phase 15b), no Telethon
involved — pure filesystem mechanics."""

from pathlib import Path

import pytest

from tgmirror.core.errors import SessionBusy
from tgmirror.core.session_lock import session_lock


def test_a_second_lock_on_the_same_path_is_refused_at_once(tmp_path: Path) -> None:
    path = tmp_path / "default.session.lock"
    with session_lock(path), pytest.raises(SessionBusy), session_lock(path):
        pass  # never reached: the second lock must fail before entering


def test_the_lock_is_released_when_the_first_holder_closes(tmp_path: Path) -> None:
    path = tmp_path / "default.session.lock"
    with session_lock(path):
        pass
    with session_lock(path):  # the first holder is gone: this must succeed
        pass


def test_the_lock_is_released_even_when_the_block_raises(tmp_path: Path) -> None:
    path = tmp_path / "default.session.lock"
    with pytest.raises(ValueError, match="boom"), session_lock(path):
        raise ValueError("boom")
    with session_lock(path):  # released despite the error: this must succeed
        pass


def test_the_lock_file_is_created_if_missing(tmp_path: Path) -> None:
    path = tmp_path / "sessions" / "default.session.lock"
    path.parent.mkdir()

    with session_lock(path):
        assert path.exists()


def test_two_different_paths_do_not_interfere(tmp_path: Path) -> None:
    a, b = tmp_path / "a.lock", tmp_path / "b.lock"
    with session_lock(a), session_lock(b):
        pass
