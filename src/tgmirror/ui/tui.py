"""Foreground TUI (Rich Live) for ``clone``/``run`` on a real terminal (phase 7,
docs/02-cli-ux.md, "Foreground TUI"). Implements ``engine.runner.Reporter``.

Only the display changes from ``ui/progress.py``'s ``LineReporter``: the hotkeys it shows
(``p``/``r``/``q``) already work through ``cli/keys.py`` and ``RunControl`` regardless of which
reporter is in use. Without a terminal (redirected output, ``tgmirror run`` from a script, tests),
``LineReporter`` remains the reporter: no ANSI, one line at a time.

Progress, speed and ETA reuse ``engine.status.estimate`` exactly as ``tgmirror status`` does, so
the two never disagree about a run in flight. ``delay`` and the flood count are not part of
``Reporter`` (they belong to the limiter, which the reporter never sees): this view infers them
from the ``throttled``/``flood_waiting`` notices it already receives, so a shown delay is only as
fresh as the last flood (it does not know about a decay back down that caused no notice).
"""

import time
from collections.abc import Callable
from datetime import datetime, timedelta

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.text import Text

from tgmirror.core.config import Limits
from tgmirror.engine.status import estimate
from tgmirror.engine.transfer import Transfer
from tgmirror.store.db import utc_now
from tgmirror.store.runs import Run
from tgmirror.ui.messages import t
from tgmirror.ui.progress import LineReporter, duration

BAR_WIDTH = 20
REFRESH = 4  # redraws a second; Live only repaints what changed


def _bar(fraction: float | None) -> Text:
    filled = 0 if fraction is None else round(max(0.0, min(1.0, fraction)) * BAR_WIDTH)
    return Text("█" * filled + "░" * (BAR_WIDTH - filled), style="cyan")


def _progress_line(
    run: Run, fraction: float | None, speed: float | None, eta: timedelta | None
) -> str:
    total = run.options.total_items
    base = (
        t(
            "tui.progress_total",
            handled=run.handled,
            total=max(total, run.handled),
            percent=round(fraction * 100) if fraction is not None else 0,
        )
        if total > 0
        else t("tui.progress_plain", handled=run.handled)
    )
    if speed is None:
        return base
    tail = (
        t("tui.speed", speed=f"{speed:.1f}", eta=duration(eta))
        if eta is not None
        else t("tui.speed_no_eta", speed=f"{speed:.1f}")
    )
    return f"{base}   {tail}"


class TuiReporter:
    """Renders the mock in docs/02-cli-ux.md, driven by the same three ``Reporter`` calls as
    ``LineReporter``. A context manager: enter it around the run (``Live.start``/``stop``),
    matching how ``stop_on_interrupt``/``rt.keys`` already wrap it in ``cli/commands/run.py``.
    """

    def __init__(
        self,
        limits: Limits,
        *,
        src: str,
        dst: str,
        console: Console | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._live = Live(console=console, refresh_per_second=REFRESH, transient=False)
        self._lines = LineReporter(self._live.console.print)  # notices, per-file transfer lines
        self._src, self._dst = src, dst
        self._delay = limits.min_delay
        self._clock = clock
        self._now = now
        self._floods = 0
        self._last_flood: float | None = None
        self._paused = False
        self._run: Run | None = None

    def __enter__(self) -> "TuiReporter":
        self._live.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._live.__exit__(*exc)
        self._live.console.print()  # Live leaves the cursor at the end of its last line, not below

    def notice(self, code: str, **params: object) -> None:
        if code == "paused":
            self._paused = True
        elif code == "resumed":
            self._paused = False
        if code in ("flood_waiting", "throttled"):
            self._floods += 1
            self._last_flood = self._clock()
            if code == "throttled":
                self._delay = float(params["delay"])  # type: ignore[arg-type]
        self._lines.notice(code, **params)
        self._refresh()

    def progress(self, run: Run) -> None:
        self._run = run
        self._refresh()

    def transfer(self, transfer: Transfer) -> None:
        self._lines.transfer(transfer)

    def render(self) -> RenderableType:
        """The current view, without touching ``Live``: what ``_refresh`` pushes to it, and what
        a test reads back (a real terminal is never needed to check this)."""
        run = self._run
        if run is None:
            return Text("")
        est = estimate(run, now=self._now(), live=not self._paused)
        header = t(
            "tui.header",
            id=run.id,
            src=self._src,
            dst=self._dst,
            mode=run.mode,
            delay=f"{self._delay:.1f}",
        )
        progress = _progress_line(run, est.fraction, est.speed, est.eta)
        counts = t("tui.counts", done=run.done, failed=run.failed, skipped=run.skipped_filter)
        if self._floods:
            ago = duration(timedelta(seconds=max(self._clock() - (self._last_flood or 0.0), 0.0)))
            counts += t("tui.floods", count=self._floods, ago=ago)
        return Group(
            Text(header),
            Text.assemble(_bar(est.fraction), "  ", progress),
            Text(counts),
            Text(t("tui.keys"), style="dim"),
        )

    def _refresh(self) -> None:
        self._live.update(self.render())
