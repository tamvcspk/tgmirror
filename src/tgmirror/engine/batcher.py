"""Group units into batches: the messages sent in one ``copy_messages`` call.

A batch also carries the messages the filter dropped just before it (``skipped``), so the runner
can count them and move the cursor past them in the same commit as the batch itself.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

from tgmirror.core.gateway import Unit
from tgmirror.engine.planner import Skip

# A batch that waits for more units flushes after this many filtered-out messages, so a long
# stretch with (almost) no matches still saves progress and lets pause/stop through.
FLUSH_AFTER = 500


@dataclass(frozen=True, slots=True)
class Batch:
    """``units`` may be empty: a progress-only batch of filtered-out messages."""

    units: tuple[Unit, ...]
    skipped: int = 0  # messages the filter dropped since the previous batch
    # Highest source id this batch settles besides its own units: filtered-out messages, and units
    # dropped because they were already ``done``.
    upto: int = 0

    @property
    def ids(self) -> list[int]:
        return [i for unit in self.units for i in unit.ids]

    @property
    def size(self) -> int:
        return sum(len(unit.messages) for unit in self.units)

    @property
    def last_id(self) -> int:
        """The cursor once the batch is settled: every message up to here is dealt with."""
        last = self.units[-1].messages[-1].id if self.units else 0
        return max(last, self.upto)


async def batches(
    stream: AsyncIterator[Unit | Skip], batch_size: int, *, flush_after: int = FLUSH_AFTER
) -> AsyncIterator[Batch]:
    """Fill batches up to ``batch_size`` messages, never splitting a unit.

    An album larger than ``batch_size`` still goes out whole, alone: at most 10 messages, so it
    stays below Telegram's 100 ids per call (docs/01-kien-truc.md, "Unit và Batch").

    Skips are credited to the batch that is emitted while they are pending: they all precede the
    unit that did not fit, so the batch's cursor may safely pass them.
    """
    current: list[Unit] = []
    count = skipped = upto = 0

    def emit() -> Batch:
        return Batch(tuple(current), skipped, upto)

    async for item in stream:
        if isinstance(item, Skip):
            skipped, upto = skipped + item.count, item.last_id
            if skipped >= flush_after:
                yield emit()
                current, count, skipped, upto = [], 0, 0, 0
            continue
        if current and count + len(item.messages) > batch_size:
            yield emit()
            current, count, skipped, upto = [], 0, 0, 0
        current.append(item)
        count += len(item.messages)
    if current or skipped:
        yield emit()
