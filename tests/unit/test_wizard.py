"""``cli/wizard.py``'s directory-asking helpers, shared by ``tgmirror backup``/``restore`` and the
full-screen menu's restore-recovery dialog (T3, Phase 15b)."""

from pathlib import Path

import pytest

from tests.fakes import ScriptedPrompter
from tgmirror.cli.wizard import ask_backup_dir, resolve_dir_answer


@pytest.fixture(autouse=True)
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))  # POSIX
    monkeypatch.setenv("USERPROFILE", str(home_dir))  # Windows
    return home_dir


async def test_resolve_dir_answer_expands_a_leading_tilde(home: Path) -> None:
    """``Path.resolve()`` alone does not expand ``~`` — typing ``~/backups`` used to create a
    literal directory named ``~`` under the current one instead of one under the home directory."""
    result = await resolve_dir_answer("~/backups")

    assert result == (home / "backups").resolve()


async def test_resolve_dir_answer_strips_shell_quotes_and_whitespace(tmp_path: Path) -> None:
    target = tmp_path / "my backups"

    result = await resolve_dir_answer(f'  "{target}"  ')

    assert result == target.resolve()


async def test_ask_backup_dir_expands_a_tilde_typed_at_the_prompt(home: Path) -> None:
    prompter = ScriptedPrompter(text=["~/backups"])

    result = await ask_backup_dir(prompter)

    assert result == (home / "backups").resolve()
