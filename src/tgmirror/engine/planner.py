"""Turn the source's message stream into ``Unit``s (docs/01-kien-truc.md, "Vòng lặp runner").

Ascending order (decision D4). Service messages are never cloned. Consecutive messages sharing a
``grouped_id`` form one album and are handed out together: an album is never split (hard rule 4).

With a ``matcher`` (phase 3) every unit is judged as a whole; a unit that does not pass comes out as
a ``Skip`` so the runner can count it and move the cursor past it. When the server narrowed the
stream by content (``complete_albums``) it dropped the album members that do not match on their
own, so each album is completed with one unfiltered read of the ids around it before it is judged.
"""

from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass

from tgmirror.core.gateway import (
    ALBUM_MARGIN,
    NO_FILTER,
    MessageReader,
    ServerFilter,
    SrcMessage,
    Unit,
)
from tgmirror.filters.matcher import Matcher


@dataclass(frozen=True, slots=True)
class Skip:
    """A unit the filter dropped: ``count`` messages, the last one has id ``last_id``."""

    count: int
    last_id: int


async def units(
    gateway: MessageReader,
    src: int,
    *,
    min_id: int = 0,
    filters: ServerFilter = NO_FILTER,
    matcher: Matcher | None = None,
    complete_albums: bool = False,
) -> AsyncIterator[Unit | Skip]:
    """Messages of ``src`` with ``id > min_id`` as units, oldest first.

    Without a ``matcher`` every unit is yielded. With one, units that do not match are replaced by
    a ``Skip`` (never for service messages, which are silently ignored as before).
    """

    async def judged(members: list[SrcMessage]) -> Unit | Skip:
        if complete_albums and members[0].grouped_id is not None:
            members = await _whole_album(gateway, src, members)
        unit = Unit(tuple(members))
        if matcher is None or matcher.matches(unit):
            return unit
        return Skip(len(unit.messages), unit.messages[-1].id)

    album: list[SrcMessage] = []
    async with aclosing(gateway.iter_messages(src, min_id=min_id, filters=filters)) as stream:
        async for message in stream:
            if message.is_service:
                continue
            if album and message.grouped_id != album[0].grouped_id:
                yield await judged(album)  # the album is complete: something else follows it
                album = []
            if message.grouped_id is None:
                yield await judged([message])
            else:
                album.append(message)
    if album:
        yield await judged(album)


async def _whole_album(
    gateway: MessageReader, src: int, seen: list[SrcMessage]
) -> list[SrcMessage]:
    """``seen`` plus the members of the same album the server left out (one unfiltered read)."""
    group = seen[0].grouped_id
    window = ServerFilter(max_id=seen[-1].id + ALBUM_MARGIN)
    members = {m.id: m for m in seen}
    async with aclosing(
        gateway.iter_messages(src, min_id=max(seen[0].id - 1 - ALBUM_MARGIN, 0), filters=window)
    ) as stream:
        async for message in stream:
            if message.grouped_id == group and not message.is_service:
                members[message.id] = message
    return [members[i] for i in sorted(members)]
