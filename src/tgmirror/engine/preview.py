"""A sample of what a filter would clone, before the job is saved (wizard step 5).

It reads the first ``sample`` messages of the range the filter selects (id and date bounds only:
narrowing by content would make almost everything match) and judges them with the real matcher.
Reads are throttled by the gateway like any other (docs/05-chong-flood.md).
"""

from contextlib import aclosing
from dataclasses import dataclass

from tgmirror.core.gateway import TelegramGateway
from tgmirror.engine import planner
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import plan_read

SAMPLE = 100
EXAMPLES = 3
EXAMPLE_WIDTH = 60


@dataclass(frozen=True, slots=True)
class Preview:
    scanned: int  # messages read (albums count every member)
    matched: int  # messages of the units that pass the filter
    examples: tuple[str, ...]  # start of the text of the first matching units


def _shorten(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= EXAMPLE_WIDTH else line[: EXAMPLE_WIDTH - 1] + "…"


async def sample(
    gateway: TelegramGateway,
    src: int,
    spec: FilterSpec,
    *,
    limit: int = SAMPLE,
    pushdown: bool = True,
) -> Preview:
    plan = plan_read(spec, 0, pushdown=pushdown, content=False)
    read = planner.units(
        gateway, src, min_id=plan.min_id, filters=plan.server, matcher=Matcher(spec)
    )
    scanned = matched = 0
    examples: list[str] = []
    async with aclosing(read) as stream:
        async for item in stream:
            if isinstance(item, planner.Skip):
                scanned += item.count
            else:
                scanned += len(item.messages)
                matched += len(item.messages)
                text = next((m.text for m in item.messages if m.text.strip()), "")
                if text and len(examples) < EXAMPLES:
                    examples.append(_shorten(text))
            if scanned >= limit:
                break
    return Preview(scanned, matched, tuple(examples))
