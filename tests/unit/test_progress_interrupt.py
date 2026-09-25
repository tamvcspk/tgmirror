"""The line reporter, the Ctrl+C handler and the hotkeys of ``clone`` and ``run``."""

import signal
import threading
from datetime import UTC, datetime

import pytest

from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.cli.keys import apply_key, no_keys, pump_keys
from tgmirror.core.gateway import ChatKind
from tgmirror.engine.runner import RunControl
from tgmirror.store.runs import Control, Run, RunOptions, RunStatus
from tgmirror.ui.progress import LineReporter

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def run(done: int, cursor: int) -> Run:
    return Run(
        id=3,
        mirror_id=1,
        account="default",
        src_id=-1,
        src_title="s",
        src_kind=ChatKind.BROADCAST,
        dst_kind=ChatKind.BROADCAST,
        dst_id=-2,
        dst_title="d",
        mode="auto",
        filters_json="{}",
        options=RunOptions(),
        status=RunStatus.RUNNING,
        control=Control.NONE,
        cursor_from=0,
        cursor_src_id=cursor,
        resume_at=None,
        fail_reason=None,
        stats={"done": done, "failed": 1},
        started_at=NOW,
        ended_at=None,
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

    reporter.progress(run(20, 20))  # first one always shows
    clock.now += 1
    reporter.progress(run(40, 40))  # too soon
    reporter.notice("reconciled", count=3)  # notices are always shown
    clock.now += 5
    reporter.progress(run(60, 60))

    assert lines[0] == "Run 3: 20 messages copied, 1 failed (source up to id 20)."
    assert "3 messages are already in the destination" in lines[1]
    assert lines[2] == "Run 3: 60 messages copied, 1 failed (source up to id 60)."
    assert len(lines) == 3


def test_a_flood_notice_shows_the_time_in_local_terms() -> None:
    lines: list[str] = []

    LineReporter(lines.append).notice("flood_stopped", seconds=90, resume_at=NOW)

    assert "90s" in lines[0] and "2026-01-01" in lines[0] and "+00:00" not in lines[0]


def test_the_notices_of_pausing_and_resuming_are_worded() -> None:
    lines: list[str] = []
    reporter = LineReporter(lines.append)

    reporter.notice("paused")
    reporter.notice("resumed")

    assert "Press r" in lines[0] and lines[1] == "Resumed."


def test_the_first_ctrl_c_asks_to_stop_and_the_handler_is_restored() -> None:
    control, told = RunControl(), []
    before = signal.getsignal(signal.SIGINT)

    with stop_on_interrupt(control, lambda: told.append(True)) as interruption:
        signal.raise_signal(signal.SIGINT)
        assert control.stop_requested and told == [True] and interruption.hit

    assert signal.getsignal(signal.SIGINT) is before


def test_the_second_ctrl_c_quits_at_once() -> None:
    control = RunControl()

    with pytest.raises(KeyboardInterrupt), stop_on_interrupt(control, lambda: None):
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)

    assert signal.getsignal(signal.SIGINT) is not None  # restored, not left as our handler


def test_stopping_with_a_key_is_not_a_ctrl_c() -> None:
    control = RunControl()

    with stop_on_interrupt(control, lambda: None) as interruption:
        apply_key(control, "q")

    assert control.stop_requested and not interruption.hit  # so the exit code stays 0


def test_the_notices_of_a_flood_wait_and_of_throttling_are_worded() -> None:
    lines: list[str] = []
    reporter = LineReporter(lines.append)

    reporter.notice("flood_waiting", seconds=42)
    reporter.notice("throttled", batch_size=5, delay=16.0)

    assert "42s" in lines[0] and "same batch" in lines[0]
    assert "5 messages" in lines[1] and "16.0s" in lines[1]


# ---- hotkeys --------------------------------------------------------------------------------


def test_p_r_and_q_drive_the_run_control_in_either_case() -> None:
    control = RunControl()

    assert apply_key(control, "p") and control.pause_requested
    assert apply_key(control, "R") and not control.pause_requested and control.take_resume()
    assert not control.take_resume()  # a resume is reported once
    assert apply_key(control, "Q") and control.stop_requested


def test_other_keys_mean_nothing() -> None:
    control = RunControl()

    assert not any(apply_key(control, key) for key in ("x", "\r", " ", "1"))
    assert not (control.pause_requested or control.stop_requested)


def test_a_pause_asked_again_after_a_resume_is_a_pause() -> None:
    control = RunControl()

    apply_key(control, "p")
    apply_key(control, "r")
    apply_key(control, "p")

    assert control.pause_requested and not control.take_resume()


class ScriptedKeys:
    """A ``KeyReader`` that hands out a script and then asks the pump to stop."""

    def __init__(self, keys: str, stop: threading.Event) -> None:
        self._keys = list(keys)
        self._stop = stop

    def read(self, timeout: float) -> str | None:
        if not self._keys:
            self._stop.set()
            return None
        return self._keys.pop(0)

    def close(self) -> None:
        pass


def test_the_key_pump_feeds_a_reader_into_the_control_until_told_to_stop() -> None:
    control, stop = RunControl(), threading.Event()

    pump_keys(ScriptedKeys("xpq", stop), control, stop)

    assert control.pause_requested and control.stop_requested


def test_without_a_terminal_there_are_no_hotkeys() -> None:
    with no_keys(RunControl()) as listening:
        assert listening is False
