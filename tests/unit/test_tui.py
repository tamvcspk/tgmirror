"""The Rich Live view (``ui/tui.py``): what it renders from the same three ``Reporter`` calls as
``LineReporter``, without ever touching a real terminal (``render()`` is pure, ``Live`` untouched).

``english_messages`` (``tests/conftest.py``, autouse) puts ``t()`` in English for every test here.
"""

import io
from datetime import UTC, datetime, timedelta

from rich.console import Console, RenderableType

from tgmirror.core.config import Limits
from tgmirror.core.gateway import ChatKind, TransferPhase
from tgmirror.engine.transfer import Transfer
from tgmirror.store.runs import Control, Run, RunOptions, RunStatus
from tgmirror.ui.tui import TuiReporter

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, width=200, no_color=True)
    console.print(renderable)
    return console.export_text()


def run(
    done: int,
    total: int,
    *,
    failed: int = 0,
    skipped: int = 0,
    started_at: datetime = NOW,
    updated_at: datetime = NOW,
    mode: str = "copy",
) -> Run:
    return Run(
        id=7,
        mirror_id=1,
        account="default",
        src_id=-1,
        src_title="Kenh A",
        src_kind=ChatKind.BROADCAST,
        dst_kind=ChatKind.BROADCAST,
        dst_id=-2,
        dst_title="Kenh A (copy)",
        mode=mode,
        filters_json="{}",
        options=RunOptions(total_items=total),
        status=RunStatus.RUNNING,
        control=Control.NONE,
        cursor_from=0,
        cursor_src_id=done,
        resume_at=None,
        fail_reason=None,
        stats={"done": done, "failed": failed, "skipped_filter": skipped},
        started_at=started_at,
        ended_at=None,
        updated_at=updated_at,
    )


class Clock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def reporter(
    seed: Run,
    *,
    console: Console | None = None,
    clock: Clock | None = None,
    now: object = None,
) -> TuiReporter:
    kwargs: dict[str, object] = {}
    if clock is not None:
        kwargs["clock"] = clock
    if now is not None:
        kwargs["now"] = now
    return TuiReporter(Limits(), seed, console=console, **kwargs)


def test_the_seed_run_is_shown_immediately_without_a_progress_call() -> None:
    """The bug found on a real reupload run (docs/06-lo-trinh.md, 2026-09-23): a run that never
    commits a batch must not leave the panel blank the whole time."""
    text = plain(reporter(run(0, 100)).render())

    assert 'tgmirror ▸ run 7  "Kenh A → Kenh A (copy)"' in text
    assert "0/100 messages (~0%)" in text


def test_the_header_names_the_run_the_pair_and_the_mode() -> None:
    r = reporter(run(0, 100))

    assert 'tgmirror ▸ run 7  "Kenh A → Kenh A (copy)"   mode=copy   delay=2.0s' in plain(
        r.render()
    )


def test_the_progress_line_counts_every_handled_message_not_only_done() -> None:
    r = reporter(run(40, 200, failed=3, skipped=10))  # handled = 40 + 3 + 10 = 53

    assert "53/200 messages (~26%)" in plain(r.render())


def test_the_counts_line_breaks_handled_down_by_outcome() -> None:
    r = reporter(run(40, 200, failed=3, skipped=10))

    assert "40 copied · 3 failed · 10 filtered out" in plain(r.render())


def test_speed_and_eta_come_from_engine_status_estimate() -> None:
    started = NOW - timedelta(seconds=40)
    r = reporter(run(40, 200, started_at=started, updated_at=NOW), now=lambda: NOW)

    assert "1.0 msg/s, ETA" in plain(r.render())  # 40 messages in 40 s


def test_too_early_for_a_speed_says_nothing_about_it() -> None:
    r = reporter(run(1, 200, started_at=NOW, updated_at=NOW), now=lambda: NOW)  # 0 s elapsed

    text = plain(r.render())
    assert "msg/s" not in text and "ETA" not in text


def test_throttled_raises_the_shown_delay_and_counts_as_a_flood() -> None:
    r = reporter(run(10, 100), clock=Clock())

    r.notice("throttled", batch_size=5, delay=7.5)

    text = plain(r.render())
    assert "delay=7.5s" in text
    assert "1 throttled (last 0 s ago)" in text


def test_flood_waiting_counts_as_a_flood_without_changing_the_delay() -> None:
    r = reporter(run(10, 100), clock=Clock())

    r.notice("flood_waiting", seconds=30)

    text = plain(r.render())
    assert f"delay={Limits().min_delay:.1f}s" in text
    assert "1 throttled" in text


def test_two_floods_are_counted_and_the_clock_ages_the_last_one() -> None:
    clock = Clock()
    r = reporter(run(10, 100), clock=clock)

    r.notice("flood_waiting", seconds=30)
    clock.now += 38
    r.notice("throttled", batch_size=5, delay=3.0)

    assert "2 throttled (last 0 s ago)" in plain(r.render())


def test_a_notice_prints_above_the_panel_like_the_line_reporter() -> None:
    console = Console(file=io.StringIO(), record=True, width=200)
    r = reporter(run(1, 10), console=console)

    r.notice("reconciled", count=3)

    assert "3 messages" in console.export_text()


def test_a_transfer_refreshes_the_panel_so_a_long_download_does_not_look_frozen() -> None:
    r = reporter(run(0, 100))
    before = plain(r.render())
    r.notice("throttled", batch_size=5, delay=9.0)  # something to see change after a transfer call

    r.transfer(Transfer(TransferPhase.DOWNLOAD, 42, 1024, 4096, None))

    assert plain(r.render()) != before  # delay=9.0s now visible, proving the panel is live


def test_paused_freezes_the_speed_at_the_last_update_instead_of_ageing_it() -> None:
    started = NOW - timedelta(seconds=100)
    frozen_at = NOW - timedelta(seconds=50)

    # "now" keeps moving well past the pause...
    r = reporter(run(10, 100, started_at=started, updated_at=frozen_at), now=lambda: NOW)
    r.notice("paused")

    # ...but the speed does not: 10 messages over the 50 s up to the last commit, not to "now"
    assert "0.2 msg/s" in plain(r.render())


def test_paused_shows_no_eta_a_run_that_is_not_moving_has_none() -> None:
    seed = run(10, 100, started_at=NOW - timedelta(seconds=100), updated_at=NOW)
    r = reporter(seed, now=lambda: NOW)
    r.notice("paused")

    assert "ETA" not in plain(r.render())


def test_resumed_lets_the_estimate_move_again() -> None:
    started = NOW - timedelta(seconds=100)

    r = reporter(run(10, 100, started_at=started, updated_at=NOW), now=lambda: NOW)
    r.notice("paused")
    r.notice("resumed")

    # a live estimate uses "now", not "updated_at": speed is 10 messages over the full 100 s
    assert "0.1 msg/s" in plain(r.render())
