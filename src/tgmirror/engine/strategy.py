"""Which strategy sends a unit (docs/01-kien-truc.md, "Hai chiến lược clone").

``copy`` is the server-side forward, ``reupload`` downloads and sends again. ``mode`` is what the
user asked for; ``auto`` copies, except a unit whose caption has to be rewritten, which no forward
can do.
"""

from collections.abc import Callable
from enum import StrEnum

from tgmirror.core.gateway import CaptionMode, MediaKind, Unit

MODES = ("auto", "copy", "reupload")


class Strategy(StrEnum):
    COPY = "copy"
    REUPLOAD = "reupload"


Router = Callable[[Unit], Strategy]


def may_reupload(mode: str, caption: CaptionMode | str) -> bool:
    """Whether a run in ``mode`` with this caption handling can download content and send it
    again, which is what decision D3 guards for a source that restricts saving content."""
    return mode == "reupload" or (mode == "auto" and caption != CaptionMode.KEEP)


def has_caption(unit: Unit) -> bool:
    """A media message with a caption. The text of a text message (or of a link preview) is the
    content itself, not a caption, so it never counts."""
    return any(m.text and m.media not in (MediaKind.TEXT, MediaKind.WEBPAGE) for m in unit.messages)


def router(mode: str, caption: CaptionMode) -> Router:
    """The strategy of each unit for a run in ``mode`` with this caption handling."""
    if mode == "reupload":
        return lambda unit: Strategy.REUPLOAD
    if mode == "copy" or caption is CaptionMode.KEEP:
        return lambda unit: Strategy.COPY
    return lambda unit: Strategy.REUPLOAD if has_caption(unit) else Strategy.COPY
