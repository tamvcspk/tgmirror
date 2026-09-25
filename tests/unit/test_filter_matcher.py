"""The client matcher: predicates, rules, exclusion, global bounds and album semantics."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tgmirror.core.gateway import MediaKind, SrcMessage, Unit
from tgmirror.filters import matcher as matcher_module
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterError, FilterSpec

BASE = datetime(2024, 6, 1, tzinfo=UTC)
_next_id = 0


def msg(text: str = "", **kw: Any) -> SrcMessage:
    global _next_id
    _next_id += 1
    kw.setdefault("id", _next_id)
    kw.setdefault("date", BASE)
    return SrcMessage(text=text, **kw)


def unit(*members: SrcMessage) -> Unit:
    return Unit(members)


def passes(data: dict[str, Any], *members: SrcMessage) -> bool:
    return Matcher(FilterSpec.from_data(data)).matches(unit(*members))


def rule(**predicates: Any) -> dict[str, Any]:
    return {"include": [predicates]}


# ---- the predicates, one truth table each ---------------------------------------------------


@pytest.mark.parametrize(
    ("media", "wanted", "expected"),
    [
        (MediaKind.VIDEO, ["video", "photo"], True),
        (MediaKind.PHOTO, ["video", "photo"], True),
        (MediaKind.STICKER, ["video", "photo"], False),
        (MediaKind.TEXT, ["text"], True),
        (MediaKind.WEBPAGE, ["text"], False),  # a link preview is its own kind
        (MediaKind.WEBPAGE, ["text", "webpage"], True),
    ],
)
def test_media(media: MediaKind, wanted: list[str], expected: bool) -> None:
    assert passes(rule(media=wanted), msg(media=media)) is expected


@pytest.mark.parametrize(
    ("hashtags", "wanted", "expected"),
    [
        (("#news",), ["#news"], True),
        (("#News",), ["news"], True),  # the message side is compared case-insensitively too
        (("#sport",), ["#news", "#sport"], True),  # any-of
        (("#newsletter",), ["#news"], False),  # whole tags, not prefixes
        ((), ["#news"], False),
    ],
)
def test_hashtag_matches_entities(
    hashtags: tuple[str, ...], wanted: list[str], expected: bool
) -> None:
    assert passes(rule(hashtag=wanted), msg("x", hashtags=hashtags)) is expected


def test_a_hash_in_the_text_without_an_entity_is_not_a_hashtag() -> None:
    assert not passes(rule(hashtag=["#news"]), msg("see #news", hashtags=()))


@pytest.mark.parametrize(
    ("text", "wanted", "expected"),
    [
        ("Big Giveaway today", ["giveaway"], True),  # any case, substring
        ("nothing here", ["giveaway", "nothing"], True),  # any-of
        ("nothing here", ["giveaway"], False),
        ("", ["x"], False),
    ],
)
def test_contains(text: str, wanted: list[str], expected: bool) -> None:
    assert passes(rule(contains=wanted), msg(text)) is expected


def test_regex_searches_the_text_and_is_case_sensitive_unless_told() -> None:
    assert passes(rule(regex=r"give(away)?\b"), msg("a giveaway"))  # search, not full match
    assert not passes(rule(regex="GIVEAWAY"), msg("a giveaway"))
    assert passes(rule(regex="(?i)GIVEAWAY"), msg("a giveaway"))


@pytest.mark.parametrize(
    ("text", "wanted", "expected"), [("hi", True, True), ("  ", True, False), ("", False, True)]
)
def test_has_caption(text: str, wanted: bool, expected: bool) -> None:
    assert passes(rule(has_caption=wanted), msg(text)) is expected


@pytest.mark.parametrize(
    ("size", "bounds", "expected"),
    [
        (5 * 1024**2, {"min": "1MB", "max": "10MB"}, True),
        (1024**2, {"min": "1MB"}, True),  # bounds are inclusive
        (10 * 1024**2, {"max": "10MB"}, True),
        (11 * 1024**2, {"max": "10MB"}, False),
        (100, {"min": "1MB"}, False),
        (None, {"max": "10MB"}, False),  # no file: the predicate is false, for max as well
    ],
)
def test_size(size: int | None, bounds: dict[str, str], expected: bool) -> None:
    assert passes(rule(size=bounds), msg(size=size)) is expected


def test_duration_mime_and_views() -> None:
    video = msg(media=MediaKind.VIDEO, duration=90.0, mime="video/mp4", views=500)

    assert passes(rule(duration={"min": "1m", "max": "2m"}), video)
    assert not passes(rule(duration={"max": 60}), video)
    assert passes(rule(mime=["video/mp4"]), video) and passes(rule(mime=["video/*"]), video)
    assert not passes(rule(mime=["image/*"]), video)
    assert passes(rule(views={"min": 500}), video) and not passes(rule(views={"min": 501}), video)
    bare = msg("x")
    assert not passes(rule(duration={"min": 0}), bare)
    assert not passes(rule(mime=["video/*"]), bare)
    assert not passes(rule(views={"min": 0}), bare)


def test_from_user_and_topic() -> None:
    group_msg = msg("hi", from_user_id=5, topic_id=7)

    assert passes(rule(from_user=[5, 9]), group_msg)
    assert not passes(rule(from_user=[9]), group_msg)
    assert passes(rule(topic=7), group_msg) and not passes(rule(topic=[1]), group_msg)
    broadcast_msg = msg("hi")  # no sender/topic: the predicate is false, like size/views/mime
    assert not passes(rule(from_user=[5]), broadcast_msg)
    assert not passes(rule(topic=[7]), broadcast_msg)


def test_topic_1_means_general_whose_messages_carry_no_topic_id() -> None:
    """General messages have no ``reply_to`` in Telethon, so their ``topic_id`` is ``None``; the
    wizard offers General as topic 1, which must still select them."""
    general_msg = msg("hi")

    assert passes(rule(topic=[1]), general_msg)
    assert not passes(rule(topic=[1]), msg("hi", topic_id=7))


# ---- rules ----------------------------------------------------------------------------------


def test_predicates_in_a_rule_are_anded() -> None:
    both = rule(media=["video"], hashtag=["#news"])

    assert passes(both, msg(media=MediaKind.VIDEO, hashtags=("#news",)))
    assert not passes(both, msg(media=MediaKind.VIDEO))
    assert not passes(both, msg(media=MediaKind.PHOTO, hashtags=("#news",)))


def test_rules_are_ored() -> None:
    either = {"include": [{"media": ["video"]}, {"contains": ["giveaway"]}]}

    assert passes(either, msg(media=MediaKind.VIDEO))
    assert passes(either, msg("giveaway"))
    assert not passes(either, msg("hello"))


def test_no_include_means_everything() -> None:
    assert passes({}, msg("anything"))
    assert passes({"exclude": [{"contains": ["ads"]}]}, msg("news"))


def test_exclude_wins_over_include() -> None:
    data = {"include": [{"media": ["video"]}], "exclude": [{"contains": ["ads"]}]}

    assert passes(data, msg("news", media=MediaKind.VIDEO))
    assert not passes(data, msg("ads inside", media=MediaKind.VIDEO))


def test_any_exclude_rule_excludes() -> None:
    data = {"exclude": [{"contains": ["ads"]}, {"media": ["sticker"]}]}

    assert not passes(data, msg("ads"))
    assert not passes(data, msg(media=MediaKind.STICKER))
    assert passes(data, msg("fine"))


# ---- global bounds --------------------------------------------------------------------------


def test_date_range_is_from_inclusive_to_exclusive() -> None:
    data = {"date": {"from": "2024-06-01", "to": "2024-07-01"}}

    assert passes(data, msg(date=BASE))  # exactly `from`
    assert passes(data, msg(date=BASE + timedelta(days=29, hours=23)))
    assert not passes(data, msg(date=BASE - timedelta(seconds=1)))
    assert not passes(data, msg(date=datetime(2024, 7, 1, tzinfo=UTC)))  # exactly `to`


def test_an_open_ended_date_range() -> None:
    assert passes({"date": {"from": "2024-01-01"}}, msg(date=BASE))
    assert not passes({"date": {"to": "2024-01-01"}}, msg(date=BASE))


def test_id_range_is_inclusive() -> None:
    data = {"id": {"from": 10, "to": 20}}

    assert [passes(data, msg(id=i)) for i in (9, 10, 20, 21)] == [False, True, True, False]


def test_global_bounds_and_rules_are_anded() -> None:
    data = {"include": [{"media": ["video"]}], "id": {"from": 100}}

    assert passes(data, msg(id=100, media=MediaKind.VIDEO))
    assert not passes(data, msg(id=99, media=MediaKind.VIDEO))
    assert not passes(data, msg(id=100, media=MediaKind.PHOTO))


# ---- albums ---------------------------------------------------------------------------------


def album(*media: MediaKind, caption: str = "", start: int = 100, **kw: Any) -> Unit:
    return unit(
        *(
            msg(caption if i == 0 else "", id=start + i, media=kind, grouped_id=7, **kw)
            for i, kind in enumerate(media)
        )
    )


def matches(data: dict[str, Any], u: Unit) -> bool:
    return Matcher(FilterSpec.from_data(data)).matches(u)


def test_album_any_matches_when_one_member_matches() -> None:
    photo_or_video = album(MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.PHOTO)

    assert matches({"include": [{"media": ["video"]}]}, photo_or_video)  # default: any
    assert not matches({"include": [{"media": ["audio"]}]}, photo_or_video)


def test_a_hashtag_on_the_first_member_selects_the_whole_album() -> None:
    tagged = unit(
        msg("caption", id=1, media=MediaKind.PHOTO, grouped_id=7, hashtags=("#news",)),
        msg("", id=2, media=MediaKind.PHOTO, grouped_id=7),
    )

    assert matches({"include": [{"hashtag": ["#news"]}]}, tagged)


def test_album_all_needs_every_member_to_match() -> None:
    data = {"include": [{"media": ["photo"]}], "album": "all"}

    assert matches(data, album(MediaKind.PHOTO, MediaKind.PHOTO))
    assert not matches(data, album(MediaKind.PHOTO, MediaKind.VIDEO))


def test_album_first_looks_at_the_first_member_only() -> None:
    data = {"include": [{"media": ["photo"]}], "album": "first"}

    assert matches(data, album(MediaKind.PHOTO, MediaKind.VIDEO))
    assert not matches(data, album(MediaKind.VIDEO, MediaKind.PHOTO))


@pytest.mark.parametrize("mode", ["any", "all", "first"])
def test_one_excluded_member_excludes_the_album_in_every_mode(mode: str) -> None:
    data = {"exclude": [{"media": ["video"]}], "album": mode}

    assert not matches(data, album(MediaKind.PHOTO, MediaKind.PHOTO, MediaKind.VIDEO))
    assert matches(data, album(MediaKind.PHOTO, MediaKind.PHOTO))


def test_date_and_id_judge_an_album_by_its_first_member() -> None:
    late = album(MediaKind.PHOTO, MediaKind.PHOTO, start=98)  # ids 98, 99

    assert not matches({"id": {"from": 99}}, late)  # the first member (98) is below the range
    assert matches({"id": {"from": 98, "to": 98}}, late)  # the second member is out, the unit is in


# ---- regex safety ---------------------------------------------------------------------------


def test_a_catastrophic_regex_fails_with_a_filter_error_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(matcher_module, "REGEX_TIMEOUT", 0.05)
    nasty = Matcher(FilterSpec.from_data(rule(regex=r"(a|aa)+$")))

    with pytest.raises(FilterError) as exc:
        nasty.matches(unit(msg("a" * 60 + "b", id=42)))

    assert "message 42" in exc.value.detail
