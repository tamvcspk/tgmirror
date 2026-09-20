"""The line reporter and the Ctrl+C handler of ``tgmirror run``."""

import signal
from datetime import UTC, datetime

import pytest

from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.core.gateway import ChatKind
from tgmirror.engine.runner import StopSignal
from tgmirror.store.jobs import Control, Job, JobOptions, JobStatus
from tgmirror.ui.progress import LineReporter

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def job(done: int, cursor: int) -> Job:
    return Job(
        id=3,
        name="n",
        account="default",
        src_id=-1,
        src_title="s",
        src_kind=ChatKind.BROADCAST,
        dst_id=-2,
        dst_title="d",
        mode="auto",
        filters_json="{}",
        options=JobOptions(),
        status=JobStatus.RUNNING,
        control=Control.NONE,
        cursor_src_id=cursor,
        resume_at=None,
        fail_reason=None,
        stats={"done": done, "failed": 1},
        created_at=NOW,
        updated_at=NOW,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_progress_lines_are_throttled_but_notices_are_not() -> None:
    lines: list[str] = []
    clock = FakeClock()
    reporter = LineReporter(lines.append, interval=5.0, clock=clock)

    reporter.progress(job(20, 20))  # first one always shows
    clock.now += 1
    reporter.progress(job(40, 40))  # too soon
    reporter.notice("reconciled", count=3)  # notices are always shown
    clock.now += 5
    reporter.progress(job(60, 60))

    assert lines[0] == "Job 3: 20 messages copied, 1 failed (source up to id 20)."
    assert "3 messages are already in the destination" in lines[1]
    assert lines[2] == "Job 3: 60 messages copied, 1 failed (source up to id 60)."
    assert len(lines) == 3


def test_a_flood_notice_shows_the_time_in_local_terms() -> None:
    lines: list[str] = []

    LineReporter(lines.append).notice("flood_stopped", seconds=90, resume_at=NOW)

    assert "90s" in lines[0] and "2026-01-01" in lines[0] and "+00:00" not in lines[0]


def test_the_first_ctrl_c_asks_to_stop_and_the_handler_is_restored() -> None:
    stop, told = StopSignal(), []
    before = signal.getsignal(signal.SIGINT)

    with stop_on_interrupt(stop, lambda: told.append(True)):
        signal.raise_signal(signal.SIGINT)
        assert stop.requested and told == [True]

    assert signal.getsignal(signal.SIGINT) is before


def test_the_second_ctrl_c_quits_at_once() -> None:
    stop = StopSignal()

    with pytest.raises(KeyboardInterrupt), stop_on_interrupt(stop, lambda: None):
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)

    assert signal.getsignal(signal.SIGINT) is not None  # restored, not left as our handler


def test_the_notices_of_a_flood_wait_and_of_throttling_are_worded() -> None:
    lines: list[str] = []
    reporter = LineReporter(lines.append)

    reporter.notice("flood_waiting", seconds=42)
    reporter.notice("throttled", batch_size=5, delay=16.0)

    assert "42s" in lines[0] and "same batch" in lines[0]
    assert "5 messages" in lines[1] and "16.0s" in lines[1]
