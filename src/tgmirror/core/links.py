"""Pure helpers for the links in a message (phase 18a): the domain a URL points at, and the
domains an HTML/plain text mentions. No Telegram, no I/O (hard rule 8), so the live gateway, the
backup reader and the filter predicate all agree on what "the domain of this link" means.
"""

import re
from collections.abc import Iterable
from html import unescape
from urllib.parse import urlsplit

# ``href="..."`` of a hyperlink and bare ``http(s)://`` / ``www.`` text, as Telethon's HTML keeps
# them (a bare URL stays plain text, a hyperlink becomes ``<a href>``).
_HREF = re.compile(r"""<a\s[^>]*?href=(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_BARE_URL = re.compile(r"""(?:https?://|www\.)[^\s<>"']+""", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_TRAILING = ".,;:!?)]}»\"'"


def domain_of(url: str) -> str | None:
    """The lower-case host of ``url`` without ``www.`` and port, or ``None`` when it has none.

    A link Telegram shows as ``example.com/path`` has no scheme, so one is assumed; schemes other
    than http(s) (``tg://``, ``mailto:``) have no domain here.
    """
    text = url.strip()
    if not text:
        return None
    try:
        scheme = urlsplit(text).scheme if "://" in text or text.startswith("mailto:") else ""
        if scheme and scheme.lower() not in ("http", "https"):
            return None
        host = urlsplit(text if scheme else "//" + text).hostname
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    return host.casefold().removeprefix("www.")


def domains_of(urls: Iterable[str]) -> tuple[str, ...]:
    """The distinct domains of ``urls``, in order of first appearance."""
    found = (domain_of(u) for u in urls)
    return tuple(dict.fromkeys(d for d in found if d is not None))


def domains_in_html(text_html: str) -> tuple[str, ...]:
    """The domains a backup's ``text_html`` links to: hyperlinks and bare URLs. An approximation
    for backups made before ``link_domains`` was recorded (a live read uses the entities)."""
    hrefs = [unescape(m.group(2)) for m in _HREF.finditer(text_html)]
    plain = unescape(_TAG.sub(" ", text_html))
    bare = [m.group(0).rstrip(_TRAILING) for m in _BARE_URL.finditer(plain)]
    return domains_of([*hrefs, *bare])


def domain_matches(domain: str, wanted: str) -> bool:
    """``blog.example.com`` matches ``example.com``; both already normalised."""
    return domain == wanted or domain.endswith("." + wanted)
