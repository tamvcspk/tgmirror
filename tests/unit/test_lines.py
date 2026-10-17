"""What a flood event is shown with (phase 19d1): the lane it hit and what the lane had sent."""

from datetime import UTC, datetime

from tgmirror.engine.status import BudgetInfo, StatusReport
from tgmirror.store.runs import FloodEvent
from tgmirror.ui.lines import budget_lines, flood_kind, flood_where, rest_phrase
from tgmirror.ui.messages import t
from tgmirror.ui.progress import LineReporter

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def test_a_write_flood_says_its_lane_destination_and_what_it_had_sent() -> None:
    event = FloodEvent(NOW, "flood_wait", 40, "copy_messages", "forward", -1001, 120, 900)

    assert flood_kind(event) == "flood_wait/forward"
    assert flood_where(event) == " [forward -> -1001, 120/1h 900/24h]"


def test_a_read_flood_has_a_lane_but_no_destination_or_counts() -> None:
    event = FloodEvent(NOW, "flood_wait", 10, "iter_messages", "history")

    assert flood_kind(event) == "flood_wait/history"
    assert flood_where(event) == " [history]"


def test_a_row_from_before_the_lanes_is_shown_as_it_always_was() -> None:
    event = FloodEvent(NOW, "flood_wait", 30, "copy_messages")

    assert flood_kind(event) == "flood_wait"
    assert flood_where(event) == ""


def test_a_rest_is_told_in_hours_and_in_whole_days_only_when_it_is_exactly_that() -> None:
    assert rest_phrase(2) == t("rest.hours", n=2)
    assert rest_phrase(26) == t("rest.hours", n=26)
    assert rest_phrase(24) == t("rest.days", n=1)
    assert rest_phrase(48) == t("rest.days", n=2)
    assert t("rest.hours", {"TGMIRROR_LANG": "vi"}, n=2) == "2 giờ"
    assert t("rest.days", {"TGMIRROR_LANG": "vi"}, n=2) == "2 ngày"


def test_the_line_reporter_tells_the_rest_in_hours_for_a_one_hour_budget() -> None:
    said: list[str] = []

    LineReporter(said.append).notice("cap_rest", total=3082, budget=1506, window=1, hours=2)
    LineReporter(said.append).notice("cap_rest", total=9000, budget=3, window=24, hours=48)

    assert "about 2 h more" in said[0] and "day" not in said[0]
    assert "about 2 day(s) more" in said[1]


def _report(*budgets: BudgetInfo) -> StatusReport:
    """Only what ``budget_lines`` reads: the budgets, the delay and the (absent) rest."""
    return StatusReport(
        run=None,  # type: ignore[arg-type]
        now=NOW,
        live=True,
        abandoned=False,
        estimate=None,  # type: ignore[arg-type]
        failed_now=0,
        retry_left=None,
        delay=2.0,
        sent_24h=0,
        daily_cap=5000,
        budgets=budgets,
        floods_24h=0,
        last_flood=None,
        left=None,
        cap_rest_hours=0,
    )


def test_status_tells_where_a_limit_was_blocked_and_how_far_it_ran_clean() -> None:
    info = BudgetInfo("forward", 1506, 1, 120, NOW, 1800, ok=1506, bad=1882, checked_at=NOW)

    lines = budget_lines(_report(info))

    assert any("forward: 1506 messages per 1 h (120 sent)" in line for line in lines)
    assert any("blocked at 1882 messages, ran clean up to 1506" in line for line in lines)


def test_status_has_no_limit_line_for_a_budget_that_knows_neither_side() -> None:
    info = BudgetInfo("forward", 1506, 1, 120, NOW, 1800)

    lines = budget_lines(_report(info))

    assert len(lines) == 1 and "Budget:" in lines[0]
