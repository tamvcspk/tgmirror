"""Decide what happened to a batch that was ``pending`` when the last run died.

The rules are in docs/04-state-checkpoint.md ("Resume"): favour "no gap" over "no duplicate".
This module is the pure decision; the runner does the reading and the store writes.
"""

from collections.abc import Collection, Sequence
from enum import StrEnum

from tgmirror.core.gateway import MediaKind, SrcMessage


class Outcome(StrEnum):
    CONFIRMED = "confirmed"  # the copies are in the destination: mark the rows done
    RESEND = "resend"  # nothing new in the destination: the batch was never sent
    AMBIGUOUS = "ambiguous"  # something is there but does not match: send again, warn


def _shape(
    messages: Sequence[SrcMessage], placeholders: Collection[int] = ()
) -> list[tuple[str, int | None]]:
    """Media kind per message plus which album (numbered by first appearance) it belongs to.

    ``placeholders`` (T2, Phase 15b): source ids the run would send as a text placeholder
    (``engine/reupload.py``'s ``--placeholder``, a game/invoice/unanswered quiz) — reported as
    ``MediaKind.TEXT`` here instead of their real kind, to match what actually landed in the
    destination. Without this, a pending placeholder's own media kind (game/invoice/poll) never
    matched the plain text message the destination's tail actually held, so ``judge`` always came
    back ``AMBIGUOUS`` and sent it again, duplicating it.
    """
    albums: dict[int, int] = {}
    shape: list[tuple[str, int | None]] = []
    for m in messages:
        number = None if m.grouped_id is None else albums.setdefault(m.grouped_id, len(albums))
        media = MediaKind.TEXT if m.id in placeholders else m.media
        shape.append((media, number))
    return shape


def judge(
    pending: Sequence[SrcMessage], tail: Sequence[SrcMessage], *, placeholders: Collection[int] = ()
) -> tuple[Outcome, list[int]]:
    """Compare the source messages that were pending with the destination's new tail.

    ``tail`` is what appeared in the destination after the last known copy (service messages
    excluded). Returns the outcome and, when confirmed, the destination ids aligned with
    ``pending``. Count, media kinds and album structure must all match to trust the tail: the
    destination's ids and ``grouped_id``s are new, so nothing else can be compared. ``placeholders``
    (T2, Phase 15b): see ``_shape``.
    """
    if not tail:
        return Outcome.RESEND, []
    if len(tail) == len(pending) and _shape(tail) == _shape(pending, placeholders):
        return Outcome.CONFIRMED, [m.id for m in tail]
    return Outcome.AMBIGUOUS, []
