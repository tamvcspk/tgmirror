"""Which strategy sends a unit (docs/01-kien-truc.md, "Hai chiến lược clone").

``copy`` is the server-side forward, ``reupload`` downloads and sends again, ``reference`` sends
the same files again by their id (nothing is downloaded or uploaded). ``mode`` is what the user
asked for; ``auto`` copies, except a unit whose caption has to be rewritten (``--caption``, or the
topic hashtag of a forum copied into a chat without topics, phase 8; or a reply a transform keeps,
phase 19c), which no forward can do:
that one goes by reference when the source lets content be saved and every message of the
unit is a file, and is downloaded and uploaded again otherwise. ``--mode reupload`` always
downloads: it is what the user asked for, and what a source that restricts saving content needs.
"""

from collections.abc import Callable
from enum import StrEnum

from tgmirror.core.gateway import CaptionMode, MediaKind, Unit
from tgmirror.engine.transform import TopicRules, Transform

MODES = ("auto", "copy", "reupload")


class Strategy(StrEnum):
    COPY = "copy"
    REUPLOAD = "reupload"
    REFERENCE = "reference"


Router = Callable[[Unit], Strategy]


def may_reupload(
    mode: str,
    caption: CaptionMode | str,
    topic_hashtag: bool = False,
    transform: Transform | None = None,
) -> bool:
    """Whether a run in ``mode`` with this caption handling (and, phase 8, a topic hashtag to
    append; phase 19, a transform) can download content and send it again, which is what decision
    D3 guards for a source that restricts saving content."""
    rewrites = (
        caption != CaptionMode.KEEP
        or topic_hashtag
        or (transform is not None and transform.rewrites)
    )
    return mode == "reupload" or (mode == "auto" and rewrites)


def has_plain_text(unit: Unit) -> bool:
    """A text message (or a link preview): the content itself, which only a transform with
    ``text_too`` rewrites (phase 19)."""
    return any(m.text and m.media in (MediaKind.TEXT, MediaKind.WEBPAGE) for m in unit.messages)


def has_caption(unit: Unit) -> bool:
    """A media message with a caption. The text of a text message (or of a link preview) is the
    content itself, not a caption, so it never counts."""
    return any(m.text and m.media not in (MediaKind.TEXT, MediaKind.WEBPAGE) for m in unit.messages)


# media the message itself carries (or nothing to send): no file whose id could be reused
FILELESS = frozenset(
    {
        MediaKind.TEXT,
        MediaKind.WEBPAGE,
        MediaKind.POLL,
        MediaKind.GEO,
        MediaKind.CONTACT,
        MediaKind.GAME,
        MediaKind.INVOICE,
        MediaKind.OTHER,
    }
)


# What cannot be sent again just to give it a reply: a poll would lose its votes, a game, an
# invoice and an unknown kind have no copy to send. ``auto`` forwards these and the reply is lost
# (phase 19c).
KEEP_FORWARDING = frozenset({MediaKind.POLL, MediaKind.GAME, MediaKind.INVOICE, MediaKind.OTHER})


def can_be_sent_again(unit: Unit) -> bool:
    return not any(m.media in KEEP_FORWARDING for m in unit.messages)


def answers_something(unit: Unit) -> bool:
    """A unit that has a reply to keep and can be sent again to carry it (phase 19c)."""
    return unit.reply_to_id is not None and can_be_sent_again(unit)


def links_to_source(unit: Unit) -> bool:
    """A unit whose text links to a message of its own chat and can be sent again with the link
    pointed at the copy (phase 19c3)."""
    return bool(unit.own_links) and can_be_sent_again(unit)


# no caption to append a topic hashtag to: ``auto`` keeps forwarding these (a poll sent again would
# even lose its votes, ``reupload.plan_unit``)
NO_CAPTION = frozenset(
    {
        MediaKind.POLL,
        MediaKind.GEO,
        MediaKind.CONTACT,
        MediaKind.GAME,
        MediaKind.INVOICE,
        MediaKind.OTHER,
        MediaKind.STICKER,
        MediaKind.VIDEO_NOTE,
    }
)


def takes_hashtag(unit: Unit) -> bool:
    """A forum unit outside General with somewhere to put its topic hashtag (phase 8)."""
    return unit.topic_id is not None and any(m.media not in NO_CAPTION for m in unit.messages)


def has_files(unit: Unit) -> bool:
    """Every message of the unit is a file Telegram stores (photo, video, document, ...), so
    it can be sent again by its id."""
    return all(m.media not in FILELESS for m in unit.messages)


def router(
    mode: str,
    caption: CaptionMode,
    *,
    by_reference: bool = False,
    topic_hashtag: bool = False,
    transform: Transform | None = None,
) -> Router:
    """The strategy of each unit for a run in ``mode`` with this caption handling.

    ``by_reference`` says the source lets content be saved, so a unit that ``auto`` would
    download can be sent by its file ids instead. ``topic_hashtag`` (phase 8) says a forum unit
    must carry its topic as a hashtag, which only a unit sent again can do. ``transform``
    (phase 19) rewrites captions (and, with ``text_too``, plain text) the same way, and with
    ``keep_replies`` sends again a unit that answers a message, and ``rewrite_links`` one that
    links to a message of its own chat (it is the runner that finds out which message at the
    destination that is, or that there is none)."""
    if mode == "reupload":
        return lambda unit: Strategy.REUPLOAD
    topics = transform.topics if transform is not None else TopicRules()
    caption_ops = caption is not CaptionMode.KEEP or (transform is not None and transform.caption)
    text_too = transform is not None and transform.text_too
    replies = transform is not None and transform.keep_replies
    links = transform is not None and transform.rewrite_links
    if mode == "copy" or not (caption_ops or topics.strips or topic_hashtag or replies or links):
        return lambda unit: Strategy.COPY

    def auto(unit: Unit) -> Strategy:
        # caption operations change every unit; ``strip`` (phase 19b) only the ones a rule routed
        # by a hashtag, whose caption must lose it, which no forward does
        changed = bool(caption_ops) or (topics.strips and topics.strip_tag(unit) is not None)
        rewritten = changed and (has_caption(unit) or (text_too and has_plain_text(unit)))
        answering = replies and answers_something(unit)
        relinking = links and links_to_source(unit)
        if not (rewritten or answering or relinking or (topic_hashtag and takes_hashtag(unit))):
            return Strategy.COPY
        return Strategy.REFERENCE if by_reference and has_files(unit) else Strategy.REUPLOAD

    return auto
