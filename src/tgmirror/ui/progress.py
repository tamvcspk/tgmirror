"""Plain-line progress for ``tgmirror clone`` and ``tgmirror run``
(implements ``engine.runner.Reporter``).

Works without a terminal: no ANSI, one line at most every ``interval`` seconds, and notices
(reconcile results, flood stop) always. This is ``Runtime.reporter``'s default
(``cli/runtime.py::plain_reporter``); with a real terminal attached, the Rich Live view
(``ui/tui.py::TuiReporter``) is used instead and reuses this module's ``duration``/notice text.
"""

import time
from collections.abc import Callable
from datetime import datetime, timedelta

from rich.text import Text

from tgmirror.core.gateway import TransferPhase
from tgmirror.engine.transfer import Transfer
from tgmirror.store.backups import Backup
from tgmirror.store.runs import Run
from tgmirror.ui.messages import t

STEP = 5  # percent a transfer must have advanced before its next line...
HEARTBEAT = 30.0  # ...unless this many seconds have passed (a crawling one still says so)
BIG_TRANSFER = 8 * 1024 * 1024  # a file smaller than this is over before a line about it would help
BAR_WIDTH = 20


def _plain(value: object) -> object:
    """Datetimes are stored in UTC; people read local time."""
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return value


def duration(span: timedelta) -> str:
    """``1 giờ 5 phút``, ``44 phút``, ``38 giây``: the two largest units are enough for an ETA."""
    seconds = max(int(span.total_seconds()), 0)
    if seconds >= 3600:
        return t("duration.hours", hours=seconds // 3600, minutes=seconds % 3600 // 60)
    if seconds >= 60:
        return t("duration.minutes", minutes=seconds // 60)
    return t("duration.seconds", seconds=seconds)


def size(count: float) -> str:
    """``512 B``, ``3.4 MB``: bytes as a person reads them."""
    units = ("B", "KB", "MB", "GB", "TB")
    value, unit = float(count), 0
    while value >= 1024 and unit < len(units) - 1:
        value, unit = value / 1024, unit + 1
    return f"{value:.0f} {units[unit]}" if unit == 0 else f"{value:.1f} {units[unit]}"


def bar(fraction: float | None) -> Text:
    """A fixed-width ``█``/``░`` bar — shared by ``TuiReporter`` (a run) and ``BackupTuiReporter``
    (a backup): once each has its own ``Estimate`` (``engine.status.estimate``/``backup_estimate``),
    drawing it is identical."""
    filled = 0 if fraction is None else round(max(0.0, min(1.0, fraction)) * BAR_WIDTH)
    return Text("█" * filled + "░" * (BAR_WIDTH - filled), style="cyan")


def progress_line(
    handled: int, total: int, fraction: float | None, speed: float | None, eta: timedelta | None
) -> str:
    """The "N/total (~P%)   speed, ETA" text next to ``bar()`` — primitive-args so a run and a
    backup share it instead of each formatting their own ``Run``/``Backup`` fields."""
    base = (
        t(
            "tui.progress_total",
            handled=handled,
            total=max(total, handled),
            percent=round(fraction * 100) if fraction is not None else 0,
        )
        if total > 0
        else t("tui.progress_plain", handled=handled)
    )
    if speed is None:
        return base
    tail = (
        t("tui.speed", speed=f"{speed:.1f}", eta=duration(eta))
        if eta is not None
        else t("tui.speed_no_eta", speed=f"{speed:.1f}")
    )
    return f"{base}   {tail}"


def floods_tail(count: int, ago: timedelta) -> str:
    """The " · N throttled (last ... ago)" suffix — identical text ``TuiReporter.render()`` and
    ``BackupTuiReporter.render()`` used to build inline, verbatim, in two places."""
    return t("tui.floods", count=count, ago=duration(ago)) if count else ""


class TransferLines:
    """The per-file "big download/upload" lines ``LineReporter.transfer`` used to build alone: a
    line when a big file starts, has advanced ``STEP`` percent (and at least ``interval`` seconds
    have passed) or ``HEARTBEAT`` seconds have gone by, and when it is done. Pure ``Transfer`` data
    in, no ``Run``/``Backup`` coupling, so ``BackupLineReporter`` shares it instead of discarding
    every transfer (as it used to)."""

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
        self._last: dict[tuple[TransferPhase, int], tuple[float, int]] = {}

    def update(self, transfer: Transfer) -> None:
        if transfer.total < BIG_TRANSFER:
            return
        key = (transfer.phase, transfer.msg_id)
        now = self._clock()
        percent = 100 if transfer.finished else min(round(transfer.fraction * 100), 99)
        last = self._last.get(key)
        if transfer.finished:
            if self._last.pop(key, None) is None:
                return  # never announced: it went by too fast to matter
        elif last is not None:
            since, shown = last
            quiet = now - since < self._interval
            small = percent - shown < STEP and now - since < HEARTBEAT
            if quiet or small:
                return
            self._last[key] = (now, percent)
        else:
            self._last[key] = (now, percent)
        speed = f", {size(transfer.speed)}/s" if transfer.speed else ""
        self._emit(
            t(
                f"run.transfer_{transfer.phase}",
                id=transfer.msg_id,
                percent=percent,
                done=size(transfer.done),
                total=size(transfer.total),
                speed=speed,
            )
        )


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
        self._transfer_lines = TransferLines(emit, interval=interval, clock=clock)

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
        """A line when a big file starts, when it has advanced ``STEP`` percent (and at least
        ``interval`` seconds have passed) or ``HEARTBEAT`` seconds have gone by, and when it is
        done. A 2 GB upload is a few dozen lines, not hundreds. Small files say nothing: the
        batch line covers them."""
        self._transfer_lines.update(transfer)


class BackupLineReporter:
    """Plain-line progress for ``tgmirror backup`` (implements ``engine.backup.Reporter``)."""

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
        self._transfer_lines = TransferLines(emit, interval=interval, clock=clock)

    def notice(self, code: str, **params: object) -> None:
        self._emit(t(f"backup.{code}", **{k: _plain(v) for k, v in params.items()}))

    def progress(self, backup: Backup) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return
        self._last = now
        self._emit(
            t(
                "backup.progress",
                id=backup.id,
                done=backup.done,
                skipped=backup.skipped_filter,
                cursor=backup.cursor_to,
            )
        )

    def transfer(self, transfer: Transfer) -> None:
        """As ``LineReporter.transfer`` — a backup only ever downloads, so
        ``run.transfer_download`` is the only key this ever emits (``TransferLines`` doesn't know
        or care which task it's for)."""
        self._transfer_lines.update(transfer)
