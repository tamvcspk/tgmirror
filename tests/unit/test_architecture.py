"""Guards for the hard rules that a stray import or call would silently break."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "tgmirror"
TELETHON_MODULE = SRC / "core" / "telethon_gateway.py"


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_only_the_gateway_module_imports_telethon() -> None:
    """Hard rule 8: engine, CLI and the rest never see Telethon types."""
    pattern = re.compile(r"^\s*(?:import|from)\s+telethon\b", re.MULTILINE)

    offenders = [
        p.relative_to(SRC).as_posix()
        for p in _sources()
        if p != TELETHON_MODULE and pattern.search(p.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_sql_stays_inside_the_store_package() -> None:
    """Skill checkpoint-state, rule 4: the engine and the CLI use intent-level ``Store`` methods."""
    store_dir = SRC / "store"
    sql = re.compile(r"\b(?:SELECT|INSERT INTO|UPDATE|DELETE FROM)\b|\baiosqlite\b")

    offenders = [
        p.relative_to(SRC).as_posix()
        for p in _sources()
        if store_dir not in p.parents and sql.search(p.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_engine_code_does_not_import_the_cli_or_the_ui() -> None:
    """The engine only depends on the gateway protocol, the store and its own modules."""
    pattern = re.compile(r"^\s*(?:import|from)\s+tgmirror\.(?:cli|ui)\b", re.MULTILINE)

    offenders = [
        p.relative_to(SRC).as_posix()
        for p in (SRC / "engine").rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]

    assert offenders == []


def test_the_client_is_only_built_with_flood_sleep_disabled() -> None:
    """Hard rule 2 / D6: one place builds ``TelegramClient`` and it passes threshold 0."""
    text = TELETHON_MODULE.read_text(encoding="utf-8")

    assert text.count("TelegramClient(") == 1
    assert "flood_sleep_threshold=0" in text


def test_the_runner_reaches_telegram_only_through_the_flood_guard() -> None:
    """Hard rule 1: every read and write of a run is paced and flood-handled by ``FloodGuard``."""
    lines = (SRC / "engine" / "runner.py").read_text(encoding="utf-8").splitlines()

    for number, line in enumerate(lines):
        if "self._gateway" in line and "def " not in line and "self._gateway = " not in line:
            nearby = " ".join(lines[max(number - 6, 0) : number + 1])
            assert any(f"guard.{how}(" in nearby for how in ("reader", "write", "transfer")), (
                f"runner.py line {number + 1} uses the gateway outside the flood guard"
            )
