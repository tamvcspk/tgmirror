"""Pure helpers for the links in a message (phase 18a): the domain a URL points at, and the
domains an HTML/plain text mentions. No Telegram, no I/O (hard rule 8), so the live gateway, the
backup reader and the filter predicate all agree on what "the domain of this link" means.

Phase 19c3 adds the links to *messages* (``t.me/<name>/<id>``, ``t.me/c/<number>/<id>``, with a
topic id in between for a forum): what chat and message one points at, and what it becomes when
the message has a copy somewhere else.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
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


_TME_HOSTS = frozenset({"t.me", "telegram.me", "telegram.dog"})
_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")
# first path segments of a t.me link that are not a chat (a message link starts with a username)
_NOT_A_CHAT = frozenset(
    {"joinchat", "addstickers", "addemoji", "addtheme", "addlist", "setlanguage", "share", "proxy",
     "socks", "iv", "boost", "invoice", "giftcode", "contact", "confirmphone", "login", "bg",
     "telegrampassport", "s", "m", "k", "a", "web"}
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class MessageLink:
    """What a link to a message points at: ``chat`` is the lower-case username, or ``c/<number>``
    for a chat with none; ``msg_id`` the message (the topic of a forum link is dropped)."""

    chat: str
    msg_id: int


def parse_message_link(url: str) -> MessageLink | None:
    """The message ``url`` points at, or ``None`` for anything else (a channel, an invite link, a
    ``tg://`` link, a page of another site)."""
    text = url.strip()
    if not text:
        return None
    try:
        parts = urlsplit(text if "://" in text else "//" + text)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("", "http", "https"):
        return None
    host = (parts.hostname or "").casefold().removeprefix("www.")
    if host not in _TME_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if segments[:1] == ["c"]:
        if len(segments) not in (3, 4) or not all(s.isdecimal() for s in segments[1:]):
            return None
        chat = f"c/{int(segments[1])}"
    else:
        if len(segments) not in (2, 3) or not all(s.isdecimal() for s in segments[1:]):
            return None
        name = segments[0]
        if not _USERNAME.fullmatch(name) or name.casefold() in _NOT_A_CHAT:
            return None
        chat = name.casefold()
    msg_id = int(segments[-1])
    return MessageLink(chat, msg_id) if msg_id > 0 else None


def points_at(link: MessageLink, names: Iterable[str]) -> bool:
    """Whether ``link`` is into a chat called one of ``names`` (lower-case usernames and the bare
    number of its id, as ``telethon_gateway._source_names`` makes them)."""
    return link.chat.removeprefix("c/") in names


def relinked(original: str, new_id: int, prefix: str) -> str:
    """``original`` (a link to a message) pointed at message ``new_id`` of the chat whose link
    starts ``prefix`` (``t.me/name`` or ``t.me/c/123``). The scheme is written the way the
    original did, so a link shown as ``t.me/x/1`` stays as short as it was."""
    scheme = "https://" if original.strip().casefold().startswith(("http://", "https://")) else ""
    return f"{scheme}{prefix}/{new_id}"


def domain_matches(domain: str, wanted: str) -> bool:
    """``blog.example.com`` matches ``example.com``; both already normalised."""
    return domain == wanted or domain.endswith("." + wanted)
