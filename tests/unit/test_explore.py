"""The model behind the "Khám phá" screen (phase 18b): steps, the filter they make, undo."""

from datetime import UTC, datetime

import pytest

from tgmirror.core.gateway import MediaKind, SrcMessage, Unit
from tgmirror.engine import selection
from tgmirror.engine.explore import Explorer, NotFilterable, Step, predicate_of, spec_of
from tgmirror.engine.inspect import NONE


def message(
    i: int, month: int = 1, media: MediaKind = MediaKind.TEXT, **extra: object
) -> SrcMessage:
    return SrcMessage(
        id=i,
        date=datetime(2024, month, 10, tzinfo=UTC),
        text=f"m{i}",
        media=media,
        **extra,  # type: ignore[arg-type]
    )


def units() -> list[Unit]:
    ms = [
        message(1, 1, MediaKind.VIDEO, hashtags=("#a",), size=10),
        message(2, 1, MediaKind.VIDEO, hashtags=("#b",), size=10),
        message(3, 2, MediaKind.PHOTO, hashtags=("#a",), size=5),
        message(4, 2, MediaKind.TEXT, is_forward=True, fwd_from_id=-1007, fwd_from_name="O"),
        message(5, 12, MediaKind.TEXT, link_domains=("example.com",), topic_id=3),
    ]
    return [Unit((m,)) for m in ms]


def keys(explorer: Explorer, by: str) -> dict[str, int]:
    return {row.key: row.messages for row in explorer.rows(by)}


def test_a_row_becomes_the_predicate_the_flags_would_make() -> None:
    assert predicate_of("media", "video") == {"media": ["video"]}
    assert predicate_of("hashtag", "#a") == {"hashtag": ["#a"]}
    assert predicate_of("domain", "example.com") == {"link": ["example.com"]}
    assert predicate_of("forward", NONE) == {"forwarded": False}
    assert predicate_of("forward", "-1007") == {"forwarded_from": [-1007]}
    assert predicate_of("topic", "3") == {"topic": [3]}
    assert predicate_of("sender", "42") == {"from_user": [42]}
    # a month is a half-open date range, December rolls into the next year
    assert predicate_of("month", "2024-02") == {"date": {"from": "2024-02-01", "to": "2024-03-01"}}
    assert predicate_of("month", "2024-12") == {"date": {"from": "2024-12-01", "to": "2025-01-01"}}


@pytest.mark.parametrize(
    ("by", "key", "reason"),
    [
        ("forward", "name:Hidden", "explore.hidden_forward"),
        ("sender", NONE, "explore.channel_post"),
        ("size", "<1MB", "explore.not_filterable"),
        ("month", "soon", "explore.not_filterable"),
        ("topic", "General", "explore.not_filterable"),
    ],
)
def test_a_row_with_no_predicate_says_why(by: str, key: str, reason: str) -> None:
    with pytest.raises(NotFilterable) as caught:
        predicate_of(by, key)

    assert caught.value.reason == reason


def test_includes_are_one_rule_and_each_exclude_a_rule_of_its_own() -> None:
    spec = spec_of(
        [
            Step("include", "media", "video"),
            Step("include", "hashtag", "#a"),
            Step("exclude", "media", "photo"),
            Step("include", "month", "2024-01"),
        ]
    )

    assert [dict(rule.model_dump(exclude_none=True)) for rule in spec.include] == [
        {"media": (MediaKind.VIDEO,), "hashtag": ("#a",)}
    ]
    assert len(spec.exclude) == 1 and spec.when is not None
    # an include and an exclude by media are things the flags can say
    assert selection.to_flags(spec) is not None


def test_narrowing_recounts_every_table_over_the_selection() -> None:
    explorer = Explorer(units())
    assert explorer.report.selected.messages == 5

    explorer.add("include", "media", "video")

    assert explorer.report.selected.messages == 2
    assert keys(explorer, "hashtag") == {"#a": 1, "#b": 1}
    assert keys(explorer, "month") == {"2024-01": 2}
    assert explorer.report.scanned.messages == 5  # the source itself does not shrink


def test_a_second_include_of_the_same_thing_replaces_the_first() -> None:
    explorer = Explorer(units())
    explorer.add("include", "media", "video")

    explorer.add("include", "media", "photo")

    assert [s.key for s in explorer.steps] == ["photo"]
    assert explorer.report.selected.messages == 1
    explorer.pop()
    assert explorer.report.selected.messages == 5  # undo goes back to before either


def test_exclude_leaves_matches_out_and_pop_undoes_the_last_step_only() -> None:
    explorer = Explorer(units())
    explorer.add("include", "hashtag", "#a")
    explorer.add("exclude", "media", "photo")

    assert explorer.report.selected.messages == 1
    assert explorer.pop() == Step("exclude", "media", "photo")
    assert explorer.report.selected.messages == 2
    assert explorer.pop() == Step("include", "hashtag", "#a")
    assert explorer.pop() is None


def test_a_row_that_cannot_be_a_filter_changes_nothing() -> None:
    explorer = Explorer(units())
    explorer.add("include", "media", "video")

    with pytest.raises(NotFilterable):
        explorer.add("exclude", "month", "2024-01")  # a month can only be kept, not excluded
    with pytest.raises(NotFilterable):
        explorer.add("include", "forward", "name:Hidden")

    assert [s.key for s in explorer.steps] == ["video"]
    assert explorer.report.selected.messages == 2


def test_topic_is_a_table_only_for_a_forum() -> None:
    assert "topic" not in Explorer(units()).tables
    forum = Explorer(units(), forum=True, topic_titles={3: "News"})

    assert "topic" in forum.tables
    assert {r.key: r.name for r in forum.rows("topic")} == {"3": "News", "1": None}
