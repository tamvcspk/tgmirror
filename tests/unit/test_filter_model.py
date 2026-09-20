"""The filter model: validation, normalisation and the canonical stored form."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from tgmirror.core.gateway import MediaKind
from tgmirror.filters.model import FilterError, FilterSpec, parse_duration, parse_size


def spec(data: dict[str, Any]) -> FilterSpec:
    return FilterSpec.from_data(data)


def rejected(data: dict[str, Any]) -> str:
    with pytest.raises(FilterError) as exc:
        spec(data)
    return exc.value.detail


# ---- values ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "size"),
    [
        ("500B", 500),
        ("10MB", 10 * 1024**2),
        ("2GB", 2 * 1024**3),
        ("1.5 gb", int(1.5 * 1024**3)),
        ("4KiB", 4096),
        (" 1TB ", 1024**4),
    ],
)
def test_sizes_have_explicit_units(text: str, size: int) -> None:
    assert parse_size(text) == size


@pytest.mark.parametrize("bad", [2048, 2.5, "2048", "10", "MB", "-1MB", "1XB", None, True])
def test_a_bare_or_malformed_size_is_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_size(bad)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [(90, 90.0), (0.5, 0.5), ("90s", 90.0), ("5m", 300.0), ("1h30m", 5400.0), ("1H 30 M", 5400.0)],
)
def test_durations_are_seconds_or_have_units(value: object, seconds: float) -> None:
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("bad", ["fast", "5", "5x", "m5", True, -3, None])
def test_bad_durations_are_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_duration(bad)


# ---- normalisation --------------------------------------------------------------------------


def test_hashtags_are_lower_cased_with_a_hash_and_deduplicated() -> None:
    rule = spec({"include": [{"hashtag": ["News", "#NEWS", "#Sport"]}]}).include[0]

    assert rule.hashtag == ("#news", "#sport")


def test_a_single_value_stands_for_a_one_item_list() -> None:
    rule = spec({"include": [{"media": "video", "hashtag": "#x", "contains": "word"}]}).include[0]

    assert (rule.media, rule.hashtag, rule.contains) == ((MediaKind.VIDEO,), ("#x",), ("word",))


def test_dates_become_aware_utc_and_yaml_dates_work() -> None:
    from datetime import date

    from_yaml = spec({"date": {"from": date(2024, 1, 1), "to": "2025-01-01"}}).when
    with_zone = spec({"date": {"from": "2024-01-01T02:00:00+02:00"}}).when

    assert from_yaml is not None and with_zone is not None
    assert from_yaml.from_ == datetime(2024, 1, 1, tzinfo=UTC)
    assert from_yaml.to == datetime(2025, 1, 1, tzinfo=UTC)
    assert with_zone.from_ == datetime(2024, 1, 1, tzinfo=UTC)


def test_sizes_in_a_rule_are_stored_as_bytes() -> None:
    rule = spec({"include": [{"size": {"min": "10MB", "max": "2GB"}}]}).include[0]

    assert rule.size is not None
    assert (rule.size.min, rule.size.max) == (10 * 1024**2, 2 * 1024**3)


def test_mime_types_are_lower_cased() -> None:
    assert spec({"include": [{"mime": ["Video/MP4", "image/*"]}]}).include[0].mime == (
        "video/mp4",
        "image/*",
    )


# ---- rejection ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"include": [{"media": ["vidoe"]}]}, "include[0].media[0]"),
        ({"include": [{}]}, "at least one predicate"),
        ({"include": [{"regex": "("}]}, "not a valid regular expression"),
        ({"include": [{"hashtag": ["two words"]}]}, "single word"),
        ({"include": [{"contains": [" "]}]}, "cannot be empty"),
        ({"include": [{"mime": ["video"]}]}, "video/mp4"),
        ({"include": [{"size": {"min": 100}}]}, "needs a unit"),
        ({"include": [{"size": {"min": "2GB", "max": "1GB"}}]}, "min is larger than max"),
        ({"include": [{"size": {}}]}, "give min, max or both"),
        ({"include": [{"views": {"min": -1}}]}, "greater than or equal to 0"),
        ({"date": {"from": "2025-01-01", "to": "2024-01-01"}}, "from must be before to"),
        ({"date": {"from": "yesterday"}}, "not a date like 2024-01-01"),
        ({"id": {"from": 50, "to": 10}}, "from is larger than to"),
        ({"id": {"from": 0}}, "greater than or equal to 1"),
        ({"album": "some"}, "any"),
        ({"colour": "red"}, "not a filter key"),
    ],
)
def test_invalid_filters_say_where_and_why(data: dict[str, Any], fragment: str) -> None:
    assert fragment in rejected(data)


@pytest.mark.parametrize("key", ["from_user", "topic"])
def test_group_predicates_are_announced_for_phase_8(key: str) -> None:
    detail = rejected({"include": [{key: "x"}]})

    assert key in detail and "phase 8" in detail


def test_every_problem_is_reported() -> None:
    detail = rejected({"include": [{"media": ["bad"]}], "album": "some"})

    assert "include[0].media[0]" in detail and "album" in detail


# ---- the stored form ------------------------------------------------------------------------


def test_an_empty_filter_is_stored_as_empty_json() -> None:
    assert FilterSpec().to_json() == "{}"
    assert FilterSpec().is_empty and FilterSpec.from_json("{}").is_empty
    assert FilterSpec.from_data(None).is_empty  # an empty YAML file


def test_a_filter_survives_the_round_trip_through_json() -> None:
    original = spec(
        {
            "include": [
                {"media": ["video", "photo"], "hashtag": ["#News"], "size": {"max": "2GB"}},
                {"regex": "give(away)?", "duration": {"min": "1m"}},
            ],
            "exclude": [{"contains": ["ads"]}],
            "date": {"from": "2024-01-01", "to": "2025-01-01"},
            "id": {"from": 10, "to": 5000},
            "album": "all",
        }
    )

    stored = original.to_json()

    assert FilterSpec.from_json(stored) == original
    assert FilterSpec.from_json(stored).to_json() == stored  # canonical: stable when saved again
    assert json.loads(stored)["date"] == {
        "from": "2024-01-01T00:00:00Z",
        "to": "2025-01-01T00:00:00Z",
    }


def test_sizes_are_stored_with_a_unit_so_the_stored_form_loads_again() -> None:
    stored = spec({"include": [{"size": {"min": "1KB"}}]}).to_json()

    assert json.loads(stored)["include"][0]["size"] == {"min": "1024B"}
    assert FilterSpec.from_json(stored).include[0].size.min == 1024  # type: ignore[union-attr]


def test_defaults_are_left_out_so_old_stored_filters_keep_loading() -> None:
    stored = spec({"include": [{"media": ["video"]}]}).to_json()

    assert json.loads(stored) == {"include": [{"media": ["video"]}]}


def test_a_broken_stored_filter_is_a_filter_error() -> None:
    with pytest.raises(FilterError):
        FilterSpec.from_json("{not json")
    with pytest.raises(FilterError):
        FilterSpec.from_json('{"include": [{"media": ["nope"]}]}')
