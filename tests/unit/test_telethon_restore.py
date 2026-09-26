"""Telethon boundary for phase 11b (restore): sending a unit read from a backup directory, no live
Telethon message involved. No network: reuses ``test_telethon_reupload.py``'s stub client.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from telethon import types
from telethon.tl.functions.messages import SendMultiMediaRequest, UploadMediaRequest
from telethon.tl.functions.upload import SaveBigFilePartRequest

from tests.unit.test_telethon_pool import POOL, pooled, small_big_files  # noqa: F401 - fixture
from tests.unit.test_telethon_reupload import Stub
from tgmirror.core.gateway import (
    CaptionMode,
    CaptionPolicy,
    ExportedMedia,
    ExportedMessage,
    FromBackup,
    MediaKind,
    Prepared,
    SrcMessage,
    Unit,
)
from tgmirror.core.telethon_gateway import UP_PART, TelethonGateway

NOW = datetime(2026, 1, 1, tzinfo=UTC)
KEEP = CaptionPolicy()
SRC_ID = -1001001  # a realistic marked channel id: "-100" + internal id "1001"
MIB = 1024 * 1024


def gateway() -> tuple[TelethonGateway, Stub]:
    stub = Stub()
    return TelethonGateway(stub), stub  # type: ignore[arg-type]


def write(path: Path, data: bytes = b"data") -> Path:
    path.write_bytes(data)
    return path


def unit_of(*messages: ExportedMessage) -> Unit:
    """A ``Unit`` shaped like what ``BackupReader`` would hand the runner (only ids/grouped_id
    matter to ``Unit`` itself; the real content travels in ``FromBackup``)."""
    return Unit(tuple(SrcMessage(m.id, NOW, grouped_id=m.grouped_id) for m in messages))


def prepared_of(media_dir: Path, *messages: ExportedMessage) -> Prepared:
    return Prepared(unit_of(*messages), files=(), handle=FromBackup(messages, media_dir, SRC_ID))


# ---- plain text and files ----------------------------------------------------------------------


async def test_a_text_message_sends_as_plain_text(tmp_path: Path) -> None:
    gw, stub = gateway()
    msg = ExportedMessage(1, NOW, None, None, None, "hello", None)
    ids = await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)

    assert ids == [101]  # the stub's first allocated id
    kind, text, kw = stub.sent[0]
    assert kind == "message" and text == "hello"


async def test_html_formatting_round_trips_through_parse(tmp_path: Path) -> None:
    gw, stub = gateway()
    msg = ExportedMessage(1, NOW, None, None, None, "<b>bold</b> plain", None)
    await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)

    _, text, kw = stub.sent[0]
    assert text == "bold plain"
    entities = kw["formatting_entities"]
    assert any(isinstance(e, types.MessageEntityBold) for e in entities)


async def test_a_photo_uploads_via_send_file(tmp_path: Path) -> None:
    gw, stub = gateway()
    write(tmp_path / "1.jpg")
    msg = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "caption",
        None,
        media=ExportedMedia(kind=MediaKind.PHOTO, filename="1.jpg", mime="image/jpeg"),
    )
    await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)

    kind, file, kw = stub.sent[0]
    assert kind == "file" and file == str(tmp_path / "1.jpg")
    assert kw["caption"] == "caption"
    assert kw["mime_type"] == "image/jpeg"
    assert "voice_note" not in kw and "video_note" not in kw and "force_document" not in kw


@pytest.mark.usefixtures("small_big_files")
async def test_a_big_file_goes_up_through_the_pool_not_a_single_connection(tmp_path: Path) -> None:
    """The gap found on a real restore (speed capped at one connection): a big document must go
    through ``_upload_parallel``/``RequestBudget`` exactly like a live reupload's big files do,
    not through Telethon's own single-connection ``send_file(path)``."""
    size = 2 * MIB + 777
    blob = os.urandom(size)
    (tmp_path / "1.mp4").write_bytes(blob)
    msg = ExportedMessage(
        4,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.VIDEO, filename="1.mp4", mime="video/mp4"),
    )
    gw, stub = pooled(blob=blob, settings=POOL)

    await gw.send_prepared(2, prepared_of(tmp_path, msg), KEEP)

    parts = -(-size // UP_PART)
    saves = [r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)]
    assert {r.file_total_parts for r in saves} == {parts}
    assert b"".join(stub.parts[i] for i in range(parts)) == blob
    kind, handle, kw = stub.sent[0]
    assert kind == "file"
    assert isinstance(handle, types.InputFileBig)
    assert "progress_callback" not in kw  # the pool reports the bytes itself


@pytest.mark.usefixtures("small_big_files")
async def test_an_albums_big_member_also_goes_through_the_pool(tmp_path: Path) -> None:
    """The album path shares ``_upload_album_member`` with the single-file path (and with a live
    reupload's own album), so a big video inside a restored album is pooled too, not just a
    lone message — this is what "lam luon phan album" asked for."""
    size = 2 * MIB + 777
    blob = os.urandom(size)
    (tmp_path / "1.mp4").write_bytes(blob)
    write(tmp_path / "2.jpg")
    video = ExportedMessage(
        1,
        NOW,
        100,
        None,
        None,
        "caption",
        None,
        media=ExportedMedia(kind=MediaKind.VIDEO, filename="1.mp4", mime="video/mp4"),
    )
    photo_msg = ExportedMessage(
        2,
        NOW,
        100,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.PHOTO, filename="2.jpg"),
    )
    gw, stub = pooled(blob=blob, settings=POOL)

    ids = await gw.send_prepared(2, prepared_of(tmp_path, video, photo_msg), KEEP)

    assert len(ids) == 2
    parts = -(-size // UP_PART)
    saves = [r for _, r in stub.requests if isinstance(r, SaveBigFilePartRequest)]
    assert {r.file_total_parts for r in saves} == {parts}
    assert b"".join(stub.parts[i] for i in range(parts)) == blob
    uploads = [c for c in stub.calls if isinstance(c, UploadMediaRequest)]
    posts = [c for c in stub.calls if isinstance(c, SendMultiMediaRequest)]
    assert len(uploads) == 2  # the pooled video's InputFileBig, and the plain photo
    assert len(posts) == 1
    assert isinstance(uploads[0].media, types.InputMediaUploadedDocument)
    assert isinstance(uploads[0].media.file, types.InputFileBig)
    assert isinstance(uploads[1].media, types.InputMediaUploadedPhoto)


async def test_a_small_file_still_uses_telethons_own_uploader(tmp_path: Path) -> None:
    """Below the pool's threshold, restore behaves like it always did: no regression for the
    common case of ordinary-sized media."""
    gw, stub = gateway()
    write(tmp_path / "1.mp4")
    msg = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.VIDEO, filename="1.mp4", mime="video/mp4"),
    )
    await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)

    kind, file, kw = stub.sent[0]
    assert kind == "file" and file == str(tmp_path / "1.mp4")


async def test_a_voice_note_sets_the_voice_note_flag(tmp_path: Path) -> None:
    gw, stub = gateway()
    write(tmp_path / "1.oga")
    msg = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.VOICE, filename="1.oga", mime="audio/ogg"),
    )
    await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)
    assert stub.sent[0][2]["voice_note"] is True


async def test_a_video_note_sets_the_video_note_flag(tmp_path: Path) -> None:
    gw, stub = gateway()
    write(tmp_path / "1.mp4")
    msg = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.VIDEO_NOTE, filename="1.mp4", mime="video/mp4"),
    )
    await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)
    assert stub.sent[0][2]["video_note"] is True


async def test_a_document_forces_the_document_flag(tmp_path: Path) -> None:
    gw, stub = gateway()
    write(tmp_path / "1.bin")
    msg = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.DOCUMENT, filename="1.bin", mime="application/x"),
    )
    await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)
    assert stub.sent[0][2]["force_document"] is True


async def test_caption_strip_links_uses_the_original_channel_id(tmp_path: Path) -> None:
    gw, stub = gateway()
    write(tmp_path / "1.jpg")
    msg = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        '<a href="https://t.me/c/1001/5">link</a> rest',
        None,
        media=ExportedMedia(kind=MediaKind.PHOTO, filename="1.jpg"),
    )
    policy = CaptionPolicy(CaptionMode.STRIP_LINKS)
    await gw.send_prepared(10, prepared_of(tmp_path, msg), policy)

    kw = stub.sent[0][2]
    # a hyperlink keeps its visible words but loses the link itself (unlike a bare url/mention)
    assert kw["caption"] == "link rest"
    entities = kw["formatting_entities"] or []
    assert not any(isinstance(e, types.MessageEntityTextUrl) for e in entities)


# ---- self-contained media -----------------------------------------------------------------------


async def test_a_plain_poll_is_rebuilt_fresh(tmp_path: Path) -> None:
    gw, stub = gateway()
    msg = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.POLL, poll_question="colour?", poll_options=("r", "b")),
    )
    await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)

    kind, text, kw = stub.sent[0]
    assert kind == "message"
    file = kw["file"]
    assert isinstance(file, types.InputMediaPoll)
    assert file.poll.question.text == "colour?"
    assert [a.text.text for a in file.poll.answers] == ["r", "b"]
    assert file.correct_answers is None


async def test_a_quiz_with_a_known_correct_answer_carries_it(tmp_path: Path) -> None:
    gw, stub = gateway()
    msg = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(
            kind=MediaKind.POLL,
            poll_question="2+2?",
            poll_options=("3", "4"),
            poll_quiz=True,
            poll_correct_option=1,
        ),
    )
    await gw.send_prepared(10, prepared_of(tmp_path, msg), KEEP)

    file = stub.sent[0][2]["file"]
    assert file.poll.quiz is True
    assert file.correct_answers == [bytes([1])]


async def test_geo_and_venue_and_contact(tmp_path: Path) -> None:
    gw, stub = gateway()
    geo = ExportedMessage(
        1,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.GEO, geo_lat=1.0, geo_lon=2.0),
    )
    venue = ExportedMessage(
        2,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.GEO, geo_lat=1.0, geo_lon=2.0, venue_title="Cafe"),
    )
    contact = ExportedMessage(
        3,
        NOW,
        None,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.CONTACT, contact_phone="123", contact_first_name="A"),
    )

    await gw.send_prepared(10, prepared_of(tmp_path, geo), KEEP)
    await gw.send_prepared(10, prepared_of(tmp_path, venue), KEEP)
    await gw.send_prepared(10, prepared_of(tmp_path, contact), KEEP)

    geo_media = stub.sent[0][2]["file"]
    venue_media = stub.sent[1][2]["file"]
    contact_media = stub.sent[2][2]["file"]
    assert isinstance(geo_media, types.InputMediaGeoPoint)
    assert isinstance(venue_media, types.InputMediaVenue)
    assert venue_media.title == "Cafe"
    assert isinstance(contact_media, types.InputMediaContact)
    assert contact_media.phone_number == "123"


# ---- albums --------------------------------------------------------------------------------------


async def test_an_album_sends_every_file_in_one_call(tmp_path: Path) -> None:
    gw, stub = gateway()
    write(tmp_path / "1.jpg")
    write(tmp_path / "2.jpg")
    first = ExportedMessage(
        1,
        NOW,
        100,
        None,
        None,
        "caption",
        None,
        media=ExportedMedia(kind=MediaKind.PHOTO, filename="1.jpg"),
    )
    second = ExportedMessage(
        2,
        NOW,
        100,
        None,
        None,
        "",
        None,
        media=ExportedMedia(kind=MediaKind.PHOTO, filename="2.jpg"),
    )
    ids = await gw.send_prepared(10, prepared_of(tmp_path, first, second), KEEP)

    assert len(ids) == 2
    assert stub.sent == []  # not sent through send_file: the shared album core is used instead
    uploads = [c for c in stub.calls if isinstance(c, UploadMediaRequest)]
    posts = [c for c in stub.calls if isinstance(c, SendMultiMediaRequest)]
    assert len(uploads) == 2  # one UploadMediaRequest per member, like a live reupload's album
    assert len(posts) == 1  # then one SendMultiMediaRequest for the whole album
    assert [m.message for m in posts[0].multi_media] == ["caption", ""]


# ---- upload_prepared is a no-op for a restore ---------------------------------------------------


async def test_upload_prepared_is_a_noop_for_a_from_backup_handle(tmp_path: Path) -> None:
    gw, _ = gateway()
    msg = ExportedMessage(1, NOW, None, None, None, "hi", None)
    prepared = prepared_of(tmp_path, msg)
    got = await gw.upload_prepared(prepared)
    assert got is prepared
