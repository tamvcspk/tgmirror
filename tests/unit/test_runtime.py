"""``Runtime.reporter``: how progress is shown while a clone runs, picked the same way as
``Runtime.keys`` (``cli/keys.py``) — both need a real terminal, so both are swapped out in tests
instead of being derived from ``interactive`` (which tests also set to drive wizard prompts on a
``CliRunner`` that is not a real terminal)."""

from tgmirror.cli.runtime import plain_reporter, terminal_reporter
from tgmirror.core.config import Limits
from tgmirror.ui.progress import LineReporter
from tgmirror.ui.tui import TuiReporter


def test_the_default_reporter_is_the_plain_line_one() -> None:
    with plain_reporter(Limits(), "A", "B") as reporter:
        assert isinstance(reporter, LineReporter)


def test_the_terminal_reporter_is_the_rich_live_one() -> None:
    with terminal_reporter(Limits(), "A", "B") as reporter:
        assert isinstance(reporter, TuiReporter)
