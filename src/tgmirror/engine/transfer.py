"""Progress of the file being downloaded or uploaded (strategy B), for whoever shows it.

The gateway reports ``(phase, message id, bytes done, bytes in all)`` many times per file
(``core.gateway.OnTransfer``); ``TransferTracker`` adds a speed (over the last few seconds, so a
stalled or slow link shows at once) and hands the result to a sink, the reporter. It keeps nothing
on disk: this is what is happening now, not state (docs/04-state-checkpoint.md).
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from tgmirror.core.gateway import TransferPhase

SPEED_WINDOW = 5.0  # seconds of history the speed is measured over


@dataclass(frozen=True, slots=True)
class Transfer:
    phase: TransferPhase
    msg_id: int  # the message (the first one of an album) the file belongs to
    done: int  # bytes
    total: int  # bytes
    speed: float | None  # bytes per second over the last ``SPEED_WINDOW``; ``None``: one sample yet

    @property
    def finished(self) -> bool:
        return self.total > 0 and self.done >= self.total

    @property
    def fraction(self) -> float:
        return min(self.done / self.total, 1.0) if self.total > 0 else 0.0


class TransferTracker:
    """Its ``update`` is what the gateway calls; it is cheap and never raises."""

    def __init__(
        self,
        sink: Callable[[Transfer], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        window: float = SPEED_WINDOW,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._window = window
        self._samples: dict[tuple[TransferPhase, int], deque[tuple[float, int]]] = {}

    def update(self, phase: TransferPhase, msg_id: int, done: int, total: int) -> None:
        now = self._clock()
        key = (phase, msg_id)
        samples = self._samples.setdefault(key, deque())
        if samples and done < samples[-1][1]:  # went backwards: the transfer started over
            samples.clear()
        samples.append((now, done))
        while len(samples) > 2 and now - samples[0][0] > self._window:
            samples.popleft()
        speed = None
        if len(samples) >= 2 and (span := now - samples[0][0]) > 0:
            speed = (done - samples[0][1]) / span
        transfer = Transfer(phase, msg_id, done, total, speed)
        if transfer.finished:
            del self._samples[key]
        self._sink(transfer)
