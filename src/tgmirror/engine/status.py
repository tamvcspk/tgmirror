"""What ``tgmirror status`` shows about a run: progress, speed, ETA and the state of the limits.

It reads only the store (no Telegram connection): the clone that is running holds the session file,
so a second terminal could not connect anyway. That is why the total is recorded when a run begins
(``RunOptions.src_last_id``) instead of being asked of Telegram now.

Every figure is an estimate, and says so:

- **Progress** of an ordinary run is the messages it has dealt with (copied, failed, left out by the
  filter or by strategy B, or already there) against the count Telegram gave when the run began
  (``RunOptions.total_items``, an upper bound: the filter is not subtracted, so a run that skips a
  lot ends below 100% until it is done). A run from before the count was recorded falls back to the
  source *message id*, from where the run began to the newest id at that moment: ids have gaps, so
  that is a rough fraction, not a count of messages.
- **Progress** of a retry is exact: the failed messages it has handled against those still waiting.
- **Speed** is the average since the run began (pauses and flood waits included): messages copied
  or failed per second.
- **ETA** extrapolates that average and is offered only while the run is really running. It cannot
  know how big the files ahead are, so a run of mixed sizes is only roughly right.
- **Daily cap**: what is left, against what the cap still allows today, says how many more days of
  rest the cap alone will cost (an upper bound too).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from tgmirror.store.db import HEARTBEAT_TIMEOUT, Store
from tgmirror.store.runs import FloodEvent, Run, RunStatus

MIN_SAMPLE = 5.0  # seconds a run must have lasted before its speed and ETA mean anything
FLOOD_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Estimate:
    fraction: float | None  # 0..1; ``None``: the total is not known
    speed: float | None  # messages per second, ``None``: too early to tell
    eta: timedelta | None


@dataclass(frozen=True, slots=True)
class StatusReport:
    run: Run
    now: datetime
    live: bool  # a process holds the run (``running``/``paused`` with a fresh heartbeat)
    abandoned: bool  # it says ``running``/``paused`` but nothing has beaten for a while: it died
    estimate: Estimate
    failed_now: int  # messages still ``failed`` from this run: what ``retry`` would send
    retry_left: int | None  # a retry: failed messages of the run it retries not handled yet
    delay: float | None  # the limiter's current delay between writes; ``None``: never ran
    sent_today: int
    daily_cap: int
    floods_24h: int
    last_flood: FloodEvent | None  # the run's own most recent rate-limit event
    left: int | None  # messages the run still has to look at (at most); ``None``: total not known
    cap_days: int  # days of rest the daily cap will cost before that is done (0: it fits today)


def estimate(run: Run, now: datetime, *, live: bool, retry_left: int | None = None) -> Estimate:
    """Progress, speed and ETA of ``run`` as of ``now`` (see the module docstring for the caveats).

    ``retry_left`` is required for a retry: how many failed messages it has yet to handle.
    """
    end = now if live else (run.ended_at or run.updated_at)
    elapsed = max((end - run.started_at).total_seconds(), 0.0)
    handled = run.done + run.failed + run.gone
    speed = handled / elapsed if elapsed >= MIN_SAMPLE and handled else None

    if run.options.retry_of is not None:
        span, progressed = handled + (retry_left or 0), handled
    elif run.options.total_items > 0:
        span, progressed = max(run.options.total_items, run.handled), run.handled
    elif run.options.src_last_id > 0:
        head = run.options.src_last_id
        span = head - run.cursor_from
        progressed = min(run.cursor_src_id, head) - run.cursor_from
    else:
        span = progressed = 0  # unknown total (a run from before the total was recorded)

    if run.status is RunStatus.DONE:
        fraction: float | None = 1.0
    elif span > 0:
        fraction = min(max(progressed / span, 0.0), 1.0)
    else:
        fraction = None

    eta = None
    if run.status is RunStatus.RUNNING and live and elapsed >= MIN_SAMPLE and 0 < progressed < span:
        eta = timedelta(seconds=elapsed * (span - progressed) / progressed)
    return Estimate(fraction, speed, eta)


async def build_report(store: Store, run: Run, *, now: datetime, daily_cap: int) -> StatusReport:
    """Gather what ``status`` needs about ``run`` from the store."""
    holds = run.status in (RunStatus.RUNNING, RunStatus.PAUSED)
    live = holds and now - run.updated_at < HEARTBEAT_TIMEOUT
    retry_of = run.options.retry_of
    retry_left = await store.count_failed(retry_of) if retry_of is not None else None
    limiter = await store.load_limiter_state(run.account)
    today = now.astimezone().date()
    floods = await store.flood_events(run.id)
    sent_today = limiter.sent_today if limiter and limiter.day == today else 0
    left = _left(run, retry_left)
    return StatusReport(
        run=run,
        now=now,
        live=live,
        abandoned=holds and not live,
        estimate=estimate(run, now, live=live, retry_left=retry_left),
        failed_now=await store.count_failed(run.id),
        retry_left=retry_left,
        delay=limiter.delay if limiter else None,
        # the count belongs to the day it was made; a new local day starts it over
        sent_today=sent_today,
        daily_cap=daily_cap,
        floods_24h=await store.flood_count_since(now - FLOOD_WINDOW),
        last_flood=floods[-1] if floods else None,
        left=left,
        cap_days=cap_days(left, daily_cap, sent_today) if left and (live or _waiting(run)) else 0,
    )


def _waiting(run: Run) -> bool:
    return run.status is RunStatus.WAITING_FLOOD


def _left(run: Run, retry_left: int | None) -> int | None:
    """How many messages the run still has to look at, at most."""
    if run.options.retry_of is not None:
        return retry_left
    if run.options.total_items > 0:
        return max(run.options.total_items - run.handled, 0)
    return None


def cap_days(left: int, daily_cap: int, sent_today: int) -> int:
    """Whole days of rest the daily cap costs for ``left`` messages, given what went out today."""
    allowed_today = max(daily_cap - sent_today, 0)
    return max(-(-(left - allowed_today) // daily_cap), 0)  # ceil, never negative
