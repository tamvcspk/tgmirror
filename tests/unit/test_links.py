"""``core/links.py``: the one definition of "the domain of this link" (phase 18a)."""

import pytest

from tgmirror.core.links import (
    MessageLink,
    domain_matches,
    domain_of,
    domains_in_html,
    domains_of,
    parse_message_link,
    points_at,
    relinked,
)


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.Example.com/a?b=1#c", "example.com"),
        ("http://blog.example.com:8080/x", "blog.example.com"),
        ("example.com/path", "example.com"),  # Telegram shows links without a scheme
        ("t.me/some_channel/12", "t.me"),
        ("https://user:pw@example.com/", "example.com"),
        ("tg://resolve?domain=x", None),  # not a web link
        ("mailto:a@example.com", None),
        ("localhost", None),  # no dot: not a domain a filter could name
        ("", None),
        ("http://[bad", None),
    ],
)
def test_domain_of(url: str, domain: str | None) -> None:
    assert domain_of(url) == domain


def test_domains_of_keeps_first_appearance_order_and_drops_duplicates() -> None:
    urls = ["https://b.com/1", "https://a.com/", "http://www.b.com/2", "tg://x"]
    assert domains_of(urls) == ("b.com", "a.com")


@pytest.mark.parametrize(
    ("domain", "wanted", "expected"),
    [
        ("example.com", "example.com", True),
        ("blog.example.com", "example.com", True),
        ("notexample.com", "example.com", False),  # a suffix of the text, not of the labels
        ("example.com", "blog.example.com", False),
    ],
)
def test_domain_matches(domain: str, wanted: str, expected: bool) -> None:
    assert domain_matches(domain, wanted) is expected


def test_domains_in_html_reads_hyperlinks_and_bare_urls() -> None:
    html = (
        'see <a href="https://docs.example.org/x?a=1&amp;b=2">the docs</a> and '
        "https://news.site.net/story, or www.third.io."
    )
    assert domains_in_html(html) == ("docs.example.org", "news.site.net", "third.io")


def test_domains_in_html_without_links_is_empty() -> None:
    assert domains_in_html("<b>no links</b> here #tag") == ()


# ---- links to messages (phase 19c3) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://t.me/news/123", MessageLink("news", 123)),
        ("http://t.me/News/123", MessageLink("news", 123)),  # usernames are not case-sensitive
        ("t.me/news/123", MessageLink("news", 123)),  # shown without a scheme
        ("https://t.me/news/123?single", MessageLink("news", 123)),
        ("https://telegram.me/news/7", MessageLink("news", 7)),
        ("https://www.t.me/news/7", MessageLink("news", 7)),
        ("https://t.me/c/1234567890/42", MessageLink("c/1234567890", 42)),
        ("https://t.me/c/1234567890/5/42", MessageLink("c/1234567890", 42)),  # topic 5, message 42
        ("https://t.me/forum_chat/5/42", MessageLink("forum_chat", 42)),
    ],
)
def test_a_link_to_a_message_names_the_chat_and_the_message(
    url: str, expected: MessageLink
) -> None:
    assert parse_message_link(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/news",  # a channel, not a message
        "https://t.me/joinchat/AAAA",
        "https://t.me/+AbCdEf",
        "https://t.me/addstickers/pack",
        "https://t.me/s/news/123",  # the web preview of a channel
        "https://t.me/c/123",
        "https://t.me/c/abc/1",
        "https://t.me/news/abc",
        "https://t.me/news/0",
        "https://example.org/news/123",
        "tg://resolve?domain=news&post=123",
        "mailto:a@b.c",
        "",
        "https://t.me/ab/1",  # shorter than any username
    ],
)
def test_what_is_not_a_link_to_a_message_is_none(url: str) -> None:
    assert parse_message_link(url) is None


def test_a_link_is_into_the_source_by_its_username_or_its_number() -> None:
    names = {"news", "1234567890"}

    assert points_at(MessageLink("news", 1), names)
    assert points_at(MessageLink("c/1234567890", 1), names)
    assert not points_at(MessageLink("other", 1), names)
    assert not points_at(MessageLink("c/999", 1), names)


def test_the_new_link_keeps_the_way_the_old_one_was_written() -> None:
    assert relinked("https://t.me/news/12", 7, "t.me/copy") == "https://t.me/copy/7"
    assert relinked("t.me/news/12", 7, "t.me/copy") == "t.me/copy/7"
    assert relinked("t.me/c/111/5/12", 7, "t.me/c/222") == "t.me/c/222/7"  # the topic is dropped
