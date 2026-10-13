"""Flags, YAML files and the wizard's answers all end in the same ``FilterSpec``."""

from pathlib import Path

import pytest

from tgmirror.core.gateway import MediaKind
from tgmirror.filters.model import FilterError, FilterSpec
from tgmirror.filters.parser import (
    FilterFileError,
    FlagFilters,
    from_file,
    from_flags,
    layer,
    resolve,
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "filters.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---- flags ----------------------------------------------------------------------------------


def test_the_positive_flags_make_one_include_rule() -> None:
    spec = from_flags(
        FlagFilters(
            media="video, photo",
            hashtag=["#news", "#sport"],
            contains=["word"],
            regex="a.c",
            min_size="10MB",
            max_size="2GB",
            from_user=[5, 9],
            topic=[7],
        )
    )

    (rule,) = spec.include
    assert rule.media == (MediaKind.VIDEO, MediaKind.PHOTO)
    assert rule.hashtag == ("#news", "#sport") and rule.contains == ("word",)
    assert rule.regex == "a.c"
    assert rule.size is not None and (rule.size.min, rule.size.max) == (10 * 1024**2, 2 * 1024**3)
    assert rule.from_user == (5, 9) and rule.topic == (7,)
    assert spec.exclude == ()


def test_every_exclude_flag_is_its_own_rule() -> None:
    spec = from_flags(FlagFilters(exclude_regex=["ads", "promo"], exclude_media="sticker,gif"))

    assert [r.regex for r in spec.exclude] == ["ads", "promo", None]
    assert spec.exclude[2].media == (MediaKind.STICKER, MediaKind.GIF)
    assert spec.include == ()


def test_dates_and_album_flags() -> None:
    spec = from_flags(FlagFilters(since="2024-01-01", until="2025-01-01", album="first"))

    assert spec.when is not None and spec.when.from_ is not None and spec.when.to is not None
    assert (spec.when.from_.year, spec.when.to.year, spec.album) == (2024, 2025, "first")
    assert from_flags(FlagFilters(until="2025-01-01")).when is not None  # one end is enough


def test_a_flag_error_names_the_offending_part() -> None:
    with pytest.raises(FilterError) as exc:
        from_flags(FlagFilters(media="video,vidoe"))
    assert "include[0].media[1]" in exc.value.detail

    with pytest.raises(FilterError) as exc:
        from_flags(FlagFilters(min_size="10"))
    assert "is not a size" in exc.value.detail

    with pytest.raises(FilterError):
        from_flags(FlagFilters(since="last week"))


# ---- files ----------------------------------------------------------------------------------

DOC_YAML = """\
include:
  - {media: [video, photo], hashtag: ["#News"]}
  - {regex: "give(away)?"}
exclude:
  - {contains: ["ads"]}
date: {from: 2024-01-01, to: 2025-01-01}
id: {from: 1000, to: 50000}
album: all
"""


def test_a_yaml_file_is_read_like_the_documented_example(tmp_path: Path) -> None:
    spec = from_file(write(tmp_path, DOC_YAML))

    assert len(spec.include) == 2 and len(spec.exclude) == 1
    assert spec.include[0].hashtag == ("#news",)
    assert spec.ids is not None and spec.ids.to == 50000
    assert spec.album == "all"


def test_yaml_and_flags_that_say_the_same_store_the_same_json(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """\
include:
  - {media: [video], hashtag: ["#news"], size: {min: 10MB}}
exclude:
  - {regex: ads}
date: {from: 2024-01-01}
""",
    )
    flags = FlagFilters(
        media="video", hashtag=["#news"], min_size="10MB", exclude_regex=["ads"], since="2024-01-01"
    )

    assert from_file(path).to_json() == from_flags(flags).to_json()


def test_an_empty_yaml_file_is_an_empty_filter(tmp_path: Path) -> None:
    assert from_file(write(tmp_path, "")).is_empty
    assert from_file(write(tmp_path, "# nothing\n")).is_empty


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("include: [", "not valid YAML"),
        ("- a\n- b\n", "must hold a mapping"),
        ("include:\n  - {media: [vidoe]}\n", "include[0].media[0]"),
        ("colour: red\n", "colour: not a filter key"),
    ],
)
def test_a_bad_file_is_a_filter_file_error_naming_the_file(
    tmp_path: Path, text: str, fragment: str
) -> None:
    path = write(tmp_path, text)

    with pytest.raises(FilterFileError) as exc:
        from_file(path)

    assert fragment in exc.value.detail and "filters.yaml" in exc.value.detail


def test_an_unquoted_hashtag_gets_a_hint_because_yaml_reads_it_as_a_comment(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, "include:\n  - {hashtag: [#news]}\n")

    with pytest.raises(FilterFileError) as exc:
        from_file(path)

    assert (
        "not valid YAML" in exc.value.detail
        and 'Hashtags need quotes ("#news")' in exc.value.detail
    )
    assert "(line " in exc.value.detail


def test_other_yaml_errors_get_no_hashtag_hint(tmp_path: Path) -> None:
    with pytest.raises(FilterFileError) as exc:
        from_file(write(tmp_path, "include: [\n"))

    assert "Hashtags need quotes" not in exc.value.detail


def test_a_missing_file_is_a_filter_file_error(tmp_path: Path) -> None:
    with pytest.raises(FilterFileError, match="cannot read"):
        from_file(tmp_path / "nope.yaml")


# ---- resolving ------------------------------------------------------------------------------


def test_no_flags_and_no_file_means_no_filter_was_given() -> None:
    assert resolve(FlagFilters(), None) is None


def test_flags_or_a_file_resolve_to_a_spec(tmp_path: Path) -> None:
    assert isinstance(resolve(FlagFilters(media="video"), None), FilterSpec)
    assert isinstance(resolve(FlagFilters(), write(tmp_path, DOC_YAML)), FilterSpec)


# ---- a file with flags on top ---------------------------------------------------------------


def test_flags_given_with_a_file_narrow_it_and_the_file_stays_the_base(tmp_path: Path) -> None:
    base = write(tmp_path, DOC_YAML)  # two include rules, one exclude, a date, an id range

    spec = resolve(FlagFilters(min_size="10MB", exclude_regex=["spam"], until="2024-06-01"), base)

    assert spec is not None
    # the flag rule is ANDed into each alternative of the file
    assert [r.hashtag for r in spec.include] == [("#news",), None]
    assert all(r.size is not None for r in spec.include)
    assert len(spec.exclude) == 2  # the file's and the flag's both apply
    assert spec.when is not None and spec.when.to.month == 6  # --until replaces that end only
    assert spec.when.from_ is not None and spec.when.from_.year == 2024
    assert spec.ids is not None and spec.album == "all"  # untouched


def test_a_flag_for_a_predicate_the_file_names_replaces_its_value() -> None:
    base = FilterSpec.from_data({"include": [{"hashtag": ["#a"], "media": ["video"]}]})

    spec = layer(base, from_flags(FlagFilters(hashtag=["#b"])))

    (rule,) = spec.include
    assert rule.hashtag == ("#b",) and rule.media == (MediaKind.VIDEO,)


def test_a_flag_rule_stands_alone_when_the_file_has_no_include_rule() -> None:
    base = FilterSpec.from_data({"date": {"from": "2024-01-01"}})

    spec = layer(base, from_flags(FlagFilters(media="video")))

    assert len(spec.include) == 1 and spec.when is not None


def test_an_album_flag_given_explicitly_wins_even_when_it_is_the_default() -> None:
    base = FilterSpec.from_data({"include": [{"media": ["video"]}], "album": "all"})

    assert layer(base, from_flags(FlagFilters(album="any"))).album == "any"
    assert layer(base, from_flags(FlagFilters(media="photo"))).album == "all"  # not given: kept


def test_layering_nothing_is_the_file_itself(tmp_path: Path) -> None:
    base = from_file(write(tmp_path, DOC_YAML))

    assert layer(base, FilterSpec()) is base


def test_flags_that_make_a_file_contradict_itself_are_a_filter_error() -> None:
    base = FilterSpec.from_data({"date": {"from": "2024-06-01"}})

    with pytest.raises(FilterError, match="before"):
        layer(base, from_flags(FlagFilters(until="2024-01-01")))
