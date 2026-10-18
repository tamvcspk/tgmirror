"""``engine/selection.py``: selection files, the frozen list, and filter-to-flags (phase 18a)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tgmirror.engine import selection as sel
from tgmirror.engine.transform import Transform
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.parser import FilterError, from_file

NOW = datetime(2026, 10, 12, 10, 31, tzinfo=UTC)


def spec(data: dict[str, Any]) -> FilterSpec:
    return FilterSpec.from_data(data)


def test_a_selection_round_trips_with_its_source_and_frozen_ids(tmp_path: Path) -> None:
    chosen = sel.Selection(
        spec(
            {
                "include": [{"media": ["video"], "hashtag": ["#tutorial"]}],
                "date": {"from": "2024-01-01"},
            }
        ),
        sel.SelectionSource(id=-1001234567890, title="Channel A"),
        sel.Snapshot(NOW, upto_id=58211, count=3, ids=(5, 9, 12)),
    )
    path = tmp_path / "chosen.yaml"

    sel.dump(chosen, path, now=NOW)
    back = sel.load(path, with_ids=True)

    assert back.filters == chosen.filters
    assert back.source == chosen.source
    assert back.snapshot == sel.Snapshot(NOW, 58211, 3, (5, 9, 12), "chosen.ids")
    assert (tmp_path / "chosen.ids").read_text(encoding="utf-8") == "5\n9\n12\n"
    assert "tgmirror selection" in path.read_text(encoding="utf-8").splitlines()[0]


def test_ids_are_only_read_when_asked_for(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    sel.dump(sel.Selection(spec({}), None, sel.Snapshot(NOW, 10, 2, (1, 2))), path)
    (tmp_path / "s.ids").unlink()  # the list is gone

    assert sel.load(path).snapshot is not None  # fine: nothing asked for the ids
    with pytest.raises(sel.SelectionError, match="cannot read the list of ids"):
        sel.load(path, with_ids=True)


def test_a_hand_edited_id_list_is_read_sorted_with_comments_ignored(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    sel.dump(sel.Selection(spec({}), None, sel.Snapshot(NOW, 10, 3, (1, 2, 3))), path)
    (tmp_path / "s.ids").write_text("7\n# a note\n3  # this one\n\n7\n", encoding="utf-8")

    assert sel.load(path, with_ids=True).snapshot.ids == (3, 7)  # type: ignore[union-attr]


def test_a_bad_id_line_names_its_line(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    sel.dump(sel.Selection(spec({}), None, sel.Snapshot(NOW, 10, 1, (1,))), path)
    (tmp_path / "s.ids").write_text("1\nabc\n", encoding="utf-8")

    with pytest.raises(sel.SelectionError, match="line 2"):
        sel.load(path, with_ids=True)


def test_a_plain_filter_file_is_a_selection_without_source_or_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "filter.yaml"
    path.write_text('include:\n  - media: [video]\n    hashtag: ["#a"]\n', encoding="utf-8")

    loaded = sel.load(path)

    assert loaded.source is None and loaded.snapshot is None
    assert loaded.filters == from_file(path)


def test_filter_file_still_refuses_a_selection_file(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    sel.dump(sel.Selection(spec({"include": [{"media": ["video"]}]})), path)

    with pytest.raises(FilterError):
        from_file(path)  # --filter-file is the filter alone; a selection goes to --selection


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("version: 2\nfilter: {}\n", "not supported"),
        ("version: 1\nfilter: {}\ntransform: {links: yes}\n", "transform: links"),
        (
            "version: 1\nfilter: {}\ntransform: {topics: {hashtag_to_topic: {}}}\n",
            "transform: topics.hashtag_to_topic",
        ),
        ("version: 1\nfilter: {}\ntransform: {caption: [{clear: no}]}\n", "transform: caption"),
        ("version: 1\nfilter: {}\nextra: 1\n", "not a selection key"),
        ("version: 1\nfilter: {include: [{media: [vidoe]}]}\n", "filter:"),
        ("version: 1\nsource: {nope: 1}\nfilter: {}\n", "source takes"),
        ("version: 1\nfilter: {}\nsnapshot: {count: 1}\n", "snapshot needs"),
        ("- a\n- b\n", "must hold a mapping"),
    ],
)
def test_what_this_version_does_not_understand_is_refused(
    tmp_path: Path, text: str, why: str
) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(sel.SelectionError, match=why):
        sel.load(path)


def test_a_transform_travels_with_the_selection(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    transform = Transform.from_data({"caption": [{"remove_hashtags": ["#ads"]}, {"append": "x"}]})
    sel.dump(sel.Selection(spec({}), None, None, transform), path)

    loaded = sel.load(path)

    assert loaded.transform == transform
    assert "transform:" in path.read_text(encoding="utf-8")
    plain = tmp_path / "plain.yaml"
    sel.dump(sel.Selection(spec({})), plain)
    assert "transform" not in plain.read_text(encoding="utf-8")
    assert sel.load(plain).transform.is_empty


def test_topic_rules_travel_with_the_selection_in_their_priority_order(tmp_path: Path) -> None:
    """Phase 19b: the order of the hashtag rules (a priority) and an id key of ``topic_map``
    (a number, not a title) must both survive the YAML file."""
    path = tmp_path / "s.yaml"
    transform = Transform.from_data(
        {
            "topics": {
                "hashtag_to_topic": {
                    "map": {"#zeta": "Z", "#alpha": "A"},
                    "default": "Misc",
                    "strip": True,
                },
                "topic_map": {12: "Merged", "Old": "New"},
                "hashtag_format": "#t_{slug}",
            }
        }
    )
    sel.dump(sel.Selection(spec({}), None, None, transform), path)

    loaded = sel.load(path).transform

    assert loaded == transform
    assert [r.to for r in loaded.topics.rules] == ["Z", "A"]  # the priority order is kept
    assert (12, "Merged") in loaded.topics.renames


def test_exact_takes_exactly_the_frozen_messages(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    snapshot = sel.Snapshot(NOW, 9, 2, (4, 8))
    sel.dump(sel.Selection(spec({"include": [{"media": ["video"]}]}), None, snapshot), path)

    exact = sel.exact(sel.load(path, with_ids=True))

    assert exact.message_ids == (4, 8) and not exact.include  # the filter is not judged again
    with pytest.raises(sel.SelectionError, match="frozen"):
        sel.exact(sel.Selection(spec({})))


def test_last_path_is_per_source(tmp_path: Path) -> None:
    assert sel.last_path(tmp_path, -1001).name == "last--1001.yaml"


# ---- filter -> flags --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "tokens"),
    [
        ({}, []),
        (
            {"include": [{"media": ["video", "photo"], "hashtag": ["#news", "#ads"]}]},
            ["--media", "video,photo", "--hashtag", "#news", "--hashtag", "#ads"],
        ),
        (
            {"date": {"from": "2024-01-01", "to": "2025-01-01"}, "album": "all"},
            ["--since", "2024-01-01", "--until", "2025-01-01", "--album", "all"],
        ),
        (
            {
                "include": [{"size": {"min": "10MB"}}],
                "exclude": [{"regex": "x"}, {"media": ["sticker"]}],
            },
            ["--exclude-regex", "x", "--exclude-media", "sticker", "--min-size", "10485760B"],
        ),
        (
            {"include": [{"forwarded": False, "link": []}]},
            ["--not-forwarded", "--has-link"],
        ),
        (
            {"include": [{"forwarded_from": [-1007], "link": ["a.com", "b.org"], "topic": [3]}]},
            [
                "--topic",
                "3",
                "--forwarded-from",
                "-1007",
                "--link-domain",
                "a.com",
                "--link-domain",
                "b.org",
            ],
        ),
    ],
)
def test_to_flags_says_the_filter_as_flags(data: dict[str, Any], tokens: list[str]) -> None:
    assert sel.to_flags(spec(data)) == tokens


@pytest.mark.parametrize(
    "data",
    [
        {"include": [{"media": ["video"]}, {"hashtag": ["#a"]}]},  # two rules: OR has no flags
        {"include": [{"mime": ["video/mp4"]}]},
        {"include": [{"duration": {"min": "5m"}}]},
        {"include": [{"forwards": {"min": 5}}]},
        {"exclude": [{"media": ["video"], "contains": ["x"]}]},
        {"exclude": [{"media": ["video"]}, {"media": ["photo"]}]},
        {"id": {"from": 5}},
        {"message_ids": [1, 2]},
        {"date": {"from": "2024-01-01T10:30:00"}},  # a time of day: the flag takes dates only
    ],
)
def test_to_flags_is_none_for_what_flags_cannot_say(data: dict[str, Any]) -> None:
    assert sel.to_flags(spec(data)) is None


def test_quote_leaves_plain_words_and_quotes_the_rest() -> None:
    assert sel.quote("--media") == "--media"
    assert sel.quote("@chan") == "@chan"
    assert sel.quote("-1001234567890") == "-1001234567890"
    assert sel.quote("#news") == '"#news"'
    assert sel.quote("two words") == '"two words"'
    assert sel.quote('say "hi"') == '"say \\"hi\\""'


# ---- the exact id list as a filter ------------------------------------------------------------


def test_message_ids_select_only_those_messages_and_whole_albums() -> None:
    from tgmirror.core.gateway import SrcMessage, Unit

    def unit(*ids: int, album: int | None = None) -> Unit:
        return Unit(tuple(SrcMessage(id=i, date=NOW, grouped_id=album) for i in ids))

    matcher = Matcher(spec({"message_ids": [2, 11]}))

    assert matcher.matches(unit(2))
    assert not matcher.matches(unit(3))
    assert matcher.matches(unit(10, 11, 12, album=7))  # one member listed takes the album
    assert not matcher.matches(unit(20, 21, album=8))


def test_message_ids_round_trip_sorted_and_deduplicated() -> None:
    original = spec({"message_ids": [9, 3, 9, 5]})

    assert original.message_ids == (3, 5, 9)
    assert FilterSpec.from_json(original.to_json()) == original
    assert not original.is_empty


def test_filter_file_names_the_right_flag_for_a_selection_file(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    sel.dump(sel.Selection(spec({"include": [{"media": ["video"]}]})), path)

    with pytest.raises(FilterError, match="--selection"):
        from_file(path)


async def test_the_wizard_file_choice_takes_a_selection_file_too(tmp_path: Path) -> None:
    from tests.fakes import ScriptedPrompter
    from tgmirror.cli import wizard

    path = tmp_path / "s.yaml"
    wanted = spec({"include": [{"media": ["video"], "hashtag": ["#a"]}]})
    sel.dump(
        sel.Selection(wanted, sel.SelectionSource(id=-1001), sel.Snapshot(NOW, 5, 1, (3,))), path
    )
    prompter = ScriptedPrompter(select=["YAML file"], text=[str(path)])

    chosen = await wizard.pick_filters(prompter)

    assert chosen == wanted
