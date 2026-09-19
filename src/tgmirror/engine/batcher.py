"""Group units into batches: the messages sent in one ``copy_messages`` call."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from tgmirror.core.gateway import Unit


@dataclass(frozen=True, slots=True)
class Batch:
    units: tuple[Unit, ...]

    @property
    def ids(self) -> list[int]:
        return [i for unit in self.units for i in unit.ids]

    @property
    def size(self) -> int:
        return sum(len(unit.messages) for unit in self.units)

    @property
    def last_id(self) -> int:
        return self.units[-1].messages[-1].id


async def batches(stream: AsyncIterator[Unit], batch_size: int) -> AsyncIterator[Batch]:
    """Fill batches up to ``batch_size`` messages, never splitting a unit.

    An album larger than ``batch_size`` still goes out whole, alone: at most 10 messages, so it
    stays below Telegram's 100 ids per call (docs/01-kien-truc.md, "Unit và Batch").
    """
    current: list[Unit] = []
    count = 0
    async for unit in stream:
        if current and count + len(unit.messages) > batch_size:
            yield Batch(tuple(current))
            current, count = [], 0
        current.append(unit)
        count += len(unit.messages)
    if current:
        yield Batch(tuple(current))
