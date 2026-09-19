import pytest

from tgmirror.ui.messages import EN, ENV_LANG, VI, t


def test_both_languages_have_the_same_keys() -> None:
    assert set(VI) == set(EN)


def test_placeholders_match_between_languages() -> None:
    import string

    def fields(text: str) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}

    assert {k: fields(v) for k, v in VI.items()} == {k: fields(v) for k, v in EN.items()}


def test_language_switch_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_LANG, raising=False)
    assert t("login.already", who="X") == VI["login.already"].format(who="X")  # Vietnamese default

    monkeypatch.setenv(ENV_LANG, "en")
    assert t("login.already", who="X") == "Already logged in: X."


def test_unknown_language_falls_back_to_vietnamese_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_LANG, "fr")

    assert t("whoami.line", who="A", id=1) == "A (id 1)"


def test_every_key_used_in_the_source_exists() -> None:
    """A typo in a message key would only surface when that code path runs."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "tgmirror"
    used: set[str] = set()
    for path in src.rglob("*.py"):
        if path.name == "messages.py":
            continue
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"""\bt\(\s*["']([a-z_]+\.[a-z_]+|yes|no|admin)["']""", text))
        used |= set(re.findall(r"""\bnotify\(\s*["']([a-z_.]+)["']""", text))
        used |= set(re.findall(r"""UsageProblem\(\s*["']([a-z_.]+)["']""", text))
        used |= {f"run.{c}" for c in re.findall(r"""\.notice\(\s*["']([a-z_]+)["']""", text)}
        used |= {
            f"warn.{w}" for w in re.findall(r"""warnings\.append\(\s*["']([a-z_]+)["']""", text)
        }

    assert used, "the scan found no keys: the regexes are stale"
    assert used - set(VI) == set()
