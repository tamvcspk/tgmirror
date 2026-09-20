"""Plain-line progress for ``tgmirror clone`` and ``tgmirror run``
(implements ``engine.runner.Reporter``).

Works without a terminal: no ANSI, one line at most every ``interval`` seconds, and notices
(reconcile results, flood stop) always. The Rich live view with keys is phase 7
(docs/06-lo-trinh.md); ``docs/02-cli-ux.md`` describes it.
"""

import time
from collections.abc import Callable
from datetime import datetime

from tgmirror.core.gateway import TransferPhase
from tgmirror.engine.transfer import Transfer
from tgmirror.store.runs import Run
from tgmirror.ui.messages import t

BIG_TRANSFER = 8 * 1024 * 1024  # a file smaller than this is over before a line about it would help


def _plain(value: object) -> object:
    """Datetimes are stored in UTC; people read local time."""
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return value


def size(count: float) -> str:
    """``512 B``, ``3.4 MB``: bytes as a person reads them."""
    units = ("B", "KB", "MB", "GB", "TB")
    value, unit = float(count), 0
    while value >= 1024 and unit < len(units) - 1:
        value, unit = value / 1024, unit + 1
    return f"{value:.0f} {units[unit]}" if unit == 0 else f"{value:.1f} {units[unit]}"


class LineReporter:
    def __init__(
        self,
        emit: Callable[[str], None],
        *,
        interval: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self._interval = interval
        self._clock = clock
        self._last: float | None = None
        self._last_transfer: dict[tuple[TransferPhase, int], float] = {}

    def notice(self, code: str, **params: object) -> None:
        self._emit(t(f"run.{code}", **{k: _plain(v) for k, v in params.items()}))

    def progress(self, run: Run) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return
        self._last = now
        total = run.options.total_items
        if run.options.retry_of is not None:  # the source cursor does not move in a retry
            key = "run.progress_retry"
        elif total > 0:
            key = "run.progress_total"
        else:
            key = "run.progress_filtered" if run.skipped_filter else "run.progress"
        self._emit(
            t(
                key,
                id=run.id,
                done=run.done,
                skipped=run.skipped_filter,
                failed=run.failed,
                cursor=run.cursor_src_id,
                handled=run.handled,
                total=max(total, run.handled),  # the count is an upper bound, never below the work
                percent=min(round(run.handled / total * 100), 100) if total else 0,
            )
        )

    def transfer(self, transfer: Transfer) -> None:
        """A line when a big file starts, every ``interval`` seconds, and when it is done. Small
        files say nothing: the batch line covers them."""
        if transfer.total < BIG_TRANSFER:
            return
        key = (transfer.phase, transfer.msg_id)
        now = self._clock()
        last = self._last_transfer.get(key)
        if transfer.finished:
            if self._last_transfer.pop(key, None) is None:
                return  # never announced: it went by too fast to matter
        elif last is not None and now - last < self._interval:
            return
        else:
            self._last_transfer[key] = now
        speed = f", {size(transfer.speed)}/s" if transfer.speed else ""
        self._emit(
            t(
                f"run.transfer_{transfer.phase}",
                id=transfer.msg_id,
                percent=round(transfer.fraction * 100),
                done=size(transfer.done),
                total=size(transfer.total),
                speed=speed,
            )
        )
