"""Ctrl+C while a clone runs: the first one asks the runner to finish the batch and save, the second
one quits at once (the half-done batch is repaired by reconcile on the next run)."""

import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from tgmirror.engine.runner import RunControl


@dataclass
class Interruption:
    hit: bool = False  # Ctrl+C was pressed (as opposed to `q` or `tgmirror stop`)


@contextmanager
def stop_on_interrupt(control: RunControl, on_first: Callable[[], None]) -> Iterator[Interruption]:
    interruption = Interruption()
    previous = signal.getsignal(signal.SIGINT) or signal.SIG_DFL

    def handler(signum: int, frame: object) -> None:
        if interruption.hit:
            signal.signal(signal.SIGINT, previous)
            raise KeyboardInterrupt
        interruption.hit = True
        control.request_stop()
        on_first()

    try:
        signal.signal(signal.SIGINT, handler)
    except ValueError:  # not the main thread: no handler possible, the default applies
        yield interruption
        return
    try:
        yield interruption
    finally:
        signal.signal(signal.SIGINT, previous)
