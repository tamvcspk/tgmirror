"""``core/links.py``: the one definition of "the domain of this link" (phase 18a)."""

import pytest

from tgmirror.core.links import domain_matches, domain_of, domains_in_html, domains_of


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
