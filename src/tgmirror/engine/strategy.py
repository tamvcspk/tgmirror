"""Which strategy sends a unit (docs/01-kien-truc.md, "Hai chiến lược clone").

``copy`` is the server-side forward, ``reupload`` downloads and sends again, ``reference`` sends
the same files again by their id (nothing is downloaded or uploaded). ``mode`` is what the user
asked for; ``auto`` copies, except a unit whose caption has to be rewritten, which no forward can
do: that one goes by reference when the source lets content be saved and every message of the
unit is a file, and is downloaded and uploaded again otherwise. ``--mode reupload`` always
downloads: it is what the user asked for, and what a source that restricts saving content needs.
"""

from collections.abc import Callable
from enum import StrEnum

from tgmirror.core.gateway import CaptionMode, MediaKind, Unit

MODES = ("auto", "copy", "reupload")


class Strategy(StrEnum):
    COPY = "copy"
    REUPLOAD = "reupload"
    REFERENCE = "reference"


Router = Callable[[Unit], Strategy]


def may_reupload(mode: str, caption: CaptionMode | str) -> bool:
    """Whether a run in ``mode`` with this caption handling can download content and send it
    again, which is what decision D3 guards for a source that restricts saving content."""
    return mode == "reupload" or (mode == "auto" and caption != CaptionMode.KEEP)


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
    }
)


def has_files(unit: Unit) -> bool:
    """Every message of the unit is a file Telegram stores (photo, video, document, ...), so
    it can be sent again by its id."""
    return all(m.media not in FILELESS for m in unit.messages)


def router(mode: str, caption: CaptionMode, *, by_reference: bool = False) -> Router:
    """The strategy of each unit for a run in ``mode`` with this caption handling.

    ``by_reference`` says the source lets content be saved, so a unit that ``auto`` would
    download can be sent by its file ids instead."""
    if mode == "reupload":
        return lambda unit: Strategy.REUPLOAD
    if mode == "copy" or caption is CaptionMode.KEEP:
        return lambda unit: Strategy.COPY

    def auto(unit: Unit) -> Strategy:
        if not has_caption(unit):
            return Strategy.COPY
        return Strategy.REFERENCE if by_reference and has_files(unit) else Strategy.REUPLOAD

    return auto
