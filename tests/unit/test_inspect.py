"""``engine/inspect.py``: the report over a source, with and without a filter (phase 18a)."""

from datetime import UTC, datetime
from typing import Any

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.gateway import MediaKind
from tgmirror.engine import inspect as insp
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec

MB = 1024**2


def _day(month: int, day: int) -> datetime:
    return datetime(2024, month, day, tzinfo=UTC)


def _gateway() -> tuple[FakeGateway, int]:
    gw = FakeGateway()
    src = gw.add_channel("Source").id
    gw.add_message(src, "intro #news", hashtags=("#news",), date=_day(1, 5))
    gw.add_message(
        src,
        "clip #news",
        media=MediaKind.VIDEO,
        size=30 * MB,
        hashtags=("#news",),
        date=_day(1, 20),
    )
    gw.add_message(
        src,
        "a doc",
        media=MediaKind.DOCUMENT,
        size=2 * MB,
        file_name="a.pdf",
        file_key="d1",
        link_domains=("example.com",),
        date=_day(2, 3),
    )
    gw.add_message(
        src,
        "same doc",
        media=MediaKind.DOCUMENT,
        size=2 * MB,
        file_name="a.pdf",
        file_key="d1",
        is_forward=True,
        fwd_from_id=-1001,
        fwd_from_name="Origin",
        date=_day(3, 1),
    )
    gw.add_album(src, [MediaKind.PHOTO, MediaKind.PHOTO], "pics")  # the fake dates it in 2024-01
    return gw, src


async def _report(
    spec: FilterSpec | None = None,
    *,
    by: tuple[str, ...] = insp.BY_CHOICES,
    top: int = insp.DEFAULT_TOP,
    forum: bool = False,
    topic_titles: dict[int, str] | None = None,
) -> insp.Report:
    gw, src = _gateway()
    inspector = insp.Inspector(
        None if spec is None else Matcher(spec), by, forum=forum, topic_titles=topic_titles
    )
    complete = await insp.scan(gw, src, inspector)
    return inspector.report(top=top, complete=complete)


def _table(report: insp.Report, by: str) -> dict[str, int]:
    (table,) = [t for t in report.tables if t.by == by]
    return {r.key: r.messages for r in table.rows}


async def test_totals_count_messages_units_albums_and_bytes() -> None:
    report = await _report()

    assert report.complete and not report.filtered
    assert report.scanned.messages == 6
    assert report.scanned.units == 5  # the album is one unit
    assert report.scanned.albums == 1
    assert report.scanned.bytes == 34 * MB
    assert report.selected == report.scanned
    assert report.last_id == 6


async def test_media_month_year_hashtag_tables() -> None:
    report = await _report()

    assert _table(report, "media") == {"document": 2, "photo": 2, "text": 1, "video": 1}
    months = _table(report, "month")
    # January: two posts and the fake's album, which it dates there
    assert months == {"2024-01": 4, "2024-02": 1, "2024-03": 1}
    assert _table(report, "year") == {"2024": 6}
    assert _table(report, "hashtag") == {"#news": 2}  # the fake's album carries no hashtag entity


async def test_forward_domain_size_and_duplicate_tables() -> None:
    report = await _report()

    assert _table(report, "forward") == {insp.NONE: 5, "-1001": 1}
    forward = next(t for t in report.tables if t.by == "forward")
    assert next(r for r in forward.rows if r.key == "-1001").name == "Origin"
    assert _table(report, "domain") == {"example.com": 1}
    sizes = next(t for t in report.tables if t.by == "size")
    # only messages with a file, smallest bucket first
    assert [(r.key, r.messages) for r in sizes.rows] == [("1-10MB", 2), ("10-100MB", 1)]
    # the same file twice is a duplicate; a file seen once is not
    (dup,) = next(t for t in report.tables if t.by == "duplicate").rows
    assert (dup.key, dup.messages, dup.bytes, dup.name) == ("d1", 2, 4 * MB, "a.pdf")


async def test_a_filter_narrows_the_selection_but_not_what_was_scanned() -> None:
    spec = FilterSpec.from_data({"include": [{"media": ["document"]}]})

    report = await _report(spec, by=("media",))

    assert report.filtered
    assert report.scanned.messages == 6
    assert report.selected.messages == 2
    assert _table(report, "media") == {"document": 2}


async def test_sender_and_topic_tables() -> None:
    gw = FakeGateway()
    src = gw.add_channel("Forum").id
    gw.add_message(src, "a", topic_id=2, from_user_id=7)
    gw.add_message(src, "b", topic_id=2, from_user_id=7)
    gw.add_message(src, "c")  # General
    inspector = insp.Inspector(None, ("topic", "sender"), forum=True, topic_titles={2: "News"})
    await insp.scan(gw, src, inspector)
    report = inspector.report()

    assert _table(report, "topic") == {"2": 2, "1": 1}
    topic = next(t for t in report.tables if t.by == "topic")
    assert topic.rows[0].name == "News"
    assert _table(report, "sender") == {"7": 2, insp.NONE: 1}


async def test_topic_table_is_empty_outside_a_forum() -> None:
    report = await _report(by=("topic",))
    assert _table(report, "topic") == {}


async def test_top_cuts_a_ranking_into_other_but_not_a_sequence() -> None:
    gw = FakeGateway()
    src = gw.add_channel("Tags").id
    for i in range(5):
        gw.add_message(src, f"#t{i}", hashtags=(f"#t{i}",), date=_day(i + 1, 1))
    gw.add_message(src, "#t0", hashtags=("#t0",), date=_day(6, 1))
    inspector = insp.Inspector(None, ("hashtag", "month"))
    await insp.scan(gw, src, inspector)

    report = inspector.report(top=2)

    tags = next(t for t in report.tables if t.by == "hashtag")
    assert [r.key for r in tags.rows] == ["#t0", "#t1"]  # most first, ties by key
    assert tags.other is not None and tags.other.key == "3" and tags.other.messages == 3
    months = next(t for t in report.tables if t.by == "month")
    assert len(months.rows) == 6 and months.other is None
    assert [r.key for r in months.rows] == sorted(r.key for r in months.rows)


async def test_stop_ends_the_scan_early_and_marks_the_report_partial() -> None:
    gw, src = _gateway()
    inspector = insp.Inspector(None, ("media",))

    complete = await insp.scan(gw, src, inspector, stop=lambda: inspector.scanned_messages >= 2)
    report = inspector.report(complete=complete)

    assert not complete and not report.complete
    assert report.scanned.messages == 2


async def test_service_messages_are_not_counted() -> None:
    gw = FakeGateway()
    src = gw.add_channel("S").id
    gw.add_message(src, "hi")
    gw.add_message(src, "", is_service=True)
    inspector = insp.Inspector(None, ("media",))
    await insp.scan(gw, src, inspector)

    assert inspector.report().scanned.messages == 1


def test_unknown_grouping_is_refused() -> None:
    with pytest.raises(ValueError):
        insp.Inspector(None, ("nonsense",))


def test_message_link_forms() -> None:
    assert insp.message_link(-1001234567890, "chan", 5) == "https://t.me/chan/5"
    assert insp.message_link(-1001234567890, None, 5) == "https://t.me/c/1234567890/5"
    assert insp.message_link(-12345, None, 5) is None


async def test_describe_message_and_json_report() -> None:
    gw, src = _gateway()
    forward = gw.messages[src][3]
    row = insp.describe_message(forward, link="https://t.me/x/4")

    assert set(row) == set(insp.LIST_COLUMNS)
    assert row["forwarded_from"] == "-1001" and row["media"] == "document"
    assert row["link"] == "https://t.me/x/4"
    assert row["file_key"] == "d1" and row["file_name"] == "a.pdf"

    data: dict[str, Any] = insp.report_to_dict(await _report(by=("media", "hashtag"), top=1))
    assert data["scanned"]["messages"] == 6
    assert data["tables"]["hashtag"]["other"] is None
    assert data["tables"]["media"]["other"] == {"count": 3, "messages": 4, "bytes": 30 * MB}
