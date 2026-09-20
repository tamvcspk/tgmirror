"""Hotkeys while a clone runs: ``p`` pause, ``r`` resume, ``q`` stop (docs/02-cli-ux.md).

Plain keys on purpose: Ctrl+P and Ctrl+R never reach a program running in the VS Code terminal.
Ctrl+C keeps working as before (``cli/interrupt.py``). A reader thread turns key presses into calls
on the ``RunControl``; the future Rich view drives the same three calls.

Only used when a terminal is attached. Each platform needs its own single-key reader; both leave
the terminal as they found it.
"""

import contextlib
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from tgmirror.engine.runner import RunControl

# ``Runtime.keys``: enter it around the run; it yields whether hotkeys are active (worth a hint).
KeyProvider = Callable[[RunControl], AbstractContextManager[bool]]

POLL = 0.1  # seconds between looks at the keyboard; also how fast the reader thread can be stopped


def apply_key(control: RunControl, key: str) -> bool:
    """Do what ``key`` asks of the run. ``False`` for a key that means nothing."""
    match key.lower():
        case "p":
            control.request_pause()
        case "r":
            control.request_resume()
        case "q":
            control.request_stop()
        case _:
            return False
    return True


class KeyReader(Protocol):
    def read(self, timeout: float) -> str | None:
        """The next key pressed within ``timeout`` seconds, else ``None``."""
        ...

    def close(self) -> None: ...


@contextmanager
def no_keys(control: RunControl) -> Iterator[bool]:
    """The default: no terminal (or a test) and therefore no hotkeys."""
    yield False


@contextmanager
def terminal_keys(control: RunControl) -> Iterator[bool]:
    """Listen for hotkeys on the real terminal while inside the block."""
    reader = open_reader()
    if reader is None:
        yield False
        return
    stop = threading.Event()
    thread = threading.Thread(target=pump_keys, args=(reader, control, stop), daemon=True)
    thread.start()
    try:
        yield True
    finally:
        stop.set()
        thread.join(timeout=1)
        reader.close()


def pump_keys(reader: KeyReader, control: RunControl, stop: threading.Event) -> None:
    """Feed key presses to ``control`` until ``stop`` is set. Public so tests can drive it."""
    while not stop.is_set():
        key = reader.read(POLL)
        if key is not None:
            apply_key(control, key)


def open_reader() -> KeyReader | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        return _WindowsReader() if os.name == "nt" else _PosixReader()
    except (ImportError, OSError):  # an odd terminal: run without hotkeys rather than not at all
        return None


class _WindowsReader:
    def __init__(self) -> None:
        import msvcrt

        self._msvcrt = msvcrt

    def read(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._msvcrt.kbhit():
                key = self._msvcrt.getwch()
                if key in ("\x00", "\xe0"):  # arrow/function key: a second code follows
                    self._msvcrt.getwch()
                    return None
                return key
            time.sleep(0.02)
        return None

    def close(self) -> None:
        pass


class _PosixReader:
    def __init__(self) -> None:
        import select
        import termios
        import tty

        self._select = select
        self._termios = termios
        self._fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)  # keys arrive at once and unechoed; Ctrl+C still raises SIGINT

    def read(self, timeout: float) -> str | None:
        ready, _, _ = self._select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(self._fd, 1)
        return data.decode(errors="ignore") or None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._saved)
