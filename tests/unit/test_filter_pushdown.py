"""What gets pushed down to Telegram, and (above all) what must not be."""

from datetime import UTC, datetime
from typing import Any

import pytest

from tgmirror.core.gateway import ALBUM_MARGIN, NO_FILTER, MediaKind, ServerFilter
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import PUSHABLE_MEDIA, ReadPlan, plan_read


def plan(data: dict[str, Any], cursor: int = 0, **kw: Any) -> ReadPlan:
    return plan_read(FilterSpec.from_data(data), cursor, **kw)


def test_an_empty_filter_pushes_nothing_down() -> None:
    assert plan({}) == ReadPlan(0, NO_FILTER, complete_albums=False)
    assert plan({}, cursor=40).min_id == 40


@pytest.mark.parametrize("kind", sorted(PUSHABLE_MEDIA))
def test_one_rule_with_one_pushable_media_kind_becomes_a_server_filter(kind: MediaKind) -> None:
    result = plan({"include": [{"media": [kind.value]}]})

    assert result.server.media is kind and result.complete_albums


@pytest.mark.parametrize("kind", [k for k in MediaKind if k not in PUSHABLE_MEDIA])
def test_kinds_without_a_safe_telegram_filter_are_not_pushed(kind: MediaKind) -> None:
    result = plan({"include": [{"media": [kind.value]}]})

    assert result.server == NO_FILTER and not result.complete_albums


def test_document_is_not_pushed_because_our_kind_is_wider_than_telegrams() -> None:
    assert MediaKind.DOCUMENT not in PUSHABLE_MEDIA  # dice, stories... are DOCUMENT for us


def test_one_hashtag_becomes_a_search() -> None:
    result = plan({"include": [{"hashtag": ["#News"]}]})

    assert result.server.search == "#news" and result.complete_albums


def test_media_and_hashtag_in_one_rule_are_both_pushed() -> None:
    result = plan({"include": [{"media": ["video"], "hashtag": ["#a"]}]})

    assert (result.server.media, result.server.search) == (MediaKind.VIDEO, "#a")


@pytest.mark.parametrize(
    "include",
    [
        [{"media": ["video", "photo"]}],  # any-of: one filter cannot express it
        [{"hashtag": ["#a", "#b"]}],
        [{"contains": ["word"]}],  # "substring" for us, "word" for Telegram's search
        [{"regex": "x"}],
        [{"media": ["video"]}, {"media": ["photo"]}],  # several rules are ORed
    ],
)
def test_what_cannot_be_narrowed_safely_is_left_to_the_client(
    include: list[dict[str, Any]],
) -> None:
    result = plan({"include": include})

    assert result.server == NO_FILTER and not result.complete_albums


def test_excludes_are_never_pushed() -> None:
    assert plan({"exclude": [{"media": ["video"]}]}).server == NO_FILTER


def test_id_bounds_get_a_margin_so_a_boundary_album_stays_whole() -> None:
    result = plan({"id": {"from": 100, "to": 200}})

    assert result.min_id == 100 - 1 - ALBUM_MARGIN
    assert result.server.max_id == 200 + ALBUM_MARGIN
    assert not result.complete_albums  # no content narrowing: nothing was dropped inside albums


def test_the_cursor_wins_over_an_earlier_id_bound() -> None:
    assert plan({"id": {"from": 100}}, cursor=5000).min_id == 5000
    assert plan({"id": {"from": 5}}).min_id == 0  # never negative


def test_dates_are_handed_to_the_gateway() -> None:
    result = plan({"date": {"from": "2024-01-01", "to": "2025-01-01"}})

    assert result.server == ServerFilter(
        since=datetime(2024, 1, 1, tzinfo=UTC), until=datetime(2025, 1, 1, tzinfo=UTC)
    )


def test_pushdown_can_be_switched_off() -> None:
    result = plan(
        {"include": [{"media": ["video"]}], "id": {"from": 100}, "date": {"from": "2024-01-01"}},
        cursor=7,
        pushdown=False,
    )

    assert result == ReadPlan(7, NO_FILTER, complete_albums=False)


def test_a_preview_keeps_the_range_but_not_the_content_narrowing() -> None:
    result = plan(
        {"include": [{"media": ["video"], "hashtag": ["#a"]}], "id": {"from": 100}}, content=False
    )

    assert (result.server.media, result.server.search) == (None, None)
    assert result.min_id == 100 - 1 - ALBUM_MARGIN and not result.complete_albums
