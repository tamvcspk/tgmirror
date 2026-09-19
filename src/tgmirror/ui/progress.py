"""Plain-line progress for ``tgmirror run`` (implements ``engine.runner.Reporter``).

Works without a terminal: no ANSI, one line at most every ``interval`` seconds, and notices
(reconcile results, flood stop) always. The Rich live view with keys is phase 7
(docs/06-lo-trinh.md); ``docs/02-cli-ux.md`` describes it.
"""

import time
from collections.abc import Callable
from datetime import datetime

from tgmirror.store.jobs import Job
from tgmirror.ui.messages import t


def _plain(value: object) -> object:
    """Datetimes are stored in UTC; people read local time."""
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return value


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

    def notice(self, code: str, **params: object) -> None:
        self._emit(t(f"run.{code}", **{k: _plain(v) for k, v in params.items()}))

    def progress(self, job: Job) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return
        self._last = now
        self._emit(
            t("run.progress", id=job.id, done=job.done, failed=job.failed, cursor=job.cursor_src_id)
        )
