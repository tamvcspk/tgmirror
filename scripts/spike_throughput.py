r"""Spike: how fast can one file move, and what makes it faster?

Answers docs/06-lo-trinh.md's questions about the request pool: is one connection with several
requests in flight enough, or does the server throttle per connection so that more connections are
needed? Does Telegram answer FloodWait when many parts are asked at once?

    .venv\Scripts\python.exe scripts\spike_throughput.py download <src> 42
    .venv\Scripts\python.exe scripts\spike_throughput.py download <src> 42 --mb 64 `
        --inflight 1,2,4,8 --connections 1,2,4
    .venv\Scripts\python.exe scripts\spike_throughput.py upload --mb 64 `
        --inflight 1,4,8 --connections 1,2,4
    .venv\Scripts\python.exe scripts\spike_throughput.py both <src> 42 --up-connections 2,4,6
    .venv\Scripts\python.exe scripts\spike_throughput.py files download <src> `
        11,22,33,44,55,66,77,88 --files 2,4,8 --max-requests 4,8,16
    .venv\Scripts\python.exe scripts\spike_throughput.py files upload `
        --files 2,4,8 --max-requests 4,8,16

``<src>`` above is a placeholder, not literal text: put your own ``-100...`` id or ``@username``
there. Do **not** type a bare ``@word`` in PowerShell (it splats a variable of that name; an
undefined one silently vanishes, shifting every argument after it) and do not wrap a plain
comma-list in backticks (a backtick is PowerShell's escape character, not a quote — one right
before a space glues that argument onto the next, as ``` `11451` --files``` did once). Commas need
no quoting at all in PowerShell; only actual spaces do (with `"..."`, never backticks).

``download`` reads the first ``--mb`` megabytes of the file of message 42 (nothing is written to
disk) with ``inflight`` requests at once on ``connections`` connections; the first row is Telethon's
own single-connection download as a baseline. ``upload`` sends random bytes as a *big file* that no
message ever uses (Telegram discards it), so nothing appears in any chat. Both measure only the
transfer. They use private Telethon calls (borrowed senders), as the real pool would have to.

``files`` is the "several files at once" shape (down a few, then up a few, never both directions
together — see ``both`` for that): ``--files`` of them move at once, each on its own
``--connections`` connections, but every request across every one of them shares a single budget of
``--max-requests`` in flight (like ``core/pool.py``'s ``RequestBudget``, minus the AIMD growth and
shrink). ``download`` needs one message id per file (a comma list, ``--files`` picks the largest N
of them); ``upload`` invents its files, same as ``upload`` above. Answers: does moving several files
at once, sharing one budget, reach the per-file speed seen alone, and at what budget does it 429?

Paste the table back into the chat, with your line speed if you know it.
"""

import argparse
import asyncio
import contextlib
import math
import os
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from _spike import chat_id, mb, session
from telethon import TelegramClient, errors, utils
from telethon.network import MTProtoSender
from telethon.tl.functions.upload import GetFileRequest, SaveBigFilePartRequest

DOWN_PART = 1024 * 1024  # a download request: at most 1 MiB, never across a 1 MiB boundary
UP_PART = 512 * 1024  # an upload part: at most 512 KiB
TIMEOUT = 90  # seconds after which a setting is given up


async def new_sender(client: TelegramClient, dc_id: int) -> MTProtoSender:
    """A connection of its own to ``dc_id`` (the way FastTelethon makes them)."""
    if dc_id != client.session.dc_id:
        return await client._create_exported_sender(dc_id)  # noqa: SLF001
    dc = await client._get_dc(dc_id)  # noqa: SLF001
    sender = MTProtoSender(client.session.auth_key, loggers=client._log)  # noqa: SLF001
    await sender.connect(
        client._connection(  # noqa: SLF001
            dc.ip_address,
            dc.port,
            dc.id,
            loggers=client._log,  # noqa: SLF001
            proxy=client._proxy,  # noqa: SLF001
            local_addr=client._local_addr,  # noqa: SLF001
        )
    )
    return sender


async def pool(
    client: TelegramClient,
    dc_id: int,
    parts: int,
    inflight: int,
    connections: int,
    request: Callable[[int], Any],
    ticks: list[float] | None = None,
) -> tuple[float, str]:
    """Run ``parts`` requests (``request(index)``), ``inflight`` at a time spread over
    ``connections`` connections. Returns the seconds it took and a note (a FloodWait, a
    connection the server closed, a run that took over ``TIMEOUT`` seconds): a setting that
    breaks is reported and the next one goes on, it never hangs the script."""
    senders: list[MTProtoSender] = []
    todo = iter(range(parts))
    note = ""

    async def worker(n: int) -> None:
        nonlocal note
        sender = senders[n % len(senders)]
        for index in todo:  # a shared iterator: whoever is free takes the next part
            try:
                await client._call(sender, request(index))  # noqa: SLF001
                if ticks is not None:
                    ticks.append(time.monotonic())  # one part is through
            except errors.FloodWaitError as exc:
                note = f"FLOOD_WAIT {exc.seconds}s"
                return

    started = time.monotonic()
    try:
        senders.extend([await new_sender(client, dc_id) for _ in range(connections)])
        started = time.monotonic()  # connecting is not part of the transfer
        await asyncio.wait_for(
            asyncio.gather(*(worker(n) for n in range(inflight))), timeout=TIMEOUT
        )
    except TimeoutError:
        note = f"TIMEOUT after {TIMEOUT}s"
    except Exception as exc:  # noqa: BLE001 - what broke is the answer
        note = f"ERROR {type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started
    for sender in senders:
        with contextlib.suppress(Exception):
            await sender.disconnect()
    if note.startswith(("ERROR", "TIMEOUT")):
        elapsed = 0.0  # no speed to speak of
    return elapsed, note


async def pool_shared(
    client: TelegramClient,
    dc_id: int,
    parts: int,
    connections: int,
    budget: asyncio.Semaphore,
    request: Callable[[int], Any],
) -> tuple[float, str]:
    """Like ``pool``, but one file among several moving at once: its own ``connections`` (one
    request in flight per connection, the shape that came out clean earlier), except every request
    also waits for a slot in ``budget`` — shared with every other file running alongside it, the
    way ``core/pool.py``'s ``RequestBudget`` is shared by every transfer of the process (here fixed,
    no AIMD)."""
    senders: list[MTProtoSender] = []
    todo = iter(range(parts))
    note = ""

    async def worker(n: int) -> None:
        nonlocal note
        sender = senders[n % len(senders)]
        for index in todo:
            async with budget:
                try:
                    await client._call(sender, request(index))  # noqa: SLF001
                except errors.FloodWaitError as exc:
                    note = f"FLOOD_WAIT {exc.seconds}s"
                    return

    started = time.monotonic()
    try:
        senders.extend([await new_sender(client, dc_id) for _ in range(connections)])
        started = time.monotonic()
        await asyncio.wait_for(
            asyncio.gather(*(worker(n) for n in range(connections))), timeout=TIMEOUT
        )
    except TimeoutError:
        note = f"TIMEOUT after {TIMEOUT}s"
    except Exception as exc:  # noqa: BLE001 - what broke is the answer
        note = f"ERROR {type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started
    for sender in senders:
        with contextlib.suppress(Exception):
            await sender.disconnect()
    if note.startswith(("ERROR", "TIMEOUT")):
        elapsed = 0.0
    return elapsed, note


def row(label: str, size: int, runs: list[tuple[float, str]]) -> None:
    """One line per setting: the speed of each repeat, and the median."""
    speeds = sorted(size / seconds / (1024 * 1024) if seconds else 0.0 for seconds, _ in runs)
    notes = " ".join(sorted({note for _, note in runs if note}))
    each = " ".join(f"{speed:5.1f}" for speed in speeds)
    print(
        f"{label:<34} {mb(size):>9}  median {speeds[len(speeds) // 2]:6.2f} MB/s  [{each}] {notes}"
    )


def counts(text: str) -> list[int]:
    return [int(part) for part in text.split(",")]


async def download(args: argparse.Namespace) -> None:
    async with session() as (client, _):
        peer = await chat_id(client, args.src)
        message = (await client.get_messages(peer, ids=[args.message]))[0]
        if message is None or message.media is None or message.file is None:
            raise SystemExit("That message has no file.")
        if (message.file.size or 0) < 8 * 1024 * 1024:
            raise SystemExit(
                f"That file is only {mb(message.file.size or 0)}: too small to say anything about "
                "speed. Pick a message with a video or a big file (8 MB or more)."
            )
        size = min(message.file.size or 0, args.mb * 1024 * 1024)
        dc_id, location = utils.get_input_location(message.media)
        print(
            f"file {message.file.name or message.id}: {mb(message.file.size or 0)}, on DC {dc_id}"
        )
        print(f"(this account's home DC is {client.session.dc_id}; measuring the first {mb(size)})")

        seen = 0
        started = time.monotonic()
        stream = client.iter_download(message.media, request_size=DOWN_PART)
        async for chunk in stream:
            seen += len(chunk)
            if seen >= size:
                break
        row("telethon iter_download (baseline)", seen, [(time.monotonic() - started, "")])

        parts = math.ceil(size / DOWN_PART)
        for connections in args.connections:
            for inflight in args.inflight:
                if inflight < connections:
                    continue
                runs = [
                    await pool(
                        client,
                        dc_id,
                        parts,
                        inflight,
                        connections,
                        lambda i: GetFileRequest(location, offset=i * DOWN_PART, limit=DOWN_PART),
                    )
                    for _ in range(args.repeat)
                ]
                row(f"{inflight} in flight on {connections} conn", parts * DOWN_PART, runs)


def timeline(ticks: list[float], began: float, window: int) -> None:
    """Speed per ``window`` seconds from when each part went through."""
    if not ticks:
        return
    first = min(ticks)
    buckets: dict[int, int] = {}
    for tick in ticks:
        buckets[int((tick - first) // window)] = buckets.get(int((tick - first) // window), 0) + 1
    line = " ".join(
        f"{n * UP_PART / window / (1024 * 1024):.1f}" for _, n in sorted(buckets.items())
    )
    print(f"  MB/s per {window}s: {line}")


async def upload(args: argparse.Namespace) -> None:
    async with session() as (client, _):
        size = args.mb * 1024 * 1024
        parts = math.ceil(size / UP_PART)
        blob = os.urandom(UP_PART)  # the same bytes every time: the server does not look
        print(f"uploading {mb(size)} of random bytes as a big file nobody will use ({parts} parts)")
        home = client.session.dc_id
        for connections in args.connections:
            for inflight in args.inflight:
                if inflight < connections:
                    continue
                runs = []
                for _ in range(args.repeat):
                    file_id = random.getrandbits(62)  # every repeat is a file of its own
                    ticks: list[float] = []
                    began = time.monotonic()
                    runs.append(
                        await pool(
                            client,
                            home,
                            parts,
                            inflight,
                            connections,
                            lambda i, file_id=file_id: SaveBigFilePartRequest(
                                file_id, i, parts, blob
                            ),
                            ticks,
                        )
                    )
                    if args.timeline:
                        timeline(ticks, began, args.timeline)
                row(f"{inflight} in flight on {connections} conn", parts * UP_PART, runs)


async def both(args: argparse.Namespace) -> None:
    """A download and an upload at the same time, as the real run does them. ``--up-connections``
    takes a list (e.g. ``2,4,6``) to find the highest that does not 429 next to a download; each
    setting's own connections are fresh, so an earlier 429 cannot taint a later one."""
    async with session() as (client, _):
        peer = await chat_id(client, args.src)
        message = (await client.get_messages(peer, ids=[args.message]))[0]
        if message is None or message.media is None or (message.file.size or 0) < 8 * 1024 * 1024:
            raise SystemExit("Pick a message with a file of 8 MB or more.")
        dc_id, location = utils.get_input_location(message.media)
        size = min(message.file.size, args.mb * 1024 * 1024)
        down_parts, up_parts = math.ceil(size / DOWN_PART), math.ceil(size / UP_PART)
        blob = os.urandom(UP_PART)
        for up_connections in args.up_connections:
            print(
                f"download {args.down_inflight} in flight on 1 conn, upload {args.up_inflight} in "
                f"flight on {up_connections} conn, {mb(size)} each way"
            )
            for label, together in (("alone", False), ("together", True)):
                down_runs: list[tuple[float, str]] = []
                up_runs: list[tuple[float, str]] = []
                for _ in range(args.repeat):
                    file_id = random.getrandbits(62)
                    down = pool(
                        client,
                        dc_id,
                        down_parts,
                        args.down_inflight,
                        1,
                        lambda i: GetFileRequest(location, offset=i * DOWN_PART, limit=DOWN_PART),
                    )
                    up = pool(
                        client,
                        client.session.dc_id,
                        up_parts,
                        args.up_inflight,
                        up_connections,
                        lambda i, file_id=file_id: SaveBigFilePartRequest(
                            file_id, i, up_parts, blob
                        ),
                    )
                    if together:
                        d, u = await asyncio.gather(down, up)
                        down_runs.append(d)
                        up_runs.append(u)
                    else:
                        down_runs.append(await down)
                        up_runs.append(await up)
                    if down_runs[-1][1] or up_runs[-1][1]:
                        # broke on this repeat (FloodWait/429/TIMEOUT): further repeats of this
                        # setting would likely just repeat it and burn time for nothing
                        break
                row(f"download, {label}", down_parts * DOWN_PART, down_runs)
                row(f"upload, {label}", up_parts * UP_PART, up_runs)


async def many_files(args: argparse.Namespace) -> None:
    """``--files`` files moving at once, one direction only, sharing one ``--max-requests`` budget
    (``pool_shared``); scans that against ``--files`` to find a combination that keeps speed without
    429s. Each file gets its own ``--connections`` connections, one request in flight per connection
    (the shape that ran clean alone in the ``upload`` spike)."""
    async with session() as (client, _):
        part_size = DOWN_PART if args.direction == "download" else UP_PART
        if args.direction == "download":
            if not args.src or not args.ids:
                raise SystemExit("files download needs src and a comma list of message ids")
            peer = await chat_id(client, args.src)
            messages = await client.get_messages(peer, ids=args.ids)
            for msg_id, message in zip(args.ids, messages, strict=True):
                size = message.file.size if message and message.media else 0
                if not size or size < 8 * 1024 * 1024:
                    raise SystemExit(f"message {msg_id}: no file of 8 MB or more")
            locations = [utils.get_input_location(m.media) for m in messages]
            dc_ids = [loc[0] for loc in locations]
            size_each = min(min(m.file.size for m in messages), args.mb * 1024 * 1024)
            parts = math.ceil(size_each / part_size)
            available = len(messages)

            def make_request(i: int) -> Callable[[int], Any]:
                location = locations[i][1]
                return lambda idx: GetFileRequest(location, offset=idx * DOWN_PART, limit=DOWN_PART)
        else:
            size_each = args.mb * 1024 * 1024
            parts = math.ceil(size_each / part_size)
            blob = os.urandom(UP_PART)
            home = client.session.dc_id
            available = max(args.files)
            dc_ids = [home] * available
            file_ids = [random.getrandbits(62) for _ in range(available)]

            def make_request(i: int) -> Callable[[int], Any]:
                return lambda idx: SaveBigFilePartRequest(file_ids[i], idx, parts, blob)

        print(f"{args.direction}: {mb(size_each)} per file, {args.connections} connections each")
        for files in args.files:
            if files > available:
                what = "message ids" if args.direction == "download" else "file slots"
                print(f"  (skip --files {files}: only {available} {what} given)")
                continue
            for max_requests in args.max_requests:
                budget = asyncio.Semaphore(max_requests)
                runs: list[tuple[float, str]] = []
                for _ in range(args.repeat):
                    if args.direction == "upload":
                        file_ids[:files] = [random.getrandbits(62) for _ in range(files)]
                    started = time.monotonic()
                    results = await asyncio.gather(
                        *(
                            pool_shared(
                                client, dc_ids[i], parts, args.connections, budget, make_request(i)
                            )
                            for i in range(files)
                        )
                    )
                    elapsed = time.monotonic() - started
                    note = " ".join(sorted({n for _, n in results if n}))
                    runs.append((0.0 if note else elapsed, note))
                    if note:
                        # broke on this repeat: further repeats would likely just repeat it
                        break
                row(f"{files} files, budget {max_requests}", files * parts * part_size, runs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="what", required=True)
    for name in ("download", "upload"):
        one = sub.add_parser(name)
        if name == "download":
            one.add_argument("src", help="-100... id or @username")
            one.add_argument("message", type=int, help="id of a message that has a file")
        one.add_argument("--mb", type=int, default=64)
        one.add_argument(
            "--timeline",
            type=int,
            default=0,
            metavar="SECONDS",
            help="also print the speed in windows of this many seconds (a long transfer"
            " that starts fast and slows down shows up here)",
        )
        one.add_argument("--repeat", type=int, default=3, help="runs per setting (median)")
        one.add_argument("--inflight", type=counts, default=[1, 2, 4, 8], help="e.g. 1,2,4,8")
        one.add_argument("--connections", type=counts, default=[1, 2, 4], help="e.g. 1,2,4")
    both_parser = sub.add_parser("both", help="a download and an upload at the same time")
    both_parser.add_argument("src")
    both_parser.add_argument("message", type=int, help="a message with a file of 8 MB+")
    both_parser.add_argument("--mb", type=int, default=64)
    both_parser.add_argument("--repeat", type=int, default=3)
    both_parser.add_argument("--down-inflight", type=int, default=8)
    both_parser.add_argument("--up-inflight", type=int, default=8)
    both_parser.add_argument("--up-connections", type=counts, default=[2], help="e.g. 2,4,6")
    files_parser = sub.add_parser("files", help="several files at once, sharing one request budget")
    files_parser.add_argument("direction", choices=["download", "upload"])
    files_parser.add_argument("src", nargs="?", help="download only: -100... id or @username")
    files_parser.add_argument(
        "ids", nargs="?", type=counts, help="download only: message ids, comma-separated"
    )
    files_parser.add_argument("--mb", type=int, default=64, help="per file")
    files_parser.add_argument("--repeat", type=int, default=3)
    files_parser.add_argument("--connections", type=int, default=2, help="each file's own")
    files_parser.add_argument("--files", type=counts, default=[2, 4, 8], help="e.g. 2,4,8")
    files_parser.add_argument(
        "--max-requests", type=counts, default=[4, 8, 16], help="the shared budget, e.g. 4,8,16"
    )
    args = parser.parse_args()
    modes: dict[str, Callable[[argparse.Namespace], Awaitable[None]]] = {
        "download": download,
        "upload": upload,
        "both": both,
        "files": many_files,
    }
    asyncio.run(modes[args.what](args))


if __name__ == "__main__":
    main()
