"""Topic routing rules (phase 21, docs/07-inspect-transform.md section 11.2): a condition, a place
to take a candidate from, what to keep of it, and a name template — the first rule to give a name
wins. Pure: units in, names out."""

from datetime import UTC, datetime
from typing import Any

import pytest
import yaml

from tgmirror.core.gateway import (
    CaptionPolicy,
    MediaKind,
    RemoveHashtags,
    SrcMessage,
    Unit,
)
from tgmirror.core.telethon_gateway import rewrite_caption
from tgmirror.engine import routing
from tgmirror.engine.transform import TopicRules, Transform, TransformError

WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def rules(*route: dict[str, Any], **side: Any) -> TopicRules:
    return Transform.from_data({"topics": {"route": list(route), **side}}).topics


def message(i: int = 1, text: str = "", **kw: Any) -> SrcMessage:
    return SrcMessage(i, WHEN, text=text, **kw)


def unit(*messages: SrcMessage) -> Unit:
    if len(messages) > 1:
        return Unit(tuple(m.__class__(**{**_fields(m), "grouped_id": 9}) for m in messages))
    return Unit(messages)


def _fields(m: SrcMessage) -> dict[str, Any]:
    return {name: getattr(m, name) for name in m.__slots__}


def one(
    rule_set: TopicRules, *args: SrcMessage, titles: dict[int, str] | None = None
) -> str | None:
    return rule_set.destination(unit(*args), titles)


# ---- templates ---------------------------------------------------------------------------------


def test_a_template_has_slots_with_filters_and_doubled_braces_for_the_characters() -> None:
    template = routing.parse_template("{name|lower|title} {{#{1}}}")

    assert template == (routing.Slot("name", ("lower", "title")), " {#", routing.Slot("1"), "}")
    values = {"name": "ALIDA", "1": "7"}
    assert routing.render(template, values) == "Alida {#7}"


@pytest.mark.parametrize(
    ("template", "value", "expected"),
    [
        ("{value|title}", "alida smith", "Alida smith"),
        ("{value|lower}", "ALIDA", "alida"),
        ("{value|upper}", "alida", "ALIDA"),
        ("{value|tag_title}", "#machine_learning", "Machine learning"),
        ("{value|slug}", "Bàn luận chung!", "bàn_luận_chung"),
        ("{value|slug|upper}", "a b", "A_B"),  # filters chain left to right
    ],
)
def test_the_name_filters(template: str, value: str, expected: str) -> None:
    assert routing.render(routing.parse_template(template), {"value": value}) == expected


def test_a_name_is_one_tidy_line_cut_to_a_topic_title_and_empty_means_no_name() -> None:
    template = routing.parse_template("{value}")

    assert routing.render(template, {"value": "  a \n  b   c "}) == "a b c"
    assert routing.render(template, {"value": "x" * 300}) == "x" * routing.MAX_TOPIC_TITLE
    assert routing.render(template, {"value": "   "}) is None
    assert routing.render(template, {}) is None  # a group that did not take part


@pytest.mark.parametrize(
    ("template", "fragment"),
    [
        ("{value", "lone"),
        ("value}", "lone"),
        ("{va lue}", "not a name"),
        ("{value|shout}", "not a filter"),
        ("{}", "not a name"),
        ("{a.b}", "not a name"),
    ],
)
def test_a_template_that_cannot_be_read_says_why(template: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        routing.parse_template(template)


# ---- the first rule to give a name wins --------------------------------------------------------


def test_a_fixed_name_by_condition_comes_before_the_rules_after_it() -> None:
    topics = rules(
        {"when": {"hashtag": ["#vip"]}, "to": "VIP"},
        {"from": "hashtags", "to": "{value|tag_title}"},
    )

    assert one(topics, message(hashtags=("#news", "#vip"))) == "VIP"
    assert one(topics, message(hashtags=("#news",))) == "News"
    assert one(topics, message()) is None  # no hashtag, no source topic, no default


def test_case_one_picks_the_last_hashtag_and_leaves_the_advertising_ones_alone() -> None:
    last = rules(
        {"from": "hashtags", "pick": "last", "skip": ["#YYLive"], "to": "{value|tag_title}"}
    )

    assert one(last, message(hashtags=("#alida", "#yylive"))) == "Alida"  # the ad is not a topic
    assert one(last, message(hashtags=("#alida", "#bob", "#yylive"))) == "Bob"
    assert one(last, message(hashtags=("#yylive",))) is None


def test_case_one_can_keep_only_the_hashtags_that_fit_a_pattern() -> None:
    topics = rules({"from": "hashtags", "regex": "^#(?!yy)", "to": "{value|tag_title}"})

    assert one(topics, message(hashtags=("#yylive", "#alida"))) == "Alida"
    assert one(topics, message(hashtags=("#yylive",))) is None


FILE_NAME = r"(?m)^(?P<name>[^\s.]+?)(?:[_-]\d+)*\.(?:mp4|mkv|mov|jpe?g)\s*$"


def test_case_two_takes_a_name_out_of_the_file_name_written_in_the_caption() -> None:
    topics = rules({"from": "text", "regex": FILE_NAME, "to": "{name|title}"}, default="Other")

    assert one(topics, message(text="Alida_0412.mp4")) == "Alida"
    assert one(topics, message(text="new!\nbob-12.mkv\n#x")) == "Bob"
    assert one(topics, message(text="no file in here")) == "Other"  # the default takes the rest


def test_a_caption_gives_a_candidate_per_match_and_pick_chooses_between_them() -> None:
    first = {"from": "text", "regex": r"(?P<n>[A-Z]\w+)", "to": "{n}"}
    text = message(text="Alida met Bob")

    assert one(rules(first), text) == "Alida"
    assert one(rules({**first, "pick": "last"}), text) == "Bob"
    assert one(rules({**first, "skip": ["alida"]}), text) == "Bob"  # skip: no case, on the match


def test_a_rule_whose_name_comes_out_empty_gives_way_to_the_next_one() -> None:
    topics = rules(
        {"from": "text", "regex": r"id:(?P<id>\d*)", "to": "{id}"},
        {"to": "Else"},
    )

    assert one(topics, message(text="id:")) == "Else"
    assert one(topics, message(text="id:42")) == "42"


def test_the_groups_of_a_pattern_are_numbered_named_and_whole() -> None:
    topics = rules(
        {"from": "file_name", "regex": r"^(\w+)-(?P<kind>\w+)\.", "to": "{1}/{kind|upper} ({0})"}
    )

    assert one(topics, message(file_name="trip-photos.zip")) == "trip/PHOTOS (trip-photos.)"
    assert one(topics, message(file_name="plain.zip")) is None  # does not fit: not a candidate


def test_a_unit_that_is_an_album_offers_the_candidates_of_all_its_messages_in_order() -> None:
    album = unit(
        message(1, hashtags=("#a",)),
        message(2, text="cap", hashtags=("#b", "#a")),
        message(3, hashtags=("#c",)),
    )
    first = rules({"from": "hashtags", "to": "{value}"})
    last = rules({"from": "hashtags", "pick": "last", "to": "{value}"})

    assert first.destination(album) == "#a"
    assert last.destination(album) == "#c"  # distinct, in the order they were written


def test_other_places_a_candidate_can_come_from() -> None:
    by_topic = rules({"from": "topic", "regex": r"^(?P<n>\w+) chat$", "to": "{n|title}"})
    by_origin = rules({"from": "forwarded_from", "to": "From {value}"})
    titles = {5: "alida chat", 6: "other"}

    assert one(by_topic, message(topic_id=5), titles=titles) == "Alida"
    assert one(by_topic, message(topic_id=6), titles=titles) is None
    assert one(by_topic, message(), titles=titles) is None  # in no topic: nothing to read
    assert one(by_topic, message(topic_id=5)) is None  # titles not known
    assert one(by_origin, message(fwd_from_name="Some Channel")) == "From Some Channel"
    assert one(by_origin, message()) is None
    assert by_topic.needs_topic_titles and not by_origin.needs_topic_titles


def test_a_condition_can_name_the_source_topic_and_a_pattern_of_the_text() -> None:
    topics = rules(
        {"when": {"topic": [7], "regex": "(?i)part\\s*2"}, "to": "Part two"},
        {"when": {"topic": [7]}, "to": "Part one"},
    )

    assert one(topics, message(topic_id=7, text="Part 2: x")) == "Part two"
    assert one(topics, message(topic_id=7, text="intro")) == "Part one"
    assert one(topics, message(topic_id=8, text="part 2")) is None


def test_default_only_takes_a_message_that_sits_in_no_source_topic() -> None:
    topics = rules({"when": {"hashtag": ["#a"]}, "to": "A"}, default="Misc")

    assert one(topics, message(hashtags=("#b",))) == "Misc"
    assert one(topics, message(hashtags=("#b",), topic_id=3)) is None  # keeps its own topic
    assert one(topics, message(hashtags=("#a",), topic_id=3)) == "A"


# ---- strip: what routed a message comes out of its caption --------------------------------------


def stripped(topics: TopicRules, text: str, *messages: SrcMessage) -> str:
    ops = topics.strip_ops(unit(*(messages or (message(text=text),))))
    return rewrite_caption(text, [], CaptionPolicy(ops=ops), set())[0]


def test_strip_takes_the_matched_text_out_with_its_line_when_it_stood_alone() -> None:
    topics = rules({"from": "text", "regex": FILE_NAME, "to": "{name}"}, strip="matched")

    assert stripped(topics, "Alida_01.mp4\nnice day") == "nice day"
    assert stripped(topics, "nice day\nAlida_01.mp4") == "nice day"
    assert stripped(topics, "Alida_01.mp4") == ""
    # inside a line only the matched words go
    inline = rules({"from": "text", "regex": r"\bAlida\b", "to": "{0}"}, strip="matched")
    assert stripped(inline, "meet Alida today") == "meet  today"


def test_strip_takes_the_routing_hashtag_not_the_others() -> None:
    topics = rules({"from": "hashtags", "pick": "last", "to": "{value|tag_title}"}, strip="matched")
    both = message(hashtags=("#keep", "#alida"))

    assert topics.strip_ops(unit(both)) == (RemoveHashtags(("#alida",)),)
    assert topics.strips


def test_strip_has_nothing_to_take_when_the_name_did_not_come_from_the_caption() -> None:
    by_name = rules({"from": "file_name", "to": "{value}"}, strip="matched")
    by_topic = rules({"from": "topic", "to": "{value}"}, strip="matched")

    assert by_name.strips is False and by_topic.strips is False  # nothing to rewrite for it
    assert by_name.strip_ops(unit(message(file_name="a.zip"))) == ()


def test_a_fixed_rule_by_hashtag_strips_the_hashtags_of_its_condition_the_message_has() -> None:
    topics = rules({"when": {"hashtag": ["#vip", "#gold"]}, "to": "VIP"}, strip="matched")

    assert topics.strip_ops(unit(message(hashtags=("#gold", "#x")))) == (
        RemoveHashtags(("#gold",)),
    )
    plain = rules({"when": {"media": ["video"]}, "to": "Clips"}, strip="matched")
    assert plain.strips is False


def test_a_message_the_default_placed_has_nothing_stripped() -> None:
    topics = rules({"when": {"hashtag": ["#a"]}, "to": "A"}, default="Misc", strip="matched")

    assert topics.strip_ops(unit(message(hashtags=("#b",)))) == ()


# ---- what the guard counts ---------------------------------------------------------------------


def test_a_name_written_in_full_is_not_counted_by_limit_a_made_up_one_is() -> None:
    topics = rules(
        {"when": {"hashtag": ["#a"]}, "to": "Alida"},
        {"from": "hashtags", "to": "{value|tag_title}"},
        default="Misc",
        limit=5,
    )

    assert topics.variable and topics.limit == 5
    assert topics.fixed_names == {"alida", "misc"}
    assert not rules({"to": "Everything"}).variable


# ---- reading rules -----------------------------------------------------------------------------


def parse(text: str) -> Transform:
    return Transform.from_data(yaml.safe_load(text))


def test_route_is_read_written_back_and_round_trips() -> None:
    transform = parse(
        """
topics:
  route:
    - when: {hashtag: ["#VIP"], media: [video]}
      to: "VIP"
    - from: hashtags
      pick: last
      skip: ["#YYLive"]
      to: "{value|tag_title}"
    - from: text
      regex: '(?P<name>\\w+)\\.mp4'
      to: "{name|title}"
  default: Misc
  limit: 12
  strip: matched
"""
    )

    assert Transform.from_json(transform.to_json()) == transform
    assert Transform.from_data(transform.to_data()) == transform
    data = transform.to_data()["topics"]
    assert data["route"][0]["when"] == {"hashtag": ["#vip"], "media": ["video"]}
    assert data["route"][1] == {
        "from": "hashtags",
        "pick": "last",
        "skip": ["#yylive"],
        "to": "{value|tag_title}",
    }
    assert (data["default"], data["limit"], data["strip"]) == ("Misc", 12, "matched")
    assert transform.routes_to_topics and transform.rewrites  # strip: sent again


def test_the_short_form_and_the_rules_it_stands_for_are_one_transform() -> None:
    short = parse(
        """
topics:
  hashtag_to_topic:
    map: {"#python": "Python"}
    auto: all
    skip: ["#ads"]
    default: Misc
    strip: true
"""
    )
    long = parse(
        """
topics:
  route:
    - {when: {hashtag: ["#python"]}, to: Python}
    - {from: hashtags, skip: ["#ads"], to: "{value|tag_title}"}
  default: Misc
  strip: matched
"""
    )

    assert short == long and short.to_json() == long.to_json()


def test_a_name_with_braces_in_the_short_form_is_taken_literally() -> None:
    transform = parse('topics: {hashtag_to_topic: {map: {"#c": "C{x}"}}}')

    assert one(transform.topics, message(hashtags=("#c",))) == "C{x}"
    assert Transform.from_json(transform.to_json()) == transform


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("topics: {route: }", "nothing under it"),
        ("topics: {route: []}", "a list of rules"),
        ("topics: {route: {a: b}}", "a list of rules"),
        ("topics: {route: [x]}", "a mapping with `to:`"),
        ("topics: {route: [{from: hashtags}]}", "`to:` is missing"),
        ("topics: {route: [{to: A, colour: red}]}", "not a key of a rule"),
        ("topics: {route: [{to: ''}]}", "name of a topic"),
        ("topics: {route: [{to: A, from: sky}]}", r"route\[0\].from: one of"),
        ("topics: {route: [{to: A, regex: x}]}", "only means something with `from:`"),
        ("topics: {route: [{to: A, pick: last}]}", "only means something with `from:`"),
        ("topics: {route: [{to: A, skip: [x]}]}", "only means something with `from:`"),
        ("topics: {route: [{to: '{value}'}]}", "no `from:`"),
        ("topics: {route: [{from: hashtags, to: '{nope}'}]}", r"\{nope\} is not there"),
        ("topics: {route: [{from: hashtags, to: '{0}'}]}", r"\{0\} is not there"),
        ("topics: {route: [{from: text, to: '{value}'}]}", "needs a pattern"),
        ("topics: {route: [{from: text, regex: '(', to: A}]}", "regex"),
        ("topics: {route: [{from: text, regex: 'a(b)', to: '{2}'}]}", r"\{2\} is not there"),
        ("topics: {route: [{from: hashtags, pick: middle, to: A}]}", "first or last"),
        ("topics: {route: [{from: hashtags, skip: [two words], to: A}]}", "skip"),
        ("topics: {route: [{from: hashtags, to: '{value|shout}'}]}", "not a filter"),
        ("topics: {route: [{when: {nope: 1}, to: A}]}", r"route\[0\].when: nope"),
        ("topics: {route: [{when: [x], to: A}]}", "mapping of filter predicates"),
        ("topics: {route: [{when: {regex: '('}, to: A}]}", "regular expression"),
        ("topics: {route: [{to: A}], default: ''}", "name of a topic"),
        ("topics: {route: [{to: A}], limit: 5}", "only means something when"),
        (
            "topics: {route: [{from: hashtags, to: '{value}'}], limit: 0}",
            "from 1 to",
        ),
        ("topics: {route: [{to: A}], strip: maybe}", "`matched`"),
        ("topics: {route: [{to: A}], hashtag_to_topic: {auto: all}}", "write one of them"),
        ("topics: {hashtag_to_topic: {auto: all}, default: A}", "written inside it"),
        ("topics: {default: A}", "only means something with `route`"),
        ("topics: {strip: matched}", "only means something with `route`"),
    ],
)
def test_rules_that_cannot_be_used_are_refused_naming_the_part(text: str, fragment: str) -> None:
    with pytest.raises(TransformError, match=fragment):
        parse(text)


def test_a_fixed_name_longer_than_a_topic_title_is_refused() -> None:
    with pytest.raises(TransformError, match="at most 128"):
        rules({"to": "x" * (routing.MAX_TOPIC_TITLE + 1)})


def test_a_pattern_that_takes_too_long_on_a_message_is_an_error_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routing, "REGEX_TIMEOUT", 0.05)
    topics = rules({"from": "text", "regex": r"(a|aa)+$", "to": "{0}"})

    with pytest.raises(TransformError, match="took more than"):
        one(topics, message(text="a" * 60 + "b"))


def test_the_hashtag_flag_adds_the_every_hashtag_rule_after_the_ones_there_and_only_once() -> None:
    base = rules({"when": {"hashtag": ["#a"]}, "to": "A"})

    flagged = base.with_auto_all()

    assert flagged.rules[:1] == base.rules and flagged.rules[-1].is_auto_all
    assert flagged.with_auto_all() == flagged  # a second time changes nothing
    assert TopicRules().with_auto_all().routing


def test_a_media_condition_selects_by_the_kind_of_the_message() -> None:
    topics = rules({"when": {"media": ["video"]}, "to": "Clips"})

    assert one(topics, message(media=MediaKind.VIDEO)) == "Clips"
    assert one(topics, message(media=MediaKind.PHOTO)) is None
