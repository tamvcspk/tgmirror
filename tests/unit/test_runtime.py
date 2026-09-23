"""``Runtime.reporter``: how progress is shown while a clone runs, picked the same way as
``Runtime.keys`` (``cli/keys.py``) — both need a real terminal, so both are swapped out in tests
instead of being derived from ``interactive`` (which tests also set to drive wizard prompts on a
``CliRunner`` that is not a real terminal)."""

from datetime import UTC, datetime

from tgmirror.cli.runtime import plain_reporter, terminal_reporter
from tgmirror.core.config import Limits
from tgmirror.core.gateway import ChatKind
from tgmirror.store.runs import Control, Run, RunOptions, RunStatus
from tgmirror.ui.progress import LineReporter
from tgmirror.ui.tui import TuiReporter

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def a_run() -> Run:
    return Run(
        id=1,
        mirror_id=1,
        account="default",
        src_id=-1,
        src_title="A",
        src_kind=ChatKind.BROADCAST,
        dst_id=-2,
        dst_title="B",
        mode="copy",
        filters_json="{}",
        options=RunOptions(),
        status=RunStatus.RUNNING,
        control=Control.NONE,
        cursor_from=0,
        cursor_src_id=0,
        resume_at=None,
        fail_reason=None,
        stats={},
        started_at=NOW,
        ended_at=None,
        updated_at=NOW,
    )


def test_the_default_reporter_is_the_plain_line_one() -> None:
    with plain_reporter(Limits(), a_run()) as reporter:
        assert isinstance(reporter, LineReporter)


def test_the_terminal_reporter_is_the_rich_live_one() -> None:
    with terminal_reporter(Limits(), a_run()) as reporter:
        assert isinstance(reporter, TuiReporter)
