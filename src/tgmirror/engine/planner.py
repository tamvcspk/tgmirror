"""Turn the source's message stream into ``Unit``s (docs/01-kien-truc.md, "Vòng lặp runner").

Ascending order (decision D4). Service messages are never cloned. Consecutive messages sharing a
``grouped_id`` form one album and are handed out together: an album is never split (hard rule 4).
Filters arrive in phase 3 and will be applied here, on whole units.
"""

from collections.abc import AsyncIterator
from contextlib import aclosing

from tgmirror.core.gateway import NO_FILTER, ServerFilter, SrcMessage, TelegramGateway, Unit


async def units(
    gateway: TelegramGateway, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
) -> AsyncIterator[Unit]:
    """Messages of ``src`` with ``id > min_id`` as units, oldest first."""
    album: list[SrcMessage] = []
    async with aclosing(gateway.iter_messages(src, min_id=min_id, filters=filters)) as stream:
        async for message in stream:
            if message.is_service:
                continue
            if album and message.grouped_id != album[0].grouped_id:
                yield Unit(tuple(album))  # the album is complete: something else follows it
                album = []
            if message.grouped_id is None:
                yield Unit((message,))
            else:
                album.append(message)
    if album:
        yield Unit(tuple(album))
