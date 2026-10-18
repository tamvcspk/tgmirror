"""Transform (phase 19): the model of a ``--transform`` file and the caption operations."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from telethon import types

from tgmirror.core.gateway import (
    Append,
    CaptionMode,
    CaptionPolicy,
    Clear,
    LinkRewrite,
    RemoveHashtags,
    RemoveMentions,
    Replace,
    SrcMessage,
    StripLinks,
    Unit,
    ops_of_mode,
)
from tgmirror.core.telethon_gateway import _plain_text, _slice16, rewrite_caption
from tgmirror.engine import transform as tf
from tgmirror.engine.transform import Transform, TransformError

# ---- the model --------------------------------------------------------------------------------


def parse(text: str) -> Transform:
    import yaml

    return Transform.from_data(yaml.safe_load(text))


def test_every_operation_is_read_in_order() -> None:
    transform = parse(
        """
version: 1
caption:
  - strip_links: source
  - remove_hashtags: ["#Ads", "quangcao"]
  - remove_mentions: all
  - replace: {regex: "(?i)contact @\\\\w+", with: ""}
  - append: "via B"
  - text_too: true
"""
    )

    assert transform.caption == (
        StripLinks("source"),
        RemoveHashtags(("#ads", "#quangcao")),
        RemoveMentions("all"),
        Replace("(?i)contact @\\w+", ""),
        Append("via B"),
    )
    assert transform.text_too and transform.rewrites_text


def test_the_canonical_form_round_trips_and_an_empty_transform_is_empty_text() -> None:
    transform = parse("caption: [{remove_hashtags: all}, {clear: true}]")

    assert Transform.from_json(transform.to_json()) == transform
    assert Transform().to_json() == "" and Transform.from_json("") == Transform()
    assert parse("caption: []").to_json() == ""
    # text_too on its own changes nothing, so it is not a transform
    assert parse("caption: [{text_too: true}]").is_empty


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("caption: [{strip_links: some}]", "source or all"),
        ("caption: [{shout: true}]", "not an operation"),
        ("caption: [{append: '  '}]", "the text to add"),
        ("caption: [{clear: false}]", "clear: true"),
        ("caption: [{replace: {regex: '(', with: ''}}]", "regex"),
        ("caption: [{replace: {with: 'x'}}]", "regex"),
        ("caption: [{remove_hashtags: []}]", "hashtags"),
        ("caption: [{remove_hashtags: ['two words']}]", "hashtag"),
        ("caption: [{strip_links: all, append: x}]", "one operation per item"),
        ("caption: [{text_too: true}, {text_too: false}]", "twice"),
        ("caption: [{clear: true}, {text_too: true}]", "would empty every text message"),
        ("caption: {strip_links: all}", "list"),
        ("version: 2", "not supported"),
        ("reply: yes", "`keep`"),
        ("reply: [keep]", "`keep`"),
        ("forward_header: yes", "`keep`"),
        ("links: yes", "`rewrite`"),
        ("colour: red", "not a transform key"),
    ],
)
def test_a_transform_that_cannot_be_used_is_refused(text: str, fragment: str) -> None:
    with pytest.raises(TransformError, match=fragment):
        parse(text)


def test_load_names_the_file_and_explains_a_yaml_slip(tmp_path: Path) -> None:
    broken = tmp_path / "t.yaml"
    broken.write_text("caption:\n  - remove_hashtags: [#ads]\n", encoding="utf-8")

    with pytest.raises(TransformError, match="Hashtags need quotes"):
        tf.load(broken)
    with pytest.raises(TransformError, match="cannot read"):
        tf.load(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("caption: [{clear: no}]\n", encoding="utf-8")
    with pytest.raises(TransformError, match="bad.yaml"):
        tf.load(bad)


# ---- topics (phase 19b) -----------------------------------------------------------------------

TOPICS = """
topics:
  hashtag_to_topic:
    map:
      "#Python": "Python"
      "#rust": "Rust lang"
    auto: ["#machine_learning", "#go"]
    default: "Misc"
    strip: true
  topic_map:
    "Old name": "New name"
    12: "Merged"
  hashtag_format: "#t_{slug}"
"""


def unit(*texts_tags: tuple[str, ...], topic_id: int | None = None, album: bool = False) -> Unit:
    """A unit whose messages carry the given hashtags (one tuple of tags per message)."""
    when = datetime(2026, 1, 1, tzinfo=UTC)
    gid = 7 if album or len(texts_tags) > 1 else None
    return Unit(
        tuple(
            SrcMessage(i + 1, when, hashtags=tags, grouped_id=gid, topic_id=topic_id)
            for i, tags in enumerate(texts_tags)
        )
    )


def test_topics_are_read_normalised_and_keep_their_priority_order() -> None:
    rules = parse(TOPICS).topics

    # the short form is read as the rules it stands for
    assert [(r.when.hashtag if r.when else None, r.to) for r in rules.rules] == [
        (("#python",), "Python"),
        (("#rust",), "Rust lang"),
        (("#machine_learning",), "Machine learning"),  # ``auto`` names the topic after the tag
        (("#go",), "Go"),
    ]
    assert rules.default == "Misc" and rules.strip and rules.strips
    assert rules.renames == ((12, "Merged"), ("old name", "New name"))
    assert rules.hashtag_format == "#t_{slug}"


def test_a_transform_with_topics_round_trips_and_is_not_empty() -> None:
    transform = parse(TOPICS)

    assert Transform.from_json(transform.to_json()) == transform
    assert Transform.from_data(transform.to_data()) == transform  # the canonical form is valid
    assert not transform.is_empty and transform.routes_to_topics
    # only ``strip`` makes the transform rewrite anything: routing alone is a forward
    assert transform.rewrites
    routing = parse('topics: {hashtag_to_topic: {map: {"#a": "A"}}}')
    assert not routing.is_empty and routing.routes_to_topics and not routing.rewrites
    names = parse('topics: {topic_map: {"Old": "New"}}')
    assert not names.is_empty and not names.routes_to_topics and not names.rewrites
    assert parse("topics: {}").is_empty and parse("topics:").is_empty


def test_the_first_rule_a_unit_matches_decides_not_the_order_of_its_hashtags() -> None:
    rules = parse(TOPICS).topics

    both = unit(("#rust", "#python"))
    assert rules.destination(both) == "Python"
    assert rules.strip_ops(both) == (RemoveHashtags(("#python",)),)
    # an album is judged as a whole: the caption (and its tags) is on one of its messages
    album = unit((), ("#Rust",), ())
    assert rules.destination(album) == "Rust lang"
    assert rules.strip_ops(album) == (RemoveHashtags(("#rust",)),)


def test_default_takes_only_a_message_that_sits_in_no_source_topic() -> None:
    rules = parse(TOPICS).topics

    assert rules.destination(unit(("#other",))) == "Misc"
    assert rules.destination(unit(())) == "Misc"
    # in a topic of a forum source it keeps that topic's own mapping
    assert rules.destination(unit(("#other",), topic_id=5)) is None
    # a matching hashtag still wins over the source topic
    assert rules.destination(unit(("#go",), topic_id=5)) == "Go"
    # without ``strip`` nothing is taken out of a caption
    no_strip = parse('topics: {hashtag_to_topic: {map: {"#a": "A"}}}').topics
    assert no_strip.strip_ops(unit(("#a",))) == ()


def test_topic_map_renames_by_id_then_by_title_and_names_the_hashtag() -> None:
    rules = parse(TOPICS).topics

    assert rules.rename(12, "Anything") == "Merged"
    assert rules.rename(3, "OLD NAME") == "New name"  # a title matches without case
    assert rules.rename(4, "Untouched") == "Untouched"
    assert rules.hashtag_of("New name") == "#t_new_name"
    assert rules.hashtag_of("Bàn luận chung!") == "#t_bàn_luận_chung"
    assert rules.hashtag_of("!!!") == "#t_topic"
    assert Transform().topics.hashtag_of("Off-topic") == "#off_topic"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("topics: [x]", "a mapping"),
        ("topics: {colour: red}", "not a topics key"),
        ("topics: {hashtag_to_topic: [x]}", "a mapping"),
        ("topics:\n  hashtag_to_topic:\n", "nothing under it"),  # a key left empty
        ("topics:\n  topic_map:\n", "nothing under it"),
        ("topics: {hashtag_format: ~}", "nothing under it"),
        ("topics: {hashtag_to_topic: {}}", "give `map` or `auto`"),
        ("topics: {hashtag_to_topic: {map: {}}}", "give `map` or `auto`"),
        ('topics: {hashtag_to_topic: {default: "A"}}', "give `map` or `auto`"),
        ('topics: {hashtag_to_topic: {map: {"#a": "A"}, rule: 1}}', "not a key"),
        ('topics: {hashtag_to_topic: {map: {"two words": "A"}}}', "hashtag"),
        ('topics: {hashtag_to_topic: {map: {"#a": ""}}}', "name of a topic"),
        ('topics: {hashtag_to_topic: {map: {"#a": "x\\ny"}}}', "one line"),
        ('topics: {hashtag_to_topic: {map: {"#a": "A", "#A": "B"}}}', "twice"),
        ('topics: {hashtag_to_topic: {map: {"#a": "A"}, auto: ["#a"]}}', "twice"),
        ('topics: {hashtag_to_topic: {map: {"#a": "A"}, strip: yes please}}', "true or false"),
        ('topics: {hashtag_to_topic: {map: {"#a": "A"}, default: 5}}', "name of a topic"),
        ('topics: {topic_map: {"Old": null}}', "exclude it with a filter"),
        ("topics: {topic_map: {1: General}}", "General has no topic id"),
        ('topics: {topic_map: {"Old": "A", "old": "B"}}', "twice"),
        ("topics: {topic_map: [{id: 5}]}", "id or title"),
        ('topics: {hashtag_to_topic: {map: {"#a": "A"}, limit: 5}}', "only means something"),
        ("topics: {hashtag_to_topic: {auto: all, limit: 0}}", "from 1 to"),
        ("topics: {hashtag_to_topic: {auto: all, limit: many}}", "from 1 to"),
        ('topics: {hashtag_to_topic: {auto: ["#a"], skip: ["#b"]}}', "only means something"),
        ("topics: {hashtag_to_topic: {auto: all, skip: []}}", "a list of hashtags"),
        ("topics: {hashtag_to_topic: {auto: 5}}", "`all`, or a list"),
        ('topics: {hashtag_format: "#topic"}', "{slug}"),
        ('topics: {hashtag_format: "#{slug}{other}"}', "placeholder"),
        ('topics: {hashtag_format: "t {slug}"}', "one hashtag"),
    ],
)
def test_topics_that_cannot_be_used_are_refused(text: str, fragment: str) -> None:
    with pytest.raises(TransformError, match=fragment):
        parse(text)


def test_auto_all_names_a_topic_after_the_first_hashtag_written_in_the_message() -> None:
    rules = parse(
        "topics: {hashtag_to_topic: {map: {'#python': 'Python'}, auto: all, skip: ['#ads']}}"
    ).topics

    assert rules.routing and rules.variable and rules.limit == tf.DEFAULT_AUTO_LIMIT
    # an explicit rule first, whatever the order in the message
    assert rules.destination(unit(("#go", "#python"))) == "Python"
    # no rule: the first hashtag that is not skipped, named after itself
    assert rules.destination(unit(("#machine_learning", "#go"))) == "Machine learning"
    assert rules.destination(unit(("#ads", "#Go"))) == "Go"
    assert rules.strip_ops(unit(("#ads", "#Go"))) == ()  # ``strip`` is off
    # only skipped tags, or none: no topic of its own
    assert rules.destination(unit(("#ads",))) is None
    assert rules.destination(unit(())) is None


def test_auto_all_with_its_limit_and_skip_round_trips() -> None:
    transform = parse(
        "topics: {hashtag_to_topic: {auto: all, limit: 5, skip: ['#ads', '#News'], strip: true}}"
    )

    assert transform.topics.limit == 5
    assert transform.topics.rules[-1].is_auto_all and transform.topics.rules[-1].skip == (
        "#ads",
        "#news",
    )
    assert Transform.from_json(transform.to_json()) == transform
    assert transform.routes_to_topics and transform.rewrites  # strip: sent again
    assert parse("topics: {hashtag_to_topic: {auto: true}}").topics.rules[-1].is_auto_all


def test_flags_layered_on_a_file_replace_a_single_value_and_add_to_the_renames() -> None:
    base = parse(TOPICS).topics  # default "Misc", strip on, renames ((12, Merged), (old name, ...))

    rules = base.layered(
        renames=[(12, "Twelve"), ("Chat", "Talk"), ("OLD name", "Newer")], default="Else"
    )

    assert rules.default == "Else"  # the flag's, not the file's
    # the file's and the flag's together; a topic both name takes the flag's; sorted (canonical)
    assert rules.renames == ((12, "Twelve"), ("chat", "Talk"), ("old name", "Newer"))
    assert rules.rules == base.rules and rules.strip == base.strip  # untouched
    assert Transform.from_data(Transform(topics=rules).to_data()) == Transform(topics=rules)


def test_strip_is_only_ever_turned_on_by_a_flag_and_needs_routing() -> None:
    routing = parse('topics: {hashtag_to_topic: {map: {"#a": "A"}}}').topics

    assert not routing.strip
    assert routing.layered(strip=True).strip
    assert routing.layered().strip is False  # no flag, no change
    assert routing.layered() == routing


@pytest.mark.parametrize(
    ("flags", "fragment"),
    [
        ({"default": "Else"}, "only mean something with routing"),
        ({"strip": True}, "only mean something with routing"),
        ({"renames": [(1, "Home")]}, "General has no topic id"),
        ({"renames": [("Old", "A"), ("old", "B")]}, "twice"),
        ({"renames": [("Old", "")]}, "name of a topic"),
    ],
)
def test_flags_with_nothing_to_act_on_or_a_bad_value_are_refused(
    flags: dict[str, object], fragment: str
) -> None:
    only_names = parse('topics: {topic_map: {"Old": "New"}}').topics  # no routing in the file

    with pytest.raises(TransformError, match=fragment):
        only_names.layered(**flags)  # type: ignore[arg-type]


def test_a_flag_default_is_checked_like_the_files() -> None:
    routing = parse('topics: {hashtag_to_topic: {map: {"#a": "A"}}}').topics

    with pytest.raises(TransformError, match="--topic-default: the name of a topic"):
        routing.layered(default="two\nlines")
    assert routing.layered(default="  Else ").default == "Else"


def test_a_title_longer_than_telegram_takes_is_refused() -> None:
    long = "x" * (tf.MAX_TOPIC_TITLE + 1)

    with pytest.raises(TransformError, match="at most 128"):
        parse(f'topics: {{topic_map: {{"Old": "{long}"}}}}')


# ---- the operations ---------------------------------------------------------------------------


def policy(*ops: object, hashtag: str | None = None) -> CaptionPolicy:
    return CaptionPolicy(ops=tuple(ops), hashtag=hashtag)  # type: ignore[arg-type]


def hashtag(text: str, tag: str) -> types.MessageEntityHashtag:
    at = len(text[: text.index(tag)].encode("utf-16-le")) // 2
    return types.MessageEntityHashtag(at, len(tag.encode("utf-16-le")) // 2)


def test_the_caption_flag_is_a_shorthand_for_operations() -> None:
    assert ops_of_mode(CaptionMode.KEEP) == ()
    assert ops_of_mode(CaptionMode.NONE) == (Clear(),)
    assert ops_of_mode(CaptionMode.APPEND, "x") == (Append("x"),)
    assert ops_of_mode(CaptionMode.STRIP_LINKS) == (StripLinks("source"), RemoveMentions("source"))
    both = CaptionPolicy(CaptionMode.APPEND, "x", ops=(Replace("a", "b"),))
    assert both.all_ops == (Replace("a", "b"), Append("x"))  # the file's list, then the flag


def test_remove_named_hashtags_keeps_the_others_and_closes_the_gap() -> None:
    text = "news #ads of the day #keep"
    entities = [hashtag(text, "#ads"), hashtag(text, "#keep")]

    new_text, kept = rewrite_caption(text, entities, policy(RemoveHashtags(("#ads",))), set())

    assert new_text == "news of the day #keep"
    (left,) = kept
    assert _slice16(new_text, left.offset, left.length) == "#keep"  # type: ignore[attr-defined]


def test_remove_every_hashtag_at_the_end_leaves_the_text_clean() -> None:
    text = "Tutorial part 2\n\n#python #ads"
    entities = [hashtag(text, "#python"), hashtag(text, "#ads")]

    assert rewrite_caption(text, entities, policy(RemoveHashtags()), set()) == (
        "Tutorial part 2",
        [],
    )


def test_a_hashtag_is_matched_regardless_of_case_and_a_bot_suffix() -> None:
    text = "#ADS@somebot rest"
    entities = [types.MessageEntityHashtag(0, 12)]

    new_text, kept = rewrite_caption(text, entities, policy(RemoveHashtags(("#ads",))), set())

    assert (new_text, kept) == ("rest", [])


def test_remove_mentions_all_or_only_the_source() -> None:
    text = "ask @alice or @news_chan"
    entities = [types.MessageEntityMention(4, 6), types.MessageEntityMention(14, 10)]

    only_source, _ = rewrite_caption(
        text, entities, policy(RemoveMentions("source")), {"news_chan"}
    )
    everyone, left = rewrite_caption(text, entities, policy(RemoveMentions("all")), {"news_chan"})

    assert only_source == "ask @alice or"
    assert everyone == "ask  or" and left == []  # as ``--caption strip-links``: no tidying


def test_strip_links_all_removes_a_link_that_is_not_the_sources() -> None:
    text = "see https://example.org/x now"
    entities = [types.MessageEntityUrl(4, 21)]

    kept_by_source, _ = rewrite_caption(text, entities, policy(StripLinks("source")), {"news"})
    stripped, _ = rewrite_caption(text, entities, policy(StripLinks("all")), {"news"})

    assert kept_by_source == text
    assert stripped == "see  now"


def test_replace_keeps_formatting_over_the_replaced_words() -> None:
    text = "call 0123 now, call 0123 later"
    bold = types.MessageEntityBold(len("call "), 4)  # the first number

    new_text, kept = rewrite_caption(text, [bold], policy(Replace(r"\d{4}", "REDACTED")), set())

    assert new_text == "call REDACTED now, call REDACTED later"
    (still,) = kept
    assert _slice16(new_text, still.offset, still.length) == "REDACTED"  # type: ignore[attr-defined]


def test_replace_counts_offsets_in_utf16_units() -> None:
    text = "😀 liên hệ @abc 😀 end"
    end_at = len("😀 liên hệ @abc 😀 ".encode("utf-16-le")) // 2
    bold = types.MessageEntityBold(end_at, 3)

    new_text, kept = rewrite_caption(text, [bold], policy(Replace(r"liên hệ @\w+", "")), set())

    assert new_text == "😀  😀 end"
    (still,) = kept
    assert _slice16(new_text, still.offset, still.length) == "end"  # type: ignore[attr-defined]


def test_replace_that_matches_nothing_or_the_empty_string_changes_nothing() -> None:
    bold = [types.MessageEntityBold(0, 3)]

    assert rewrite_caption("abc", bold, policy(Replace("zzz", "x")), set()) == ("abc", bold)
    assert rewrite_caption("abc", bold, policy(Replace("x*", "-")), set()) == ("abc", bold)


def test_operations_run_in_order_and_the_topic_hashtag_goes_last() -> None:
    text = "hello #ads"
    entities = [hashtag(text, "#ads")]

    new_text, _ = rewrite_caption(
        text,
        entities,
        policy(RemoveHashtags(("#ads",)), Replace("hello", "hi"), Append("via B"), hashtag="#t"),
        set(),
    )

    assert new_text == "hi\n\nvia B\n#t"


def test_clear_then_nothing_to_append_to() -> None:
    assert rewrite_caption("abc", [], policy(Clear(), Append("x")), set()) == ("", [])


# ---- replies (phase 19c) ----------------------------------------------------------------------


def test_reply_keep_is_a_transform_of_its_own_and_round_trips() -> None:
    kept = parse("reply: keep")

    assert kept.keep_replies and not kept.is_empty
    assert kept.rewrites and not kept.rewrites_content  # sent again, but no text changes
    assert kept.to_data() == {"version": 1, "reply": "keep"}
    assert Transform.from_json(kept.to_json()) == kept
    assert Transform.from_json(
        parse("reply: keep\ncaption: [{clear: true}]").to_json()
    ).keep_replies


def test_reply_drop_is_the_default_spelt_out() -> None:
    assert parse("reply: drop").is_empty and parse("reply: drop") == Transform()
    assert parse("reply:").is_empty


def test_text_too_needs_a_caption_operation_not_just_a_reply() -> None:
    """``text_too`` says which messages a caption operation touches; kept replies are not one."""
    assert not parse("reply: keep\ncaption: [{text_too: true}]").rewrites_text
    assert parse("reply: keep\ncaption: [{append: x}, {text_too: true}]").rewrites_text


# ---- links to messages (phase 19c3) -----------------------------------------------------------


def test_links_rewrite_is_a_transform_of_its_own_and_round_trips() -> None:
    relinked = parse("links: rewrite")

    assert relinked.rewrite_links and not relinked.is_empty
    assert relinked.rewrites and not relinked.rewrites_content
    assert relinked.to_data() == {"version": 1, "links": "rewrite"}
    assert Transform.from_json(relinked.to_json()) == relinked
    assert parse("links: keep").is_empty


def test_links_rewrite_cannot_go_with_the_forward_header() -> None:
    with pytest.raises(TransformError, match="`links: rewrite`"):
        parse("forward_header: keep\nlinks: rewrite")


def links(**mapping: int) -> LinkRewrite:
    return LinkRewrite(
        tuple((int(k.removeprefix("m")), v) for k, v in mapping.items()), "t.me/copy"
    )


def linking(**mapping: int) -> CaptionPolicy:
    return CaptionPolicy(links=links(**mapping))


def test_a_hyperlink_gets_the_address_of_the_copy_and_keeps_its_words() -> None:
    text = "read the first part"
    link = types.MessageEntityTextUrl(5, 9, url="https://t.me/news/12")

    new_text, (kept,) = rewrite_caption(text, [link], linking(m12=7), {"news"})

    assert new_text == text
    assert kept.url == "https://t.me/copy/7" and (kept.offset, kept.length) == (5, 9)
    assert link.url == "https://t.me/news/12"  # the source message's own entity is untouched


def test_a_visible_link_is_rewritten_in_the_text_and_the_entities_after_it_move() -> None:
    text = "see t.me/news/12 and #tag"
    url = types.MessageEntityUrl(4, len("t.me/news/12"))
    tag = hashtag(text, "#tag")

    new_text, (new_url, new_tag) = rewrite_caption(text, [url, tag], linking(m12=1034), {"news"})

    assert new_text == "see t.me/copy/1034 and #tag"
    assert (new_url.offset, new_url.length) == (4, len("t.me/copy/1034"))
    assert _slice16(new_text, new_tag.offset, new_tag.length) == "#tag"


def test_only_links_into_the_source_that_have_a_copy_change() -> None:
    text = "a t.me/news/1 b t.me/other/1 c t.me/news/2"
    entities = [
        types.MessageEntityUrl(2, len("t.me/news/1")),
        types.MessageEntityUrl(16, len("t.me/other/1")),
        types.MessageEntityUrl(31, len("t.me/news/2")),
    ]

    new_text, _ = rewrite_caption(text, entities, linking(m1=10), {"news"})

    assert new_text == "a t.me/copy/10 b t.me/other/1 c t.me/news/2"  # 2 has no copy yet


def test_the_links_are_rewritten_before_strip_links_looks_for_the_sources() -> None:
    text = "see t.me/news/12 now"
    url = types.MessageEntityUrl(4, len("t.me/news/12"))
    both = CaptionPolicy(ops=(StripLinks("source"),), links=links(m12=7))

    new_text, _ = rewrite_caption(text, [url], both, {"news"})

    assert new_text == "see t.me/copy/7 now"  # not stripped: it no longer points at the source


def test_a_text_message_has_its_links_rewritten_without_text_too() -> None:
    text = "see t.me/news/12"
    url = types.MessageEntityUrl(4, len("t.me/news/12"))

    new_text, kept = _plain_text(text, [url], linking(m12=7), {"news"})

    assert new_text == "see t.me/copy/7" and len(kept) == 1


def test_the_link_in_a_text_goes_through_the_operations_of_text_too() -> None:
    text = "see t.me/news/12 #ads"
    both = CaptionPolicy(ops=(RemoveHashtags(("#ads",)),), text_too=True, links=links(m12=7))
    entities = [types.MessageEntityUrl(4, len("t.me/news/12")), hashtag(text, "#ads")]

    new_text, _ = _plain_text(text, entities, both, {"news"})

    assert new_text == "see t.me/copy/7"
