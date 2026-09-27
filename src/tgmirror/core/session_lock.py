"""One process per Telethon session: an OS-level exclusive lock beside the session file (N7,
Phase 15b, hard rule 1 in ``CLAUDE.md``).

The session file itself is a SQLite database, and SQLite only takes its own file lock during a
*write* — so a second process could ``connect()`` successfully (a read) and only fail later, deep
inside some arbitrary Telethon call that happens to write (saving an updated auth key, the update
state, ...), as a raw ``sqlite3.OperationalError: database is locked`` nothing maps to a clean
error (``core/telethon_gateway.py::map_exception`` maps that message to ``SessionBusy`` as a
second line of defence, for whatever narrow window this lock does not cover). Taking this lock
before the session is even opened makes the second process fail immediately and always, not only
when its bad luck lines up with a write.

Pure OS mechanics, no Telethon import, so it is testable without a client at all. The lock is
released by the OS the moment the holding process exits or closes the file — including a hard
kill — so there is nothing to clean up on the next start, unlike the store's heartbeat-based
``RunBusy``/``BackupBusy``.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from tgmirror.core.errors import SessionBusy


@contextmanager
def session_lock(path: Path) -> Iterator[None]:
    """Exclusive lock on ``path`` (created if missing, never truncated), held for the block.

    Raises ``SessionBusy`` at once if another process already holds it — never blocks waiting.
    """
    fh = path.open("a+b")
    try:
        _try_lock(fh)
    except OSError as exc:
        fh.close()
        raise SessionBusy(f"{path} is locked by another process") from exc
    try:
        yield
    finally:
        _unlock(fh)
        fh.close()


def _try_lock(fh: BinaryIO) -> None:
    """Raises ``OSError`` at once if already locked (never blocks)."""
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        if fh.read(1) == b"":  # a brand new, empty file: give the byte-range lock a byte to hold
            fh.write(b"0")
            fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
