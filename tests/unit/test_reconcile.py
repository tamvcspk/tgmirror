"""The reconcile decision (docs/04-state-checkpoint.md, "Resume"): no gap beats no duplicate."""

from datetime import UTC, datetime

from tgmirror.core.gateway import MediaKind, SrcMessage
from tgmirror.engine.reconcile import Outcome, judge

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PHOTO, VIDEO = MediaKind.PHOTO, MediaKind.VIDEO


def msg(i: int, media: MediaKind = MediaKind.TEXT, group: int | None = None) -> SrcMessage:
    return SrcMessage(i, NOW, media=media, grouped_id=group)


def test_nothing_new_in_the_destination_means_the_batch_was_never_sent() -> None:
    assert judge([msg(1), msg(2)], []) == (Outcome.RESEND, [])


def test_a_matching_tail_confirms_the_batch_and_maps_ids_in_order() -> None:
    pending = [msg(10), msg(11, PHOTO)]
    tail = [msg(500), msg(501, PHOTO)]

    assert judge(pending, tail) == (Outcome.CONFIRMED, [500, 501])


def test_an_album_matches_by_structure_not_by_its_new_group_id() -> None:
    pending = [msg(1, PHOTO, group=7), msg(2, VIDEO, group=7), msg(3)]
    tail = [msg(90, PHOTO, group=555), msg(91, VIDEO, group=555), msg(92)]

    assert judge(pending, tail)[0] is Outcome.CONFIRMED


def test_an_album_that_arrived_as_singles_is_not_confirmed() -> None:
    pending = [msg(1, PHOTO, group=7), msg(2, PHOTO, group=7)]
    tail = [msg(90, PHOTO), msg(91, PHOTO)]

    assert judge(pending, tail)[0] is Outcome.AMBIGUOUS


def test_two_albums_are_told_apart() -> None:
    pending = [msg(1, PHOTO, group=7), msg(2, PHOTO, group=8)]
    merged = [msg(90, PHOTO, group=5), msg(91, PHOTO, group=5)]

    assert judge(pending, merged)[0] is Outcome.AMBIGUOUS


def test_a_different_count_is_ambiguous() -> None:
    assert judge([msg(1), msg(2)], [msg(90)])[0] is Outcome.AMBIGUOUS  # partial
    assert judge([msg(1)], [msg(90), msg(91)])[0] is Outcome.AMBIGUOUS  # someone else posted


def test_a_different_media_kind_is_ambiguous() -> None:
    assert judge([msg(1, PHOTO)], [msg(90, VIDEO)])[0] is Outcome.AMBIGUOUS


def test_a_placeholder_sent_as_text_confirms_against_its_own_real_kind() -> None:
    """T2, Phase 15b: a pending game/invoice sent as a text placeholder (``--placeholder``) landed
    in the destination as a plain text message, never its own media kind — without ``placeholders``
    telling ``judge`` about that, the shapes never matched and it was resent, duplicating it."""
    pending = [msg(1, MediaKind.GAME)]
    tail = [msg(90)]  # what actually landed: plain text, not a game

    assert judge(pending, tail)[0] is Outcome.AMBIGUOUS  # without the hint: still wrongly resent
    assert judge(pending, tail, placeholders={1}) == (Outcome.CONFIRMED, [90])


def test_a_placeholder_in_an_otherwise_ordinary_batch_is_still_confirmed() -> None:
    pending = [msg(1, PHOTO), msg(2, MediaKind.INVOICE), msg(3)]
    tail = [msg(90, PHOTO), msg(91), msg(92)]

    assert judge(pending, tail, placeholders={2}) == (Outcome.CONFIRMED, [90, 91, 92])


def test_a_placeholder_hint_for_the_wrong_id_does_not_paper_over_a_real_mismatch() -> None:
    pending = [msg(1, PHOTO)]
    tail = [msg(90, VIDEO)]

    assert judge(pending, tail, placeholders={1})[0] is Outcome.AMBIGUOUS
