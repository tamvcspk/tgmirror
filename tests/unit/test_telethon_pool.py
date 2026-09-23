"""Telethon boundary: big files moved with many requests in flight (``TransferSettings``).

No network: a stub client answers ``GetFileRequest`` and ``SaveBigFilePartRequest`` from memory.
"""

import os
import struct
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telethon import errors, types
from telethon.errors.common import InvalidBufferError
from telethon.tl import custom
from telethon.tl.functions.messages import UploadMediaRequest
from telethon.tl.functions.upload import GetFileRequest, SaveBigFilePartRequest

from tests.unit.test_telethon_reupload import KEEP, NOW, VIDEO, Stub, message, photo, unit_of
from tgmirror.core import telethon_gateway
from tgmirror.core.errors import FloodWait, Transient
from tgmirror.core.gateway import TransferPhase
from tgmirror.core.pool import TRANSPORT_WAIT
from tgmirror.core.telethon_gateway import (
    DOWN_PART,
    UP_PART,
    TelethonGateway,
    TransferSettings,
)

DOWN, UP = TransferPhase.DOWNLOAD, TransferPhase.UPLOAD
MIB = 1024 * 1024
WARN_LOGGER = "tgmirror.core.telethon_gateway"


def big_video(size: int, *, dc_id: int = 1) -> types.MessageMediaDocument:
    doc = types.Document(
        id=1,
        access_hash=1,
        file_reference=b"",
        date=NOW,
        mime_type="video/mp4",
        size=size,
        dc_id=dc_id,
        attributes=[VIDEO],
    )
    return types.MessageMediaDocument(document=doc)


class PoolStub(Stub):
    """``Stub`` that also serves the raw requests of the pool from a blob."""

    def __init__(self, *messages: Any, blob: bytes = b"") -> None:
        super().__init__(*messages)
        self.blob = blob
        self.session = SimpleNamespace(dc_id=1)
        self._sender = "main"
        self.requests: list[tuple[Any, Any]] = []
        self.parts: dict[int, bytes] = {}
        self.script: list[Callable[[Any], Any]] = []  # each may raise or return an answer
        self.borrowed: list[int] = []
        self.returned: list[Any] = []

    async def _call(self, sender: Any, request: Any) -> Any:
        self.requests.append((sender, request))
        if self.script:
            answer = self.script.pop(0)(request)
            if answer is not None:
                return answer
        if isinstance(request, GetFileRequest):
            return SimpleNamespace(bytes=self.blob[request.offset : request.offset + request.limit])
        if isinstance(request, SaveBigFilePartRequest):
            self.parts[request.file_part] = request.bytes
            return True
        raise AssertionError(f"unexpected request {request!r}")

    async def _borrow_exported_sender(self, dc_id: int) -> str:
        self.borrowed.append(dc_id)
        return f"dc{dc_id}"

    async def _return_exported_sender(self, sender: Any) -> None:
        self.returned.append(sender)


async def no_wait(seconds: float) -> None:
    return None


POOL = TransferSettings(
    download_requests=8, upload_requests=8, upload_connections=2, min_bytes=1 * MIB
)


class Conn:
    """A connection the gateway makes for itself (``_new_sender``)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    async def disconnect(self) -> None:
        self.closed = True

    def __repr__(self) -> str:
        return self.name


def pooled(*messages: Any, blob: bytes = b"", settings: TransferSettings = POOL) -> Any:
    stub = PoolStub(*messages, blob=blob)
    gateway = TelethonGateway(stub, settings, sleep=no_wait)  # type: ignore[arg-type]
    made: list[Conn] = []

    async def new_sender() -> Any:
        made.append(Conn(f"conn{len(made) + 1}"))
        return made[-1]

    gateway._new_sender = new_sender  # type: ignore[method-assign]
    stub.made = made  # type: ignore[attr-defined]
    return gateway, stub


def clip_of(size: int, *, dc_id: int = 1) -> Any:
    return message(4, media=big_video(size, dc_id=dc_id))


def content(path: Path) -> bytes:
    return path.read_bytes()


def names(folder: Path, pattern: str = "*") -> list[str]:
    return sorted(p.name for p in folder.glob(pattern))


def collector() -> tuple[list[tuple[TransferPhase, int, int, int]], Any]:
    events: list[tuple[TransferPhase, int, int, int]] = []

    def on_transfer(phase: TransferPhase, msg_id: int, done: int, total: int) -> None:
        events.append((phase, msg_id, done, total))

    return events, on_transfer


# ---- downloading ------------------------------------------------------------------------------


async def test_a_big_document_comes_down_in_parts_and_is_put_together(tmp_path: Path) -> None:
    blob = os.urandom(3 * MIB + 1234)
    clip = clip_of(len(blob))
    gw, stub = pooled(clip, blob=blob)
    events, on_transfer = collector()

    prepared = await gw.prepare(1, unit_of(clip), tmp_path, on_transfer)

    (final,) = prepared.files
    assert final.name == "4.mp4" and content(final) == blob
    assert names(tmp_path, "*.part") == []
    offsets = sorted(r.offset for _, r in stub.requests)
    assert offsets == [0, DOWN_PART, 2 * DOWN_PART, 3 * DOWN_PART]
    assert all(r.limit == DOWN_PART for _, r in stub.requests)
    assert stub.downloads == []  # Telethon's own one-at-a-time download was not used
    assert events[0] == (DOWN, 4, 0, len(blob)) and events[-1] == (DOWN, 4, len(blob), len(blob))
    assert [e[2] for e in events] == sorted(e[2] for e in events)  # bytes only ever go up


async def test_the_pool_is_off_unless_asked_for(tmp_path: Path) -> None:
    clip = clip_of(3 * MIB)
    stub = PoolStub(clip)
    gw = TelethonGateway(stub)  # type: ignore[arg-type]  # the default: no pool

    await gw.prepare(1, unit_of(clip), tmp_path)

    assert stub.requests == [] and len(stub.downloads) == 1


async def test_a_small_document_keeps_telethons_own_download(tmp_path: Path) -> None:
    clip = clip_of(MIB // 2)
    gw, stub = pooled(clip, blob=os.urandom(MIB // 2))

    await gw.prepare(1, unit_of(clip), tmp_path)

    assert stub.requests == [] and len(stub.downloads) == 1


async def test_a_download_uses_a_connection_of_its_own_and_not_the_main_one(
    tmp_path: Path,
) -> None:
    blob = os.urandom(2 * MIB)
    clip = clip_of(len(blob))
    gw, stub = pooled(clip, blob=blob)

    await gw.prepare(1, unit_of(clip), tmp_path)

    assert {repr(sender) for sender, _ in stub.requests} == {"conn1"}
    await gw.prepare(1, unit_of(clip_of(len(blob))), tmp_path / "again")
    assert len(stub.made) == 1  # made once, kept for the next file


async def test_a_download_falls_back_to_the_main_connection_if_it_cannot_make_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blob = os.urandom(2 * MIB)
    clip = clip_of(len(blob))
    gw, stub = pooled(clip, blob=blob)

    async def refuses() -> Any:
        raise ConnectionError("no")

    gw._new_sender = refuses  # type: ignore[method-assign]

    with caplog.at_level("WARNING", logger=WARN_LOGGER):
        prepared = await gw.prepare(1, unit_of(clip), tmp_path)

    assert content(prepared.files[0]) == blob
    assert {sender for sender, _ in stub.requests} == {"main"}
    (record,) = caplog.records
    assert "download connection could not be made (ConnectionError: no)" in record.getMessage()


async def test_a_file_on_another_data_centre_uses_the_borrowed_connection(tmp_path: Path) -> None:
    blob = os.urandom(2 * MIB)
    clip = clip_of(len(blob), dc_id=4)
    gw, stub = pooled(clip, blob=blob)

    await gw.prepare(1, unit_of(clip), tmp_path)

    assert stub.borrowed == [4] and stub.returned == ["dc4"]
    assert {sender for sender, _ in stub.requests} == {"dc4"}


async def test_a_transport_429_ends_the_transfer_as_a_flood_wait_and_backs_the_budget_off(
    tmp_path: Path,
) -> None:
    blob = os.urandom(3 * MIB)
    clip = clip_of(len(blob))
    gw, stub = pooled(clip, blob=blob)

    def flood(request: Any) -> None:
        raise InvalidBufferError(struct.pack("<i", -429))

    stub.script = [flood]

    with pytest.raises(FloodWait) as caught:
        await gw.prepare(1, unit_of(clip), tmp_path)

    assert caught.value.transport and caught.value.seconds == TRANSPORT_WAIT
    assert gw._download_budget is not None and gw._download_budget.limit == 1  # 2 halved
    # download and upload no longer share one budget (docs/06-lo-trinh.md, 2026-09-23): pressure
    # on the download side must never touch the upload side's budget
    assert gw._upload_budget is not None and gw._upload_budget.limit == 2  # untouched


async def test_the_repeat_after_a_flood_takes_up_the_parts_it_already_has(tmp_path: Path) -> None:
    """The guard repeats ``prepare`` after the wait: a 200 MB video must not start again."""
    blob = os.urandom(4 * MIB + 99)
    clip = clip_of(len(blob))
    settings = TransferSettings(download_requests=1, upload_requests=1, min_bytes=MIB)
    gw, stub = pooled(clip, blob=blob, settings=settings)

    def flood_on_the_third_part(request: Any) -> None:
        raise InvalidBufferError(struct.pack("<i", -429))

    stub.script = [lambda r: None, lambda r: None, flood_on_the_third_part]  # one at a time

    with pytest.raises(FloodWait):
        await gw.prepare(1, unit_of(clip), tmp_path)
    assert names(tmp_path) == ["4.part"]  # kept, not deleted
    first = len(stub.requests)
    stub.requests.clear()
    events, on_transfer = collector()

    prepared = await gw.prepare(1, unit_of(clip), tmp_path, on_transfer)  # what FloodGuard does

    assert content(prepared.files[0]) == blob
    assert first == 3  # two parts, and the one that met the 429
    assert sorted(r.offset for _, r in stub.requests) == [
        2 * DOWN_PART,
        3 * DOWN_PART,
        4 * DOWN_PART,
    ]
    assert events[0][2] == 2 * DOWN_PART  # the progress starts where it left off
    assert events[-1] == (DOWN, 4, len(blob), len(blob))
    assert names(tmp_path, "*.part") == []


async def test_a_connection_that_keeps_failing_keeps_the_partial_file_for_the_next_try(
    tmp_path: Path,
) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))

    def closed(request: Any) -> None:
        raise ConnectionError("closed")

    stub.script = [closed] * 50

    with pytest.raises(Transient):
        await gw.prepare(1, unit_of(clip), tmp_path)

    assert names(tmp_path) == ["4.part"]


async def test_a_failure_that_is_not_a_flood_or_a_lost_connection_leaves_nothing(
    tmp_path: Path,
) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))

    def broken(request: Any) -> None:
        raise RuntimeError("a bug")

    stub.script = [broken]

    with pytest.raises(RuntimeError):
        await gw.prepare(1, unit_of(clip), tmp_path)

    assert names(tmp_path) == []


async def test_a_part_of_the_wrong_length_is_asked_for_again(tmp_path: Path) -> None:
    blob = os.urandom(2 * MIB)
    clip = clip_of(len(blob))
    gw, stub = pooled(clip, blob=blob)
    stub.script = [lambda request: SimpleNamespace(bytes=b"short")]

    prepared = await gw.prepare(1, unit_of(clip), tmp_path)

    assert content(prepared.files[0]) == blob


async def test_a_request_that_gets_no_answer_in_time_counts_as_pushback(tmp_path: Path) -> None:
    blob = os.urandom(2 * MIB)
    clip = clip_of(len(blob))
    settings = TransferSettings(
        download_requests=8, upload_requests=8, min_bytes=1 * MIB, request_timeout=0.05
    )
    gw, stub = pooled(clip, blob=blob, settings=settings)
    stalled = False
    original = stub._call

    async def stalls_once(sender: Any, request: Any) -> Any:
        nonlocal stalled
        if not stalled:
            stalled = True
            import asyncio

            await asyncio.sleep(5)
        return await original(sender, request)

    stub._call = stalls_once  # type: ignore[method-assign]

    prepared = await gw.prepare(1, unit_of(clip), tmp_path)

    assert content(prepared.files[0]) == blob


async def test_a_flood_wait_ends_the_download_and_keeps_the_partial_file(tmp_path: Path) -> None:
    blob = os.urandom(3 * MIB)
    clip = clip_of(len(blob))
    gw, stub = pooled(clip, blob=blob)

    def flood(request: Any) -> None:
        raise errors.FloodWaitError(None, capture=30)

    stub.script = [flood]

    with pytest.raises(FloodWait):
        await gw.prepare(1, unit_of(clip), tmp_path)

    assert names(tmp_path) == ["4.part"]


async def test_a_file_behind_a_cdn_falls_back_to_telethons_download(tmp_path: Path) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))
    redirect = types.upload.FileCdnRedirect(
        dc_id=2, file_token=b"", encryption_key=b"", encryption_iv=b"", file_hashes=[]
    )
    stub.script = [lambda request: redirect]

    prepared = await gw.prepare(1, unit_of(clip), tmp_path)

    assert len(stub.downloads) == 1 and prepared.files[0].name == "4.mp4"


# ---- uploading --------------------------------------------------------------------------------


@pytest.fixture
def small_big_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """The big-file API starts at 10 MB; the tests use a 1 MiB file for it."""
    monkeypatch.setattr(telethon_gateway, "BIG_FILE", 1 * MIB)


async def send_video(
    tmp_path: Path, size: int, settings: TransferSettings = POOL
) -> tuple[Any, PoolStub, bytes, list[Any]]:
    blob = os.urandom(size)
    clip = clip_of(size)
    gw, stub = pooled(clip, blob=blob, settings=settings)
    prepared = await gw.prepare(1, unit_of(clip), tmp_path)
    events, on_transfer = collector()
    await gw.send_prepared(2, prepared, KEEP, on_transfer)
    return gw, stub, blob, events


@pytest.mark.usefixtures("small_big_files")
async def test_a_big_video_goes_up_in_parts_and_is_posted_with_what_was_uploaded(
    tmp_path: Path,
) -> None:
    size = 2 * MIB + 777
    gw, stub, blob, events = await send_video(tmp_path, size)

    parts = -(-size // UP_PART)
    assert sorted(stub.parts) == list(range(parts))
    assert b"".join(stub.parts[i] for i in range(parts)) == blob
    saves = [r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)]
    assert {r.file_total_parts for r in saves} == {parts} and len({r.file_id for r in saves}) == 1
    ((_, handle, kw),) = stub.sent
    assert isinstance(handle, types.InputFileBig)
    assert (handle.id, handle.parts, handle.name) == (saves[0].file_id, parts, "4.mp4")
    # the message is posted exactly as for a path: what makes it a video is still passed
    assert kw["attributes"] == [VIDEO] and kw["mime_type"] == "video/mp4"
    assert kw["force_document"] is False and kw["nosound_video"] is True
    assert "progress_callback" not in kw  # the pool reports the bytes itself
    assert events[0] == (UP, 4, 0, size) and events[-1] == (UP, 4, size, size)


@pytest.mark.usefixtures("small_big_files")
async def test_the_parts_of_an_upload_are_spread_over_connections_of_their_own(
    tmp_path: Path,
) -> None:
    clip = clip_of(3 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(3 * MIB))
    prepared = await gw.prepare(1, unit_of(clip), tmp_path)  # takes conn1 for the download

    await gw.send_prepared(2, prepared, KEEP)

    used = {repr(s) for s, r in stub.requests if isinstance(r, SaveBigFilePartRequest)}
    assert used == {"conn2", "conn3"}  # the two upload connections, never the main one


@pytest.mark.usefixtures("small_big_files")
async def test_an_extra_connection_that_cannot_be_made_is_left_out(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))

    async def refuses() -> Any:
        raise ConnectionError("no")

    gw._new_sender = refuses  # type: ignore[method-assign]
    with caplog.at_level("WARNING", logger=WARN_LOGGER):
        prepared = await gw.prepare(1, unit_of(clip), tmp_path)
        await gw.send_prepared(2, prepared, KEEP)

    assert {s for s, r in stub.requests if isinstance(r, SaveBigFilePartRequest)} == {"main"}
    assert len(stub.sent) == 1
    # not silent: the download and the upload each say they fell back to the main connection
    assert len(caplog.records) == 2
    down, up = (r.getMessage() for r in caplog.records)
    assert "download connection could not be made" in down and "ConnectionError: no" in down
    assert "only 0 of 2" in up and "ConnectionError: no" in up and "main connection" in up


@pytest.mark.usefixtures("small_big_files")
async def test_fewer_upload_connections_than_asked_for_is_a_warning_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))
    real = gw._new_sender

    async def second_refuses() -> Any:
        if len(stub.made) == 2:  # the download's and the first upload connection exist
            raise ConnectionError("Server closed the connection")
        return await real()

    gw._new_sender = second_refuses  # type: ignore[method-assign]
    with caplog.at_level("WARNING", logger=WARN_LOGGER):
        prepared = await gw.prepare(1, unit_of(clip), tmp_path)
        await gw.send_prepared(2, prepared, KEEP)
        await gw.send_prepared(3, prepared, KEEP)  # the connections are kept: nothing new to say

    (record,) = caplog.records
    text = record.getMessage()
    assert "only 1 of 2" in text and "Server closed the connection" in text
    assert "main connection" not in text  # one of its own is still there
    used = {repr(s) for s, r in stub.requests if isinstance(r, SaveBigFilePartRequest)}
    assert used == {"conn2"}


@pytest.mark.usefixtures("small_big_files")
async def test_all_connections_made_is_not_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clip = clip_of(2 * MIB)
    gw, _ = pooled(clip, blob=os.urandom(2 * MIB))

    with caplog.at_level("WARNING", logger=WARN_LOGGER):
        await gw.send_prepared(2, await gw.prepare(1, unit_of(clip), tmp_path), KEEP)

    assert caplog.records == []


@pytest.mark.usefixtures("small_big_files")
async def test_closing_the_gateway_closes_every_connection_it_made(tmp_path: Path) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))
    await gw.send_prepared(2, await gw.prepare(1, unit_of(clip), tmp_path), KEEP)
    assert len(stub.made) == 3  # one for the download, two for the upload

    await gw.aclose()

    assert all(conn.closed for conn in stub.made)


@pytest.mark.usefixtures("small_big_files")
async def test_without_the_pool_a_video_goes_up_by_its_path_as_before(tmp_path: Path) -> None:
    clip = clip_of(2 * MIB)
    stub = PoolStub(clip, blob=os.urandom(2 * MIB))
    gw = TelethonGateway(stub)  # type: ignore[arg-type]
    prepared = await gw.prepare(1, unit_of(clip), tmp_path)

    await gw.send_prepared(2, prepared, KEEP)

    ((_, what, _),) = stub.sent
    assert isinstance(what, str) and what.endswith("4.mp4")


@pytest.mark.usefixtures("small_big_files")
async def test_an_albums_big_document_goes_through_the_pool_a_small_photo_does_not(
    tmp_path: Path,
) -> None:
    small = custom.Message(
        id=7, peer_id=types.PeerChannel(1), date=NOW, message="", media=photo(), grouped_id=9
    )
    big = custom.Message(
        id=8,
        peer_id=types.PeerChannel(1),
        date=NOW,
        message="",
        media=big_video(2 * MIB),
        grouped_id=9,
    )
    gw, stub = pooled(small, big, blob=os.urandom(2 * MIB))
    prepared = await gw.prepare(1, unit_of(small, big), tmp_path)

    await gw.send_prepared(2, prepared, KEEP)

    assert [r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)]  # the video
    assert stub.uploaded == [str(tmp_path / "7.jpg")]  # the photo: Telethon's own connection
    kinds = {type(c.media) for c in stub.calls if isinstance(c, UploadMediaRequest)}
    assert kinds == {types.InputMediaUploadedPhoto, types.InputMediaUploadedDocument}
    assert stub.sent == []  # posted through SendMultiMediaRequest, not Telethon's send_file


@pytest.mark.usefixtures("small_big_files")
async def test_a_flood_wait_while_uploading_is_a_flood_wait(tmp_path: Path) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))
    prepared = await gw.prepare(1, unit_of(clip), tmp_path)
    stub.requests.clear()

    def flood(request: Any) -> None:
        raise errors.FloodWaitError(None, capture=30)

    stub.script = [flood]

    with pytest.raises(FloodWait):
        await gw.send_prepared(2, prepared, KEEP)

    assert stub.sent == []  # nothing was posted


@pytest.mark.usefixtures("small_big_files")
async def test_the_bytes_read_for_upload_are_the_files_own(tmp_path: Path) -> None:
    size = UP_PART * 3
    gw, stub, blob, _ = await send_video(tmp_path, size)

    assert [stub.parts[i] for i in range(3)] == [
        blob[:UP_PART],
        blob[UP_PART : 2 * UP_PART],
        blob[2 * UP_PART :],
    ]


# ---- uploading ahead of the post ---------------------------------------------------


@pytest.mark.usefixtures("small_big_files")
async def test_the_bytes_can_go_up_before_the_message_is_posted(tmp_path: Path) -> None:
    size = 2 * MIB + 3
    blob = os.urandom(size)
    clip = clip_of(size)
    gw, stub = pooled(clip, blob=blob)
    prepared = await gw.prepare(1, unit_of(clip), tmp_path)
    events, on_transfer = collector()

    ready = await gw.upload_prepared(prepared, on_transfer)

    assert ready.uploaded and not prepared.uploaded
    assert stub.sent == []  # no message exists yet: nothing to reconcile if this is cut short
    parts = -(-size // UP_PART)
    assert b"".join(stub.parts[i] for i in range(parts)) == blob
    assert events[-1] == (UP, 4, size, size)
    saves = len([r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)])

    ids = await gw.send_prepared(2, ready, KEEP, on_transfer)

    assert ids == [101] and len(stub.sent) == 1
    handle = stub.sent[0][1]
    assert isinstance(handle, types.InputFileBig) and handle.parts == parts
    assert len([r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)]) == saves


@pytest.mark.usefixtures("small_big_files")
async def test_a_flood_wait_on_the_post_leaves_the_uploaded_file_ready_for_the_repeat(
    tmp_path: Path,
) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))
    ready = await gw.upload_prepared(await gw.prepare(1, unit_of(clip), tmp_path))
    saves = len([r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)])
    original = stub.send_file
    failed = False

    async def flood_once(peer: Any, file: Any, **kw: Any) -> Any:
        nonlocal failed
        if not failed:
            failed = True
            raise errors.FloodWaitError(None, capture=30)
        return await original(peer, file, **kw)

    stub.send_file = flood_once  # type: ignore[method-assign]

    with pytest.raises(FloodWait):
        await gw.send_prepared(2, ready, KEEP)
    ids = await gw.send_prepared(2, ready, KEEP)  # what FloodGuard does after the wait

    assert ids == [101]
    assert len([r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)]) == saves


@pytest.mark.usefixtures("small_big_files")
async def test_what_cannot_go_up_ahead_is_returned_as_it_is(tmp_path: Path) -> None:
    small = message(4, media=big_video(MIB // 2))
    gw, stub = pooled(small, blob=os.urandom(MIB // 2))
    prepared = await gw.prepare(1, unit_of(small), tmp_path)

    assert await gw.upload_prepared(prepared) is prepared  # small: Telethon's post does it all

    members = [
        custom.Message(
            id=i,
            peer_id=types.PeerChannel(1),
            date=NOW,
            message="",
            media=big_video(2 * MIB),
            grouped_id=9,
        )
        for i in (7, 8)
    ]
    gw2, _ = pooled(*members, blob=os.urandom(2 * MIB))
    album = await gw2.prepare(1, unit_of(*members), tmp_path)
    assert await gw2.upload_prepared(album) is album  # an album is uploaded by its post

    off = PoolStub(clip_of(2 * MIB), blob=os.urandom(2 * MIB))
    plain = TelethonGateway(off)  # type: ignore[arg-type]
    unpooled = await plain.prepare(1, unit_of(clip_of(2 * MIB)), tmp_path)
    assert await plain.upload_prepared(unpooled) is unpooled  # the pool is off
    assert stub.requests == []  # and nothing above touched the pool


@pytest.mark.usefixtures("small_big_files")
async def test_uploading_twice_does_not_send_the_bytes_twice(tmp_path: Path) -> None:
    clip = clip_of(2 * MIB)
    gw, stub = pooled(clip, blob=os.urandom(2 * MIB))
    ready = await gw.upload_prepared(await gw.prepare(1, unit_of(clip), tmp_path))
    saves = len([r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)])

    again = await gw.upload_prepared(ready)

    assert again is ready
    assert len([r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)]) == saves
