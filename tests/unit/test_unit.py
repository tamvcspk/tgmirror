from datetime import UTC, datetime

import pytest

from tgmirror.core.gateway import SrcMessage, Unit

NOW = datetime(2024, 1, 1, tzinfo=UTC)


def msg(id: int, grouped_id: int | None = None) -> SrcMessage:
    return SrcMessage(id=id, date=NOW, grouped_id=grouped_id)


def test_single_message_unit() -> None:
    unit = Unit((msg(5),))

    assert unit.ids == [5]
    assert unit.grouped_id is None
    assert not unit.is_album


def test_album_unit() -> None:
    unit = Unit((msg(5, 9), msg(6, 9), msg(7, 9)))

    assert unit.ids == [5, 6, 7]
    assert unit.grouped_id == 9
    assert unit.is_album


def test_single_message_can_carry_grouped_id() -> None:
    # An album whose other members were filtered out by the server is still one Unit.
    assert Unit((msg(5, 9),)).is_album


@pytest.mark.parametrize(
    "messages",
    [
        (),
        (msg(1), msg(2)),  # two ungrouped messages are not an album
        (msg(1, 9), msg(2, 8)),  # different albums
        (msg(1, 9), msg(2)),  # album mixed with a loose message
        (msg(2, 9), msg(1, 9)),  # not ascending
        (msg(1, 9), msg(1, 9)),  # duplicate id
    ],
)
def test_invalid_units_are_rejected(messages: tuple[SrcMessage, ...]) -> None:
    with pytest.raises(ValueError):
        Unit(messages)
