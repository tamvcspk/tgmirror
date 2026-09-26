"""Ctrl+C (SIGINT) or `docker stop` (SIGTERM) while a clone runs: the first one asks the runner to
finish the batch and save, the second one quits at once (the half-done batch is repaired by
reconcile on the next run). Phase 12: SIGTERM is treated exactly like the first/second SIGINT so
`docker stop` stops a container-run `clone`/`run`/`retry`/`backup`/`restore` the same clean way
Ctrl+C already did; `docker stop`'s default 10s grace period is too short for a batch plus a
FloodWait, so the image's docs call for a longer `--stop-timeout`/`stop_grace_period`."""

import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from tgmirror.engine.runner import RunControl

_SIGNALS = (signal.SIGINT, signal.SIGTERM)


@dataclass
class Interruption:
    hit: bool = False  # Ctrl+C or SIGTERM arrived (as opposed to `q` or `tgmirror stop`)


@contextmanager
def stop_on_interrupt(control: RunControl, on_first: Callable[[], None]) -> Iterator[Interruption]:
    interruption = Interruption()
    previous = {sig: signal.getsignal(sig) or signal.SIG_DFL for sig in _SIGNALS}

    def handler(signum: int, frame: object) -> None:
        if interruption.hit:
            for sig, prev in previous.items():
                signal.signal(sig, prev)
            raise KeyboardInterrupt
        interruption.hit = True
        control.request_stop()
        on_first()

    try:
        for sig in _SIGNALS:
            signal.signal(sig, handler)
    except ValueError:  # not the main thread: no handler possible, the default applies
        yield interruption
        return
    try:
        yield interruption
    finally:
        for sig, prev in previous.items():
            signal.signal(sig, prev)
