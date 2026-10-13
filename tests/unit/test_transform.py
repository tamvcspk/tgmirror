"""Transform (phase 19): the model of a ``--transform`` file and the caption operations."""

from pathlib import Path

import pytest
from telethon import types

from tgmirror.core.gateway import (
    Append,
    CaptionMode,
    CaptionPolicy,
    Clear,
    RemoveHashtags,
    RemoveMentions,
    Replace,
    StripLinks,
    ops_of_mode,
)
from tgmirror.core.telethon_gateway import _slice16, rewrite_caption
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
        ("topics: {}", "not available yet"),
        ("forward_header: keep", "not available yet"),
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
    assert both.all_ops == (Append("x"), Replace("a", "b"))  # the flag first, then the list


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
