r"""Spike: can a message's media be sent again *by reference*, without downloading and uploading?

WRITES to the destination, so use a test channel and say so with --yes-this-is-a-test-channel.
If the media came from a source that restricts saving content, decision D3 applies exactly as for
`tgmirror clone`: the script refuses unless you also pass --yes-i-administer-this-channel, and by
doing so you answer for it.

    .venv\Scripts\python.exe scripts\spike_reference.py @src @testdst 12 13 14 `
        --yes-this-is-a-test-channel
    .venv\Scripts\python.exe scripts\spike_reference.py @src @testdst 20 21 22 --album `
        --yes-this-is-a-test-channel

For each message it sends ``send_file(dst, message.media, caption=...)`` (Telegram is given the id
of the file it already stores, so nothing is transferred) and then reads the new message back to
say what it became: photo, video (with duration), voice, plain file... A video that comes back as a
plain file, an error, or a slow send are all answers. Paste the output back into the chat.
"""

import argparse
import asyncio
from typing import Any

from _spike import chat_id, session

from tgmirror.core.gateway import ChannelInfo


def kind_of(message: Any) -> str:
    for name in ("photo", "video", "video_note", "gif", "voice", "audio", "sticker"):
        if getattr(message, name, None):
            return name
    return "document" if message.document else "no media"


def describe(message: Any) -> str:
    file = message.file
    extra = ""
    if file is not None:
        extra = f", {file.size or '?'} bytes, {file.mime_type}, duration {file.duration}"
    return f"{kind_of(message)}{extra}"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src")
    parser.add_argument("dst")
    parser.add_argument("ids", type=int, nargs="*", help="message ids of the source")
    parser.add_argument(
        "--find-albums",
        action="store_true",
        help="only list the albums among the newest 500 messages of the source",
    )
    parser.add_argument("--album", action="store_true", help="send them all as one album")
    parser.add_argument("--caption", default="spike: sent by reference")
    parser.add_argument("--yes-this-is-a-test-channel", action="store_true", dest="test_channel")
    parser.add_argument("--yes-i-administer-this-channel", action="store_true", dest="admin")
    args = parser.parse_args()
    if not args.ids and not args.find_albums:
        raise SystemExit("Give message ids (or --find-albums to look for an album).")
    if not args.test_channel and not args.find_albums:
        raise SystemExit(
            "This posts messages into the destination: add --yes-this-is-a-test-channel."
        )

    async with session() as (client, gateway):
        src: ChannelInfo = await gateway.get_channel(await chat_id(client, args.src))
        if args.find_albums:
            albums: dict[int, list[int]] = {}
            async for m in client.iter_messages(src.id, limit=500):
                if m.grouped_id:
                    albums.setdefault(m.grouped_id, []).append(m.id)
            for members in albums.values():
                print(" ".join(str(i) for i in sorted(members)))
            print(f"{len(albums)} albums; pass the ids of one of them with --album")
            return
        dst: ChannelInfo = await gateway.get_channel(await chat_id(client, args.dst))
        if not dst.can_post:
            raise SystemExit(f"This account cannot post to {dst.title!r}.")
        if src.noforwards and not args.admin:
            raise SystemExit(
                f"{src.title!r} restricts saving content. Only with "
                "--yes-i-administer-this-channel (your own statement, D3) may a spike use it."
            )
        found = [m for m in await client.get_messages(src.id, ids=args.ids) if m is not None]
        print(f"source {src.title!r} (noforwards={src.noforwards}) -> {dst.title!r}")
        print(f"read {len(found)} of {len(args.ids)} messages")
        media = [m for m in found if m.media is not None]
        for m in found:
            print(f"  source {m.id}: {describe(m)}")

        async def attempt(label: str, what: Any) -> None:
            try:
                sent = await client.send_file(dst.id, what, caption=args.caption)
            except Exception as exc:  # noqa: BLE001 - the error's name is the answer
                print(f"{label}: FAILED with {type(exc).__name__}: {exc}")
                return
            for item in sent if isinstance(sent, list) else [sent]:
                back = (await client.get_messages(dst.id, ids=[item.id]))[0]
                print(
                    f"{label}: new message {back.id} -> {describe(back)}, group {back.grouped_id}"
                )

        if args.album and len(media) < 2:
            raise SystemExit(
                "An album needs at least two messages with media, and these ids are one "
                "single message each. Find one with --find-albums."
            )
        if args.album:
            await attempt("album by reference", [m.media for m in media])
        else:
            for m in media:
                await attempt(f"message {m.id} by reference", m.media)

    print(
        "\nWorked = the new message has the same kind (and duration) as the source and no download "
        "happened. Then a run that only changes captions could skip the transfer altogether."
    )


if __name__ == "__main__":
    asyncio.run(main())
