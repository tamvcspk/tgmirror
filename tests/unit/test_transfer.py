"""The speed of the file in flight (``TransferTracker``) and how the line reporter shows it and the
run's progress against its total."""

from datetime import UTC, datetime

from tgmirror.core.gateway import ChatKind, TransferPhase
from tgmirror.engine.transfer import Transfer, TransferTracker
from tgmirror.store.runs import Control, Run, RunOptions, RunStatus
from tgmirror.ui.progress import BIG_TRANSFER, LineReporter, size

DOWN, UP = TransferPhase.DOWNLOAD, TransferPhase.UPLOAD
NOW = datetime(2026, 1, 1, tzinfo=UTC)
MB = 1024 * 1024


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def tracker() -> tuple[TransferTracker, list[Transfer], Clock]:
    seen: list[Transfer] = []
    clock = Clock()
    return TransferTracker(seen.append, clock=clock, window=5.0), seen, clock


# ---- the tracker ------------------------------------------------------------------------------


def test_the_first_report_has_no_speed_and_the_next_ones_are_measured() -> None:
    track, seen, clock = tracker()

    track.update(DOWN, 7, 0, 10 * MB)
    clock.now += 2
    track.update(DOWN, 7, 4 * MB, 10 * MB)

    assert seen[0].speed is None
    assert seen[1].speed == 2 * MB  # 4 MB in 2 s
    assert seen[1].fraction == 0.4 and not seen[1].finished


def test_the_speed_is_that_of_the_last_few_seconds_so_a_stall_shows() -> None:
    track, seen, clock = tracker()
    track.update(UP, 7, 0, 100 * MB)
    clock.now += 1
    track.update(UP, 7, 50 * MB, 100 * MB)  # fast start
    clock.now += 10  # then nothing for 10 s
    track.update(UP, 7, 51 * MB, 100 * MB)

    assert seen[-1].speed is not None and seen[-1].speed < 1 * MB  # not the 50 MB/s it began with


def test_a_download_and_an_upload_at_once_are_measured_apart() -> None:
    track, seen, clock = tracker()
    track.update(DOWN, 8, 0, 10 * MB)
    track.update(UP, 7, 0, 10 * MB)
    clock.now += 1
    track.update(DOWN, 8, 8 * MB, 10 * MB)
    track.update(UP, 7, 1 * MB, 10 * MB)

    assert [t.speed for t in seen[2:]] == [8 * MB, 1 * MB]


def test_a_finished_transfer_is_forgotten_and_a_repeat_starts_afresh() -> None:
    track, seen, clock = tracker()
    track.update(DOWN, 7, 0, 4)
    clock.now += 1
    track.update(DOWN, 7, 4, 4)
    assert seen[-1].finished

    track.update(DOWN, 7, 0, 4)  # the same file again (a retry after a FloodWait)
    assert seen[-1].speed is None


def test_a_transfer_that_starts_over_forgets_its_old_speed() -> None:
    track, seen, clock = tracker()
    track.update(DOWN, 7, 0, 100)
    clock.now += 1
    track.update(DOWN, 7, 90, 100)
    track.update(DOWN, 7, 5, 100)  # went backwards

    assert seen[-1].speed is None


# ---- the reporter -----------------------------------------------------------------------------


def big(done: int, phase: TransferPhase = DOWN, msg_id: int = 7) -> Transfer:
    return Transfer(phase, msg_id, done, 20 * MB, 2.0 * MB if done else None)


def test_a_big_file_is_announced_then_repeated_every_interval_and_closed_when_done() -> None:
    lines: list[str] = []
    clock = Clock()
    reporter = LineReporter(lines.append, interval=5.0, clock=clock)

    reporter.transfer(big(0))
    clock.now += 1
    reporter.transfer(big(2 * MB))  # too soon
    clock.now += 5
    reporter.transfer(big(12 * MB))
    reporter.transfer(big(20 * MB))  # done: always said, if it was announced at all

    assert lines == [
        "Downloading message 7: 0% (0 B / 20.0 MB).",
        "Downloading message 7: 60% (12.0 MB / 20.0 MB, 2.0 MB/s).",
        "Downloading message 7: 100% (20.0 MB / 20.0 MB, 2.0 MB/s).",
    ]


def test_a_long_transfer_speaks_when_it_advances_a_few_percent_or_now_and_then() -> None:
    lines: list[str] = []
    clock = Clock()
    reporter = LineReporter(lines.append, interval=5.0, clock=clock)

    reporter.transfer(big(0))  # announced
    for step in range(1, 4):  # 1.25%, 2.5%, 3.75%: less than the 5% that is worth a line
        clock.now += 6
        reporter.transfer(big(step * MB // 4))
    assert len(lines) == 1
    clock.now += 6
    reporter.transfer(big(2 * MB))  # 10% since the first line
    assert len(lines) == 2
    clock.now += 31
    reporter.transfer(big(2 * MB + 1))  # hardly moved, but it is 31 s: it still says so
    assert len(lines) == 3


def test_a_transfer_is_never_shown_as_finished_before_it_is() -> None:
    lines: list[str] = []
    reporter = LineReporter(lines.append)

    reporter.transfer(big(0))
    reporter.transfer(Transfer(DOWN, 7, 20 * MB - 100, 20 * MB, 2.0 * MB))  # 99.999%
    reporter.transfer(big(20 * MB))

    assert [line.split(":")[1].split("%")[0].strip() for line in lines] == ["0", "100"]


def test_an_upload_is_worded_as_one_and_a_small_file_says_nothing() -> None:
    lines: list[str] = []
    reporter = LineReporter(lines.append)

    reporter.transfer(big(4 * MB, UP))
    reporter.transfer(Transfer(DOWN, 9, 100, BIG_TRANSFER - 1, None))  # too small to matter

    assert lines == ["Uploading message 7: 20% (4.0 MB / 20.0 MB, 2.0 MB/s)."]


def test_a_file_that_was_never_announced_is_not_closed_either() -> None:
    lines: list[str] = []

    LineReporter(lines.append).transfer(big(20 * MB))  # arrived complete in one go

    assert lines == []


def test_sizes_are_written_the_way_people_read_them() -> None:
    assert [size(n) for n in (0, 512, 1024, 1536, 5 * MB, 3 * 1024 * MB)] == [
        "0 B",
        "512 B",
        "1.0 KB",
        "1.5 KB",
        "5.0 MB",
        "3.0 GB",
    ]


def run(copied: int, total: int, *, skipped: int = 0) -> Run:
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
        options=RunOptions(total_items=total),
        status=RunStatus.RUNNING,
        control=Control.NONE,
        cursor_from=0,
        cursor_src_id=copied,
        resume_at=None,
        fail_reason=None,
        stats={"done": copied, "skipped_filter": skipped},
        started_at=NOW,
        ended_at=None,
        updated_at=NOW,
    )


def test_progress_says_x_of_y_when_the_total_is_known() -> None:
    lines: list[str] = []

    LineReporter(lines.append).progress(run(40, 200, skipped=10))

    assert lines == [
        "Run 3: 50/200 messages (~25%): 40 copied, 10 left out by the filter, 0 failed."
    ]


def test_a_total_that_turns_out_too_small_never_shows_more_than_100_percent() -> None:
    lines: list[str] = []

    LineReporter(lines.append).progress(run(12, 10))  # messages were posted while it ran

    assert "12/12 messages (~100%)" in lines[0]
