"""``BackupTuiReporter`` (``ui/menu/backup_screen.py``): what it renders from the same three
``Reporter`` calls as ``BackupLineReporter``, without touching a terminal — mirrors
``test_tui.py`` for ``TuiReporter``, now that a backup shares the same bar/progress-line/floods
building blocks (``ui/progress.py``) once it has a total
(``engine/backup.py::BackupWriter._analyze``).

``english_messages`` (``tests/conftest.py``, autouse) puts ``t()`` in English for every test here.
"""

import io
from datetime import UTC, datetime, timedelta

from rich.console import Console, RenderableType

from tgmirror.core.gateway import ChatKind, TransferPhase
from tgmirror.engine.transfer import Transfer
from tgmirror.store.backups import Backup
from tgmirror.store.runs import Control, RunStatus
from tgmirror.ui.menu.backup_screen import BackupTuiReporter

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
MB = 1024 * 1024


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, width=200, no_color=True)
    console.print(renderable)
    return console.export_text()


def backup(
    done: int,
    total: int,
    *,
    skipped: int = 0,
    started_at: datetime = NOW,
    updated_at: datetime = NOW,
    status: RunStatus = RunStatus.RUNNING,
) -> Backup:
    return Backup(
        id=7,
        account="default",
        src_id=-1,
        src_title="Kenh A",
        src_kind=ChatKind.BROADCAST,
        dir="/backups/kenh-a",
        filters_json="{}",
        status=status,
        control=Control.NONE,
        cursor_to=done,
        resume_at=None,
        fail_reason=None,
        stats={"done": done, "skipped_filter": skipped, "total_items": total},
        started_at=started_at,
        ended_at=None,
        updated_at=updated_at,
    )


class Clock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_a_known_total_draws_a_bar_and_percent_like_a_run_does() -> None:
    r = BackupTuiReporter(backup(0, 100))

    assert "0/100 messages (~0%)" in plain(r.render())


def test_not_yet_analyzed_falls_back_to_the_plain_count() -> None:
    """A backup started before this feature existed, or whose count call failed, still shows
    something sane instead of a bar that can never fill (``total_items`` stays ``0``)."""
    r = BackupTuiReporter(backup(3, 0))

    text = plain(r.render())
    assert "3 messages handled" in text
    assert "%" not in text


def test_speed_and_eta_come_from_engine_status_backup_estimate() -> None:
    started = NOW - timedelta(seconds=40)
    r = BackupTuiReporter(backup(40, 200, started_at=started, updated_at=NOW), now=lambda: NOW)

    assert "1.0 msg/s, ETA" in plain(r.render())  # 40 messages in 40 s


def test_a_notice_is_shown_in_render_not_printed_anywhere() -> None:
    """As ``TuiReporter``'s silent mode: the menu draws its own alt-screen ``Live``, so a notice
    must only ever show up through ``render()``. Before this, ``BackupTuiReporter.notice()``
    dropped everything but ``paused``/``resumed``/flood counting — a "gone" line never reached
    the menu at all."""
    r = BackupTuiReporter(backup(1, 10))

    r.notice("gone", id=42)

    assert "Message 42" in plain(r.render())


def test_a_transfer_is_shown_the_same_way_a_run_download_is() -> None:
    """Used to be invisible (``BackupTuiReporter.transfer`` was ``pass``, phase 11a v1)."""
    r = BackupTuiReporter(backup(0, 100))

    r.transfer(Transfer(TransferPhase.DOWNLOAD, 42, 0, 20 * MB, None))
    r.transfer(Transfer(TransferPhase.DOWNLOAD, 42, 20 * MB, 20 * MB, 2.0 * MB))

    assert "Downloading message 42: 100%" in plain(r.render())


def test_two_floods_are_counted_and_the_clock_ages_the_last_one() -> None:
    clock = Clock()
    r = BackupTuiReporter(backup(10, 100), clock=clock)

    r.notice("flood_waiting", seconds=30)
    clock.now += 38
    r.notice("throttled", batch_size=5, delay=3.0)

    assert "2 throttled (last 0 s ago)" in plain(r.render())


def test_paused_shows_the_paused_line() -> None:
    r = BackupTuiReporter(backup(1, 10))

    r.notice("paused")

    assert "Paused." in plain(r.render())
