r"""Spike: does Telegram's total (``TelethonGateway.count``) agree with what reading finds?

Read-only. It answers the open question of ``count``: whether ``min_id``/``max_id`` and the media or
search narrowing are applied to the total the server returns, or only the whole chat is counted.

    .venv\Scripts\python.exe scripts\spike_count.py @somechannel
    .venv\Scripts\python.exe scripts\spike_count.py -1001234567890 --min-id 500 --search "#news"

It reads the chat several times (one page per 100 messages), so pick a channel of a few thousand
messages at most, or lower ``--max-read``. Paste the printed table back into the chat.
"""

import argparse
import asyncio
from collections.abc import Awaitable

from _spike import chat_id, session

from tgmirror.core.gateway import MediaKind, ServerFilter


async def agree(label: str, counted: Awaitable[int], read: Awaitable[int]) -> None:
    total, found = await counted, await read
    verdict = "same" if total == found else f"DIFFERENT ({total - found:+d})"
    print(f"{label:<34} count={total:<8} reading={found:<8} {verdict}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("chat", help="-100... id or @username of a chat you have joined")
    parser.add_argument("--min-id", type=int, default=0, help="only messages after this id")
    parser.add_argument("--search", default="#news", help="a word for the search rows")
    parser.add_argument("--max-read", type=int, default=20000, help="give up reading past this")
    args = parser.parse_args()

    async with session() as (client, gateway):
        chat = await chat_id(client, args.chat)
        newest = await gateway.last_message_id(chat)
        print(f"chat {chat}: newest message id {newest}")

        async def read(min_id: int = 0, **narrow: object) -> int:
            found = 0
            filters = ServerFilter(**narrow)  # type: ignore[arg-type]
            async for _ in gateway.iter_messages(chat, min_id=min_id, filters=filters):
                found += 1
                if found >= args.max_read:
                    raise SystemExit(f"more than {args.max_read} messages: pick a smaller chat")
            return found

        def count(min_id: int = 0, **narrow: object) -> Awaitable[int]:
            return gateway.count(chat, min_id=min_id, filters=ServerFilter(**narrow))  # type: ignore[arg-type]

        middle = args.min_id or newest // 2
        await agree("whole chat", count(), read())
        await agree(f"after id {middle}", count(middle), read(middle))
        await agree(f"up to id {middle}", count(max_id=middle), read(max_id=middle))
        await agree("photos", count(media=MediaKind.PHOTO), read(media=MediaKind.PHOTO))
        await agree("videos", count(media=MediaKind.VIDEO), read(media=MediaKind.VIDEO))
        await agree(
            f"photos after id {middle}",
            count(middle, media=MediaKind.PHOTO),
            read(middle, media=MediaKind.PHOTO),
        )
        await agree(f"search {args.search!r}", count(search=args.search), read(search=args.search))

    print(
        "\nEvery row should say `same`: the range is counted from where Telegram says the "
        "messages stand (`offset_id_offset`). A row with a range that says DIFFERENT means "
        "the count is only the upper bound `whole chat` there. Service messages (joins, "
        "pins) are counted by both."
    )


if __name__ == "__main__":
    asyncio.run(main())
