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
