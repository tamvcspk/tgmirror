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

    # ``learn_context`` is the documented one-shot setup (Premium, the destination's slow mode)
    guarded = ("guard.reader(", "guard.write(", "guard.transfer(", "learn_context(")
    for number, line in enumerate(lines):
        if "self._gateway" in line and "def " not in line and "self._gateway = " not in line:
            nearby = " ".join(lines[max(number - 6, 0) : number + 1])
            assert any(how in nearby for how in guarded), (
                f"runner.py line {number + 1} uses the gateway outside the flood guard"
            )


def test_operations_reach_telegram_only_through_the_flood_guard() -> None:
    """Hard rule 1: deleting is a write, and the scan of its plan thousands of reads — so
    ``engine/ops.py`` calls a gateway method only inside ``guard.write(...)``, or for the one-shot
    setup (``get_channel``: the rights that count now); everything it reads goes through
    ``guard.reader(...)``."""
    text = (SRC / "engine" / "ops.py").read_text(encoding="utf-8")
    lines = text.splitlines()

    assert "guard.reader(" in text and "guard.write(" in text
    for number, line in enumerate(lines):
        if re.search(r"gateway\.\w+\(", line) and "get_channel" not in line:
            nearby = " ".join(lines[max(number - 4, 0) : number + 1])
            assert "guard.write(" in nearby, (
                f"ops.py line {number + 1} calls the gateway outside the flood guard"
            )


def test_inspect_reads_a_channel_only_through_the_flood_guard() -> None:
    """Hard rule 1: a scan is thousands of read requests, so ``tgmirror inspect`` is no one-shot
    command — its reader is ``guard.reader(...)`` and the raw gateway is only asked for the
    one-shot setup (listing chats)."""
    text = (SRC / "cli" / "commands" / "inspect.py").read_text(encoding="utf-8")

    assert "guard.reader(conn.gateway)" in text
    for forbidden in ("conn.gateway.iter_messages", "conn.gateway.get_messages", ".export_unit("):
        assert forbidden not in text
    assert text.count("conn.gateway.") == 1 and "conn.gateway.list_channels()" in text
