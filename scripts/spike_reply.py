r"""Spike: does a reply posted into a forum topic land in that topic, and answer the right message?

WRITES to the destination (it posts a few short texts), so use a test channel and say so with
--yes-this-is-a-test-channel. Phase 19c (``reply: keep``) sends a message with the id of the
message it answers as Telethon's ``reply_to``; for a forum topic that is the same parameter that
names the topic (the topic's first message). This asks what Telegram does with it:

    .venv\Scripts\python.exe scripts\spike_reply.py @testforum --topic 5 `
        --yes-this-is-a-test-channel
    .venv\Scripts\python.exe scripts\spike_reply.py @testchannel `
        --yes-this-is-a-test-channel

In a forum (``--topic ID``, as ``tgmirror topics`` lists it) it posts, each time reading the new
message back and printing its topic and the message it answers:

  1. a text into the topic (``reply_to=topic``), the way a clone does;
  2. a text answering 1 with ``reply_to=<id of 1>`` only, the way ``reply: keep`` does it:
     does it land in the topic, or in General?
  3. an album answering 1 with the raw request naming both (``reply_to_msg_id`` and
     ``top_msg_id``): the way an album with a kept reply is posted. (Two photos of a few bytes,
     built in memory.)

Without ``--topic`` (a channel or a group) only the plain case is tried: a text, then a text
answering it. Paste the output back into the chat.
"""

import argparse
import asyncio
import io
from typing import Any

from _spike import chat_id, session
from telethon import types, utils
from telethon.tl.functions.messages import SendMultiMediaRequest, UploadMediaRequest

from tgmirror.core.gateway import ChannelInfo

# a 1x1 white GIF would be a document; a real photo needs a JPEG: the smallest valid one
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b"
    "0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ff"
    "c0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bff"
    "c400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a10823"
    "42b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a5354555657"
    "58595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aa"
    "b2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7"
    "f8f9faffda0008010100003f00fbfcffd9"
)


def where(message: Any) -> str:
    reply = message.reply_to
    answers = getattr(reply, "reply_to_msg_id", None)
    top = getattr(reply, "reply_to_top_id", None)
    topic = top or (answers if getattr(reply, "forum_topic", False) else None)
    return f"id {message.id}: answers {answers}, topic/top {topic}, text {message.message!r}"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dst")
    parser.add_argument("--topic", type=int, help="id of a topic of the forum (tgmirror topics)")
    parser.add_argument("--yes-this-is-a-test-channel", action="store_true", dest="test_channel")
    args = parser.parse_args()
    if not args.test_channel:
        raise SystemExit(
            "This posts messages into the destination: add --yes-this-is-a-test-channel."
        )

    async with session() as (client, gateway):
        dst: ChannelInfo = await gateway.get_channel(await chat_id(client, args.dst))
        if not dst.can_post:
            raise SystemExit(f"This account cannot post to {dst.title!r}.")
        peer = await client.get_input_entity(dst.id)

        text = "spike reply: 1 (the original)"
        first = await client.send_message(peer, text, reply_to=args.topic)
        print("1.", where(first))
        second = await client.send_message(peer, "spike reply: 2 (answers 1)", reply_to=first.id)
        print("2.", where(second))
        if args.topic is None:
            print("Not a forum: only the plain case was tried.")
            return
        if second.reply_to is not None and not getattr(second.reply_to, "forum_topic", False):
            print("   -> 2 is not marked as sitting in a topic: it may have gone to General.")

        uploads = []
        for i in (1, 2):
            file = await client.upload_file(io.BytesIO(TINY_JPEG), file_name=f"spike{i}.jpg")
            done = await client(UploadMediaRequest(peer, media=types.InputMediaUploadedPhoto(file)))
            uploads.append(done.photo)
        media = [
            types.InputSingleMedia(utils.get_input_media(photo), message=f"spike reply: album {i}")
            for i, photo in enumerate(uploads, start=1)
        ]
        reply = types.InputReplyToMessage(reply_to_msg_id=first.id, top_msg_id=args.topic)
        await client(SendMultiMediaRequest(peer, multi_media=media, reply_to=reply))
        newest = await client.get_messages(peer, limit=2)
        for message in reversed(newest):
            print("3.", where(message))


if __name__ == "__main__":
    asyncio.run(main())
