"""Decide what happened to a batch that was ``pending`` when the last run died.

The rules are in docs/04-state-checkpoint.md ("Resume"): favour "no gap" over "no duplicate".
This module is the pure decision; the runner does the reading and the store writes.
"""

from collections.abc import Sequence
from enum import StrEnum

from tgmirror.core.gateway import SrcMessage


class Outcome(StrEnum):
    CONFIRMED = "confirmed"  # the copies are in the destination: mark the rows done
    RESEND = "resend"  # nothing new in the destination: the batch was never sent
    AMBIGUOUS = "ambiguous"  # something is there but does not match: send again, warn


def _shape(messages: Sequence[SrcMessage]) -> list[tuple[str, int | None]]:
    """Media kind per message plus which album (numbered by first appearance) it belongs to."""
    albums: dict[int, int] = {}
    shape: list[tuple[str, int | None]] = []
    for m in messages:
        number = None if m.grouped_id is None else albums.setdefault(m.grouped_id, len(albums))
        shape.append((m.media, number))
    return shape


def judge(pending: Sequence[SrcMessage], tail: Sequence[SrcMessage]) -> tuple[Outcome, list[int]]:
    """Compare the source messages that were pending with the destination's new tail.

    ``tail`` is what appeared in the destination after the last known copy (service messages
    excluded). Returns the outcome and, when confirmed, the destination ids aligned with
    ``pending``. Count, media kinds and album structure must all match to trust the tail: the
    destination's ids and ``grouped_id``s are new, so nothing else can be compared.
    """
    if not tail:
        return Outcome.RESEND, []
    if len(tail) == len(pending) and _shape(tail) == _shape(pending):
        return Outcome.CONFIRMED, [m.id for m in tail]
    return Outcome.AMBIGUOUS, []
