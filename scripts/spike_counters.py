r"""Spike: what does ``messages.getSearchCounters`` return, and does it agree with reading?

Read-only. It answers spike 1 of docs/07-inspect-transform.md section 9, the question behind
``inspect --quick`` (Phase 18b): one request that gives the number of photos, videos, documents,
links, ... of a chat. It sends that one request, reads the whole chat page by page, counts the same
things on what it read, and prints both side by side.

    .venv\Scripts\python.exe scripts\spike_counters.py @somechannel
    .venv\Scripts\python.exe scripts\spike_counters.py @somechannel --explain
    .venv\Scripts\python.exe scripts\spike_counters.py -1001234567890 --topic 5 --max-read 5000

What to look at in the table:

- which filters the server answers at all (``(no answer)``: the reply left that filter out);
- ``counters`` against ``reading``: the same number, or only close (``inexact`` says Telegram itself
  is not sure) — and whether it is off by a constant (service messages, one side of an album);
- ``--explain``: for each row that differs, the messages the server's own filter returns that the
  reading does not count under that name — what they are, so the difference has a cause;
- ``--topic N`` sends ``top_msg_id=N`` (1 = General) and prints a second table for that topic
  next to the whole chat's, each compared with the same part of the reading.

Pick a channel of a few thousand messages, or lower ``--max-read``; reading is the slow side
(about 11 s per 1,000 messages). Paste the printed tables back into the chat.
"""

import argparse
import asyncio
import inspect
import time
from collections import Counter
from collections.abc import Callable

from _spike import chat_id, session
from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl import types
from telethon.tl.functions.messages import GetSearchCountersRequest, SearchRequest

from tgmirror.core.gateway import MediaKind, SrcMessage

# label, Telegram's filter, and how the same thing is recognised on a message we read
CHECKS: list[tuple[str, type, Callable[[SrcMessage], bool]]] = [
    ("photos", types.InputMessagesFilterPhotos, lambda m: m.media is MediaKind.PHOTO),
    ("video", types.InputMessagesFilterVideo, lambda m: m.media is MediaKind.VIDEO),
    (
        "photo+video",
        types.InputMessagesFilterPhotoVideo,
        lambda m: m.media in (MediaKind.PHOTO, MediaKind.VIDEO),
    ),
    ("document", types.InputMessagesFilterDocument, lambda m: m.media is MediaKind.DOCUMENT),
    ("music", types.InputMessagesFilterMusic, lambda m: m.media is MediaKind.AUDIO),
    ("voice", types.InputMessagesFilterVoice, lambda m: m.media is MediaKind.VOICE),
    ("round video", types.InputMessagesFilterRoundVideo, lambda m: m.media is MediaKind.VIDEO_NOTE),
    ("gif", types.InputMessagesFilterGif, lambda m: m.media is MediaKind.GIF),
    ("url", types.InputMessagesFilterUrl, lambda m: bool(m.link_domains)),
    ("geo", types.InputMessagesFilterGeo, lambda m: m.media is MediaKind.GEO),
    ("contacts", types.InputMessagesFilterContacts, lambda m: m.media is MediaKind.CONTACT),
    ("poll", types.InputMessagesFilterPoll, lambda m: m.media is MediaKind.POLL),
    ("pinned", types.InputMessagesFilterPinned, lambda m: m.pinned),
]
Answers = dict[type, tuple[str, str]]  # filter class -> (count as text, "yes" when inexact)


async def counters(client: TelegramClient, peer: object, top: int | None) -> Answers:
    """One request for every filter; if the server refuses the batch, one filter at a time."""
    answers: Answers = {}
    try:
        got = await client(
            GetSearchCountersRequest(peer=peer, filters=[c() for _, c, _ in CHECKS], top_msg_id=top)  # type: ignore[arg-type]
        )
        for counter in got:
            answers[type(counter.filter)] = (str(counter.count), "yes" if counter.inexact else "")
    except RPCError as exc:
        print(f"the batch was refused ({type(exc).__name__}: {exc}); one filter at a time:")
        for _label, kind, _ in CHECKS:
            try:
                (counter,) = await client(
                    GetSearchCountersRequest(peer=peer, filters=[kind()], top_msg_id=top)  # type: ignore[arg-type]
                )
                answers[kind] = (str(counter.count), "yes" if counter.inexact else "")
            except RPCError as one:
                answers[kind] = (f"refused: {type(one).__name__}", "")
    return answers


def table(title: str, answers: Answers, tally: Counter[str]) -> list[str]:
    """The side-by-side rows; the labels whose numbers differ are returned for ``--explain``."""
    print(f"\n{title}")
    print(f"{'filter':<14}{'counters':<12}{'inexact':<9}{'reading':<9}verdict")
    differing: list[str] = []
    for label, kind, _ in CHECKS:
        said, inexact = answers.get(kind, ("(no answer)", ""))
        found = tally[label]
        verdict = ""
        if said.lstrip("-").isdigit():
            verdict = "same" if int(said) == found else f"DIFFERENT ({int(said) - found:+d})"
            if int(said) != found:
                differing.append(label)
        print(f"{label:<14}{said:<12}{inexact:<9}{found:<9}{verdict}")
    return differing


def describe(message: SrcMessage | None) -> str:
    if message is None:
        return "not among the messages read"
    text = (message.text or "").replace("\n", " ")[:50]
    extras = f" links={','.join(message.link_domains)}" if message.link_domains else ""
    where = f" topic_id={message.topic_id} reply_to={message.reply_to_id}"
    return f"read as {message.media.value}{extras}{where} {text!r}"


async def server_ids(client: TelegramClient, peer: object, kind: type, top: int | None) -> set[int]:
    """Ids the server's own filter returns, asked with ``messages.search`` directly: Telethon's
    ``iter_messages(reply_to=...)`` becomes ``getReplies``, which has no filter."""
    found: set[int] = set()
    offset = 0
    while True:
        got = await client(
            SearchRequest(
                peer=peer,  # type: ignore[arg-type]
                q="",
                filter=kind(),
                min_date=None,
                max_date=None,
                offset_id=offset,
                add_offset=0,
                limit=100,
                max_id=0,
                min_id=0,
                hash=0,
                top_msg_id=top,
            )
        )
        ids = [m.id for m in got.messages]  # type: ignore[union-attr]
        if not ids:
            return found
        found.update(ids)
        offset = min(ids)


async def explain(
    client: TelegramClient,
    peer: object,
    labels: list[str],
    read: dict[int, SrcMessage],
    top: int | None,
    wanted: Callable[[SrcMessage], bool],
) -> None:
    """For each differing filter, the messages only the server counts and the ones only the
    reading counts (``wanted`` says which of the messages read belong to the part compared)."""
    by_label = {label: (kind, matches) for label, kind, matches in CHECKS}
    for label in labels:
        kind, matches = by_label[label]
        theirs = await server_ids(client, peer, kind, top)
        ours = {i for i, m in read.items() if not m.is_service and wanted(m) and matches(m)}
        print(f"\n--explain {label}: server {len(theirs)}, reading {len(ours)}")
        for title, ids in (
            ("only the server counts", sorted(theirs - ours, reverse=True)),
            ("only the reading counts", sorted(ours - theirs, reverse=True)),
        ):
            if not ids:
                continue
            print(f"  {title} ({len(ids)}):")
            for message_id in ids[:15]:
                print(f"    id {message_id}: {describe(read.get(message_id))}")
            if len(ids) > 15:
                print(f"    ... and {len(ids) - 15} more")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("chat", help="-100... id or @username of a chat you have joined")
    parser.add_argument("--topic", type=int, default=None, help="forum topic id (1 = General)")
    parser.add_argument("--max-read", type=int, default=20000, help="give up reading past this")
    parser.add_argument(
        "--explain", action="store_true", help="list the messages behind each DIFFERENT row"
    )
    parser.add_argument(
        "--raw",
        type=int,
        nargs="+",
        metavar="ID",
        help="only print what Telegram sends for these message ids, then stop",
    )
    args = parser.parse_args()

    print("request parameters:", ", ".join(inspect.signature(GetSearchCountersRequest).parameters))

    async with session() as (client, gateway):
        chat = await chat_id(client, args.chat)
        peer = await client.get_input_entity(chat)

        if args.raw:
            for message in await client.get_messages(peer, ids=args.raw):
                if message is None:
                    print("\n(no such message)")
                    continue
                print(f"\nid {message.id}  {message.date:%Y-%m-%d %H:%M}  {type(message).__name__}")
                print("  reply_to:", message.reply_to)
                media = message.media
                print("  media:", type(media).__name__ if media else None)
                document = getattr(media, "document", None)
                if document is not None:
                    names = [type(a).__name__ for a in document.attributes]
                    print(f"  document: {document.mime_type} {document.size} B, attributes {names}")
                if getattr(media, "geo", None) is not None or hasattr(media, "address"):
                    print("  geo media:", repr(media)[:200])
                print("  fwd_from:", message.fwd_from is not None, " out:", message.out)
            return

        # ---- the requests: the whole chat, and the topic when one is named ------------------
        started = time.monotonic()
        whole_answers = await counters(client, peer, None)
        topic_answers = None if args.topic is None else await counters(client, peer, args.topic)
        request_seconds = time.monotonic() - started

        # ---- the reading --------------------------------------------------------------------
        whole: Counter[str] = Counter()
        in_topic: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        read: dict[int, SrcMessage] = {}
        seen = services = 0
        started = time.monotonic()
        async for message in gateway.iter_messages(chat):
            seen += 1
            if seen > args.max_read:
                raise SystemExit(f"more than {args.max_read} messages: pick a smaller chat")
            read[message.id] = message
            if message.is_service:
                services += 1
                continue
            kinds[message.media.value] += 1
            here = args.topic is not None and (
                message.topic_id == args.topic or (args.topic == 1 and message.topic_id is None)
            )
            for label, _, matches in CHECKS:
                if matches(message):
                    whole[label] += 1
                    if here:
                        in_topic[label] += 1
        read_seconds = time.monotonic() - started

        print(f"\nchat {chat}")
        differing = table("whole chat (no top_msg_id)", whole_answers, whole)
        topic_differing: list[str] = []
        if topic_answers is not None:
            topic_differing = table(
                f"topic {args.topic} (top_msg_id={args.topic})", topic_answers, in_topic
            )
        print(
            f"\nread {seen} messages ({services} service, left out above) in {read_seconds:.0f} s;"
        )
        print(f"the counters requests took {request_seconds:.1f} s for {len(CHECKS)} filters.")
        print("kinds read:", ", ".join(f"{k} {n}" for k, n in kinds.most_common()))

        if args.explain:
            await explain(client, peer, differing, read, None, lambda _m: True)
            if topic_differing:
                await explain(
                    client,
                    peer,
                    topic_differing,
                    read,
                    args.topic,
                    lambda m: m.topic_id == args.topic or (args.topic == 1 and m.topic_id is None),
                )

    print(
        "\nA row that says DIFFERENT is the answer: note the sign and whether `inexact` is set. "
        "`photo+video` against `photos` + `video` shows whether the combined filter is one "
        "count or two."
    )


if __name__ == "__main__":
    asyncio.run(main())
