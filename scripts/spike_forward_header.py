r"""Spike: does a forward that keeps its header (``drop_author`` off) keep albums and topics?

WRITES to the destination, so use a test channel and say so with --yes-this-is-a-test-channel.
Phase 19c2 (``forward_header: keep``) is the ordinary forward, without ``drop_author``. The two
questions of docs/07 section 9, item 5: does an album forwarded this way arrive as one album, and
does a forward into a forum topic (``top_msg_id``) land in that topic, as it does with
``drop_author=True``?

    .venv\Scripts\python.exe scripts\spike_forward_header.py @src @testdst `
        --yes-this-is-a-test-channel
    .venv\Scripts\python.exe scripts\spike_forward_header.py @src @testforum --topic 5 `
        --yes-this-is-a-test-channel

With no ids it looks through the newest 500 messages of the source, takes the newest album it
finds (all its members) and the newest plain message, and forwards both. Give ids (12 13 14) to
forward exactly those instead. It forwards in one request, reads the new messages back and
prints, for each, whether it carries a "Forwarded from" header, its group and its topic. A source
that restricts saving content cannot be forwarded at all: the script says so rather than trying.
Paste the output back into the chat.
"""

import argparse
import asyncio
from typing import Any

from _spike import chat_id, session
from telethon.tl.functions.messages import ForwardMessagesRequest

from tgmirror.core.gateway import ChannelInfo


def describe(message: Any) -> str:
    fwd = message.fwd_from
    header = "no header" if fwd is None else f"header (from {fwd.from_name or fwd.from_id})"
    reply = message.reply_to
    top = getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None)
    return f"id {message.id}: {header}, group {message.grouped_id}, topic/top {top}"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src")
    parser.add_argument("dst")
    parser.add_argument(
        "ids", type=int, nargs="*", help="message ids of the source (default: find an album)"
    )
    parser.add_argument("--topic", type=int, help="id of a topic of the forum destination")
    parser.add_argument("--yes-this-is-a-test-channel", action="store_true", dest="test_channel")
    args = parser.parse_args()
    if not args.test_channel:
        raise SystemExit(
            "This posts messages into the destination: add --yes-this-is-a-test-channel."
        )

    async with session() as (client, gateway):
        src: ChannelInfo = await gateway.get_channel(await chat_id(client, args.src))
        dst: ChannelInfo = await gateway.get_channel(await chat_id(client, args.dst))
        if not dst.can_post:
            raise SystemExit(f"This account cannot post to {dst.title!r}.")
        if src.noforwards:
            raise SystemExit(f"{src.title!r} restricts saving content: it cannot be forwarded.")
        ids: list[int] = list(args.ids)
        if not ids:
            albums: dict[int, list[int]] = {}
            plain: int | None = None
            async for m in client.iter_messages(src.id, limit=500):
                if m.grouped_id:
                    albums.setdefault(m.grouped_id, []).append(m.id)
                elif plain is None and not m.action:
                    plain = m.id
            if not albums:
                raise SystemExit("No album among the newest 500 messages: give ids.")
            album = sorted(next(iter(albums.values())))  # iter_messages goes newest first
            ids = sorted(album + ([plain] if plain is not None else []))
            print(f"forwarding album {album} and plain message {plain}")
        from_peer = await client.get_input_entity(src.id)
        to_peer = await client.get_input_entity(dst.id)
        tail = await client.get_messages(to_peer, limit=1)
        before = tail[0].id if tail else 0

        request = ForwardMessagesRequest(
            from_peer=from_peer, id=ids, to_peer=to_peer, top_msg_id=args.topic
        )
        await client(request)  # no drop_author: a plain forward
        sent = [m async for m in client.iter_messages(to_peer, min_id=before, reverse=True)]
        for message in sent:
            print(describe(message))
        groups = {m.grouped_id for m in sent if m.grouped_id}
        print(f"{len(sent)} messages, {len(groups)} group(s)")


if __name__ == "__main__":
    asyncio.run(main())
