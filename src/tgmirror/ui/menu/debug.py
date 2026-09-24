"""A debug log for diagnosing the full-screen menu's redraw timing (the "phải bấm phím mới cập
nhật" symptom, docs/06-lo-trinh.md, "Kế hoạch giao diện full-screen (menu)").

Off by default (nothing here touches stdout — that belongs to the alt-screen, and writing to it
would corrupt the display exactly like ``typer.echo`` would). Turn it on by setting
``TGMIRROR_MENU_DEBUG`` to a file path before running ``tgmirror``; every loop iteration, redraw,
screen transition and background-key event gets one line, with a monotonic timestamp so the
relative timing between them is visible. Never logs message content, filenames, api_hash, session
strings, phone numbers or codes (CLAUDE.md rule 6) — only loop/timing events and Python type names.
"""

import os
import time
from pathlib import Path

_path: Path | None = None
_start = time.monotonic()
_initialized = False


def enabled() -> bool:
    return _path is not None


def init() -> None:
    """Read ``TGMIRROR_MENU_DEBUG`` once; call before the menu's main loop starts. Safe to call
    more than once (a second call is a no-op) so both ``launch()`` and tests can call it freely."""
    global _path, _initialized
    if _initialized:
        return
    _initialized = True
    raw = os.environ.get("TGMIRROR_MENU_DEBUG")
    if not raw:
        return
    _path = Path(raw)
    _path.write_text("", encoding="utf-8")  # start fresh each run, not appended across runs


def log(event: str, **fields: object) -> None:
    if _path is None:
        return
    elapsed = time.monotonic() - _start
    parts = " ".join(f"{key}={value!r}" for key, value in fields.items())
    line = f"{elapsed:9.3f} {event} {parts}\n" if parts else f"{elapsed:9.3f} {event}\n"
    with _path.open("a", encoding="utf-8") as f:
        f.write(line)
