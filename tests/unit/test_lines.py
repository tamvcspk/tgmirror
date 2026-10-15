"""What a flood event is shown with (phase 19d1): the lane it hit and what the lane had sent."""

from datetime import UTC, datetime

from tgmirror.store.runs import FloodEvent
from tgmirror.ui.lines import flood_kind, flood_where

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
