"""Ctrl+C during ``tgmirror run``: the first one asks the runner to finish the batch and save,
the second one quits at once (the half-done batch is repaired by reconcile on the next run)."""

import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from tgmirror.engine.runner import StopSignal


@contextmanager
def stop_on_interrupt(stop: StopSignal, on_first: Callable[[], None]) -> Iterator[None]:
    previous = signal.getsignal(signal.SIGINT) or signal.SIG_DFL

    def handler(signum: int, frame: object) -> None:
        if stop.requested:
            signal.signal(signal.SIGINT, previous)
            raise KeyboardInterrupt
        stop.request()
        on_first()

    try:
        signal.signal(signal.SIGINT, handler)
    except ValueError:  # not the main thread: no handler possible, the default applies
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
