"""Telethon boundary for strategy B: fetching a unit, sending it again, rewriting captions.

No network: a stub client records what the gateway asks of it and writes the files it "downloads".
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telethon import errors, types
from telethon.tl import custom
from telethon.tl.functions.messages import SendMultiMediaRequest, UploadMediaRequest

from tgmirror.core.errors import FileRefExpired, FloodWait, PerMessage
from tgmirror.core.gateway import CaptionMode, CaptionPolicy, MediaKind, SrcMessage, Unit
from tgmirror.core.telethon_gateway import (
    TelethonGateway,
    _slice16,
    map_exception,
    rewrite_caption,
    src_message,
    strip_source_links,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
KEEP = CaptionPolicy()


def message(msg_id: int = 1, *, text: str = "", media: Any = None, **kw: Any) -> Any:
    return custom.Message(
        id=msg_id, peer_id=types.PeerChannel(1), date=NOW, message=text, media=media, **kw
    )


def photo() -> types.MessageMediaPhoto:
    return types.MessageMediaPhoto(
        photo=types.Photo(id=1, access_hash=1, file_reference=b"", date=NOW, sizes=[], dc_id=1)
    )


def document(*attributes: Any, mime: str = "video/mp4", thumbs: Any = None, size: int = 1) -> Any:
    doc = types.Document(
        id=1,
        access_hash=1,
        file_reference=b"",
        date=NOW,
        mime_type=mime,
        size=size,
        dc_id=1,
        attributes=list(attributes),
        thumbs=thumbs,
    )
    return types.MessageMediaDocument(document=doc)


VIDEO = types.DocumentAttributeVideo(duration=5, w=320, h=240, supports_streaming=True)
COVER = types.PhotoSize(type="m", w=90, h=60, size=100)


def unit_of(*messages: Any) -> Unit:
    return Unit(
        tuple(
            SrcMessage(m.id, NOW, m.message or "", MediaKind.TEXT, grouped_id=m.grouped_id)
            for m in messages
        )
    )


def write(file: str) -> None:
    Path(file).write_bytes(b"data")


def size_of(file: str) -> int:
    return len(Path(file).read_bytes())


def parts_in(folder: Path) -> list[Path]:
    return list(folder.glob("*.part"))


class Stub:
    """Just enough of ``TelegramClient`` for strategy B."""

    def __init__(self, *messages: Any) -> None:
        self.by_id = {m.id: m for m in messages}
        self.downloads: list[tuple[int, str, Any]] = []
        self.sent: list[tuple[str, Any, dict[str, Any]]] = []
        self.uploaded: list[str] = []  # paths given to ``upload_file`` (single-connection)
        self.calls: list[Any] = []  # raw requests given to ``__call__``
        self.next_id = 100

    async def get_input_entity(self, ref: int) -> int:
        return ref

    async def get_messages(self, peer: Any, ids: list[int]) -> list[Any]:
        return [self.by_id.get(i) for i in ids]

    async def download_media(
        self, msg: Any, file: str, thumb: Any = None, progress_callback: Any = None
    ) -> str:
        self.downloads.append((msg.id, file, thumb))
        write(file)
        if progress_callback is not None:  # what Telethon does: (received, total) per part
            progress_callback(2, 4)
            progress_callback(4, 4)
        return file

    async def send_file(self, peer: Any, file: Any, **kw: Any) -> Any:
        self.sent.append(("file", file, kw))
        if isinstance(file, list):
            return [SimpleNamespace(id=self._take()) for _ in file]
        return SimpleNamespace(id=self._take())

    async def send_message(self, peer: Any, text: str = "", **kw: Any) -> Any:
        self.sent.append(("message", text, kw))
        return SimpleNamespace(id=self._take())

    async def upload_file(self, file: str, *, progress_callback: Any = None) -> Any:
        self.uploaded.append(file)
        size = size_of(file)
        if progress_callback is not None:
            progress_callback(size, size)
        return types.InputFile(id=self._take(), parts=1, name=Path(file).name, md5_checksum="")

    async def __call__(self, request: Any) -> Any:
        """The one-shot RPCs an album posts with: ``UploadMediaRequest``, then
        ``SendMultiMediaRequest``. The pool's part requests go through ``_call`` (a sender), not
        here."""
        self.calls.append(request)
        if isinstance(request, UploadMediaRequest):
            if isinstance(request.media, types.InputMediaUploadedPhoto):
                got = types.Photo(
                    id=self._take(), access_hash=1, file_reference=b"", date=NOW, sizes=[], dc_id=1
                )
                return types.MessageMediaPhoto(photo=got)
            doc = types.Document(
                id=self._take(),
                access_hash=1,
                file_reference=b"",
                date=NOW,
                mime_type=request.media.mime_type,
                size=1,
                dc_id=1,
                attributes=request.media.attributes,
            )
            return types.MessageMediaDocument(document=doc)
        if isinstance(request, SendMultiMediaRequest):
            updates = [
                types.UpdateMessageID(id=self._take(), random_id=m.random_id)
                for m in request.multi_media
            ]
            return types.Updates(updates=updates, users=[], chats=[], date=NOW, seq=0)
        raise AssertionError(f"unexpected call {request!r}")

    def _take(self) -> int:
        self.next_id += 1
        return self.next_id


def gateway(*messages: Any) -> tuple[TelethonGateway, Stub]:
    stub = Stub(*messages)
    return TelethonGateway(stub), stub  # type: ignore[arg-type]


# ---- reading what strategy B needs to know -------------------------------------------------------


def test_a_game_an_invoice_and_a_poll_are_named_by_their_title() -> None:
    game = message(media=types.MessageMediaGame(game=SimpleNamespace(title="Chess")))  # type: ignore[arg-type]
    invoice = message(
        media=types.MessageMediaInvoice(
            title="Shop", description="", currency="USD", total_amount=1, start_param=""
        )
    )

    assert src_message(game).title == "Chess"  # type: ignore[union-attr]
    assert src_message(invoice).title == "Shop"  # type: ignore[union-attr]


def poll(*, quiz: bool, results: Any) -> types.MessageMediaPoll:
    def words(text: str) -> types.TextWithEntities:
        return types.TextWithEntities(text=text, entities=[])

    answers = [
        types.PollAnswer(text=words("a"), option=b"1"),
        types.PollAnswer(text=words("b"), option=b"2"),
    ]
    return types.MessageMediaPoll(
        poll=types.Poll(id=1, question=words("2+2?"), answers=answers, hash=0, quiz=quiz),
        results=types.PollResults(results=results),
    )


def test_a_quiz_is_unanswered_until_a_right_answer_is_visible() -> None:
    open_quiz = src_message(message(media=poll(quiz=True, results=None)))
    answered = src_message(
        message(
            media=poll(
                quiz=True, results=[types.PollAnswerVoters(option=b"1", voters=1, correct=True)]
            )
        )
    )
    plain_poll = src_message(message(media=poll(quiz=False, results=None)))

    assert open_quiz is not None and open_quiz.quiz_unanswered and open_quiz.title == "2+2?"
    assert answered is not None and not answered.quiz_unanswered
    assert plain_poll is not None and not plain_poll.quiz_unanswered


def test_a_caption_that_is_too_long_is_that_messages_problem_not_the_runs() -> None:
    assert isinstance(map_exception(errors.MediaCaptionTooLongError(None)), PerMessage)
    assert isinstance(map_exception(errors.MessageTooLongError(None)), PerMessage)


# ---- captions ---------------------------------------------------------------------------------


def url(text: str, offset: int) -> types.MessageEntityUrl:
    return types.MessageEntityUrl(offset, len(text.encode("utf-16-le")) // 2)


def test_links_and_mentions_of_the_source_are_removed_with_their_text() -> None:
    text = "read t.me/news_chan/12 and @news_chan or @other"
    entities = [
        url("t.me/news_chan/12", 5),
        types.MessageEntityMention(27, 10),
        types.MessageEntityMention(41, 6),
    ]

    new_text, kept = strip_source_links(text, entities, {"news_chan"})

    assert new_text == "read  and  or @other"
    assert [(_slice16(new_text, e.offset, e.length)) for e in kept] == ["@other"]


def test_a_hyperlink_to_the_source_keeps_its_words_and_loses_the_link() -> None:
    text = "our channel and a friend"
    entities = [
        types.MessageEntityTextUrl(0, 11, url="https://t.me/news_chan"),
        types.MessageEntityTextUrl(18, 6, url="https://example.org/"),
    ]

    new_text, kept = strip_source_links(text, entities, {"news_chan"})

    assert new_text == text
    assert [(e.url) for e in kept] == ["https://example.org/"]  # type: ignore[attr-defined]


def test_offsets_are_in_utf16_units_so_an_emoji_before_a_link_shifts_them_correctly() -> None:
    text = "😀 t.me/news_chan/1 then bold"
    bold_at = len("😀 t.me/news_chan/1 then ".encode("utf-16-le")) // 2
    entities = [url("t.me/news_chan/1", 3), types.MessageEntityBold(bold_at, 4)]

    new_text, kept = strip_source_links(text, entities, {"news_chan"})

    assert new_text == "😀  then bold"
    (bold,) = kept
    assert _slice16(new_text, bold.offset, bold.length) == "bold"  # type: ignore[attr-defined]


def test_formatting_that_covered_a_removed_link_shrinks_with_it() -> None:
    text = "see t.me/news_chan/1 here"
    entities = [types.MessageEntityBold(0, 25), url("t.me/news_chan/1", 4)]

    new_text, kept = strip_source_links(text, entities, {"news_chan"})

    (bold,) = kept
    assert new_text == "see  here"
    assert _slice16(new_text, bold.offset, bold.length) == new_text  # type: ignore[attr-defined]


def test_a_link_at_the_end_leaves_no_trailing_space_and_no_stray_entities() -> None:
    text = "join us @news_chan"
    entities = [types.MessageEntityMention(8, 10), types.MessageEntityBold(8, 10)]

    assert strip_source_links(text, entities, {"news_chan"}) == ("join us", [])


def test_a_private_channel_link_is_recognised_by_its_number() -> None:
    text = "https://t.me/c/1234567/9 and https://t.me/other/1"
    entities = [url("https://t.me/c/1234567/9", 0), url("https://t.me/other/1", 29)]

    new_text, kept = strip_source_links(text, entities, {"1234567"})

    assert new_text == " and https://t.me/other/1" and len(kept) == 1


def test_a_text_without_such_links_is_returned_as_it_is() -> None:
    entities = [types.MessageEntityBold(0, 3)]

    assert strip_source_links("abc def", entities, {"news_chan"}) == ("abc def", entities)


def test_caption_modes() -> None:
    bold = [types.MessageEntityBold(0, 3)]

    assert rewrite_caption("abc", bold, KEEP, set()) == ("abc", bold)
    assert rewrite_caption("abc", bold, CaptionPolicy(CaptionMode.NONE), set()) == ("", [])
    append = CaptionPolicy(CaptionMode.APPEND, "via X")
    assert rewrite_caption("abc", bold, append, set()) == ("abc\n\nvia X", bold)
    assert rewrite_caption("", [], append, set()) == ("", [])  # nothing to add to


# ---- fetching ---------------------------------------------------------------------------------


async def test_prepare_downloads_the_media_and_keeps_it_for_a_repeat_call(tmp_path: Path) -> None:
    clip = message(4, media=document(VIDEO, thumbs=[COVER]))
    gw, stub = gateway(clip)

    prepared = await gw.prepare(1, unit_of(clip), tmp_path)

    names = sorted(p.name for p in prepared.files)
    assert names == ["4.mp4", "4.thumb.jpg"] and all(p.parent == tmp_path for p in prepared.files)
    assert not parts_in(tmp_path)  # only finished files carry their real name
    assert stub.downloads[1][2] is COVER  # the cover, not a stripped preview

    await gw.prepare(1, unit_of(clip), tmp_path)  # e.g. after a FloodWait
    assert len(stub.downloads) == 2  # nothing fetched twice


async def test_prepare_of_text_and_of_a_poll_downloads_nothing(tmp_path: Path) -> None:
    words = message(1, text="hi")
    quiz = message(2, media=poll(quiz=False, results=None))
    gw, stub = gateway(words, quiz)

    assert (await gw.prepare(1, unit_of(words), tmp_path)).files == ()
    assert (await gw.prepare(1, unit_of(quiz), tmp_path)).files == ()
    assert stub.downloads == []


async def test_prepare_of_an_album_downloads_every_member(tmp_path: Path) -> None:
    members = [message(i, media=photo(), grouped_id=9) for i in (7, 8, 9)]
    gw, _ = gateway(*members)

    prepared = await gw.prepare(1, unit_of(*members), tmp_path)

    assert sorted(p.name for p in prepared.files) == ["7.jpg", "8.jpg", "9.jpg"]


async def test_prepare_refuses_a_unit_with_a_message_that_is_gone(tmp_path: Path) -> None:
    here = message(1, text="a")
    gw, _ = gateway(here)

    pair = [message(1, text="a", grouped_id=5), message(2, text="b", grouped_id=5)]
    with pytest.raises(PerMessage, match="gone_from_source"):
        await gw.prepare(1, unit_of(*pair), tmp_path)  # 2 is not there
    empty = message(3)
    empty.__class__ = types.MessageEmpty  # Telegram answers this for a deleted id
    gone_gw, _ = gateway(empty)
    with pytest.raises(PerMessage):
        await gone_gw.prepare(1, unit_of(message(3)), tmp_path)


# ---- sending ----------------------------------------------------------------------------------


async def test_a_photo_goes_out_as_a_photo_with_its_caption_and_formatting(tmp_path: Path) -> None:
    pic = message(1, text="look", media=photo(), entities=[types.MessageEntityBold(0, 4)])
    gw, stub = gateway(pic)
    prepared = await gw.prepare(1, unit_of(pic), tmp_path)

    ids = await gw.send_prepared(2, prepared, KEEP)

    assert ids == [101]
    kind, file, kw = stub.sent[0]
    assert kind == "file" and file == str(tmp_path / "1.jpg")
    assert kw["caption"] == "look" and kw["formatting_entities"] == [types.MessageEntityBold(0, 4)]
    assert kw["parse_mode"] is None  # the entities are the formatting; no markdown parsing
    assert "attributes" not in kw and kw["thumb"] is None


async def test_a_video_keeps_what_makes_it_a_video_and_its_cover(tmp_path: Path) -> None:
    clip = message(2, media=document(VIDEO, thumbs=[COVER]))
    gw, stub = gateway(clip)
    prepared = await gw.prepare(1, unit_of(clip), tmp_path)

    await gw.send_prepared(2, prepared, KEEP)

    kw = stub.sent[0][2]
    assert kw["attributes"] == [VIDEO] and kw["mime_type"] == "video/mp4"
    # a video must not be forced to a file: Telethon turns force_document into force_file
    assert kw["force_document"] is False and kw["supports_streaming"] is True
    assert kw["nosound_video"] is True  # a silent video must not become a GIF
    assert kw["thumb"] == str(tmp_path / "2.thumb.jpg")
    assert kw["caption"] == "" and kw["formatting_entities"] is None


AUDIO = types.DocumentAttributeAudio(duration=3, title="song")
VOICE = types.DocumentAttributeAudio(duration=3, voice=True)
ROUND_VIDEO = types.DocumentAttributeVideo(duration=3, w=240, h=240, round_message=True)
ANIMATED = types.DocumentAttributeAnimated()
STICKER_ATTR = types.DocumentAttributeSticker(alt="x", stickerset=types.InputStickerSetEmpty())
NAME = types.DocumentAttributeFilename("report.pdf")


@pytest.mark.parametrize(
    ("attributes", "mime", "as_file"),
    [
        ([VIDEO], "video/mp4", False),  # the reported bug: a video went out as a file
        ([VIDEO, ANIMATED], "video/mp4", False),  # a GIF
        ([ROUND_VIDEO], "video/mp4", False),  # a round video
        ([AUDIO], "audio/mpeg", False),  # a song
        ([VOICE], "audio/ogg", False),  # a voice note
        ([STICKER_ATTR], "image/webp", False),  # a sticker
        ([NAME], "application/pdf", True),  # a real file
        ([NAME], "image/jpeg", True),  # a picture sent as a file stays a file, not a photo
    ],
)
async def test_only_what_was_a_plain_file_is_sent_as_a_file(
    tmp_path: Path, attributes: list[Any], mime: str, as_file: bool
) -> None:
    doc = message(3, media=document(*attributes, mime=mime))
    gw, stub = gateway(doc)

    await gw.send_prepared(2, await gw.prepare(1, unit_of(doc), tmp_path), KEEP)

    kw = stub.sent[0][2]
    assert kw["force_document"] is as_file
    assert kw["attributes"] == attributes and kw["mime_type"] == mime


async def test_a_mixed_album_forces_only_its_plain_files(tmp_path: Path) -> None:
    """Each member is forced to a file or not by its own kind (the album goes up through the
    pool's ``UploadMediaRequest`` per member); Telethon's own list ``send_file`` could only apply
    one ``force_document`` to every member, so a document mixed with a song had to pick one."""
    mixed = [
        message(1, media=document(AUDIO, mime="audio/mpeg"), grouped_id=4),
        message(2, media=document(NAME, mime="application/pdf"), grouped_id=4),
    ]
    gw, stub = gateway(*mixed)

    await gw.send_prepared(2, await gw.prepare(1, unit_of(*mixed), tmp_path), KEEP)

    uploads = [c for c in stub.calls if isinstance(c, UploadMediaRequest)]
    assert [u.media.force_file for u in uploads] == [False, True]


async def test_the_caption_policy_is_applied_to_the_files_caption(tmp_path: Path) -> None:
    pic = message(1, text="look", media=photo())
    gw, stub = gateway(pic)
    prepared = await gw.prepare(1, unit_of(pic), tmp_path)

    await gw.send_prepared(2, prepared, CaptionPolicy(CaptionMode.APPEND, "via X"))
    await gw.send_prepared(2, prepared, CaptionPolicy(CaptionMode.NONE))

    assert [s[2]["caption"] for s in stub.sent] == ["look\n\nvia X", ""]


async def test_strip_links_finds_the_sources_name_on_the_message(tmp_path: Path) -> None:
    text = "see t.me/news_chan/5"
    pic = message(1, text=text, media=photo(), entities=[url("t.me/news_chan/5", 4)])
    pic._chat = SimpleNamespace(username="News_Chan", usernames=None)
    gw, stub = gateway(pic)
    prepared = await gw.prepare(1, unit_of(pic), tmp_path)

    await gw.send_prepared(2, prepared, CaptionPolicy(CaptionMode.STRIP_LINKS))

    assert stub.sent[0][2]["caption"] == "see"


async def test_an_album_goes_out_in_one_call_with_one_caption_per_member(tmp_path: Path) -> None:
    members = [
        message(7, text="album", media=photo(), grouped_id=9),
        message(8, media=photo(), grouped_id=9),
    ]
    gw, stub = gateway(*members)
    prepared = await gw.prepare(1, unit_of(*members), tmp_path)

    ids = await gw.send_prepared(2, prepared, CaptionPolicy(CaptionMode.APPEND, "via X"))

    assert len(ids) == 2 and ids[1] == ids[0] + 1  # aligned with the members, in order
    assert stub.uploaded == [str(tmp_path / "7.jpg"), str(tmp_path / "8.jpg")]
    uploads = [c for c in stub.calls if isinstance(c, UploadMediaRequest)]
    assert all(isinstance(u.media, types.InputMediaUploadedPhoto) for u in uploads)
    (sent,) = [c for c in stub.calls if isinstance(c, SendMultiMediaRequest)]
    assert [m.message for m in sent.multi_media] == ["album\n\nvia X", ""]
    assert [m.entities for m in sent.multi_media] == [None, None]


async def test_a_document_album_stays_documents(tmp_path: Path) -> None:
    members = [message(i, media=document(mime="application/pdf"), grouped_id=9) for i in (1, 2)]
    gw, stub = gateway(*members)

    await gw.send_prepared(2, await gw.prepare(1, unit_of(*members), tmp_path), KEEP)

    uploads = [c for c in stub.calls if isinstance(c, UploadMediaRequest)]
    assert [u.media.force_file for u in uploads] == [True, True]


async def test_an_album_video_keeps_its_cover_and_attributes(tmp_path: Path) -> None:
    """The gap the old ``send_file([...])`` album path had: it never passed a per-item thumbnail
    (Telethon read the cover from the file itself instead). Going through the pool per member
    lets each item's own already-downloaded cover (``prepare``) be used directly."""
    clip = message(2, media=document(VIDEO, thumbs=[COVER]), grouped_id=9)
    pic = message(3, media=photo(), grouped_id=9)
    gw, stub = gateway(clip, pic)

    await gw.send_prepared(2, await gw.prepare(1, unit_of(clip, pic), tmp_path), KEEP)

    assert str(tmp_path / "2.thumb.jpg") in stub.uploaded
    uploads = [c for c in stub.calls if isinstance(c, UploadMediaRequest)]
    (video_upload,) = [u for u in uploads if isinstance(u.media, types.InputMediaUploadedDocument)]
    assert video_upload.media.attributes == [VIDEO] and video_upload.media.mime_type == "video/mp4"
    assert video_upload.media.thumb is not None and video_upload.media.nosound_video is True


async def test_text_goes_out_as_text_and_is_never_parsed_as_markdown(tmp_path: Path) -> None:
    words = message(1, text="**not bold** really", entities=[])
    gw, stub = gateway(words)

    ids = await gw.send_prepared(2, await gw.prepare(1, unit_of(words), tmp_path), KEEP)

    assert ids == [101]
    _, text, kw = stub.sent[0]
    assert text == "**not bold** really" and kw["parse_mode"] is None
    assert kw["link_preview"] is False and kw["formatting_entities"] is None


async def test_a_link_preview_is_kept_when_the_original_had_one(tmp_path: Path) -> None:
    page = message(1, text="http://x.org", media=types.MessageMediaWebPage(webpage=None))  # type: ignore[arg-type]
    gw, stub = gateway(page)

    await gw.send_prepared(2, await gw.prepare(1, unit_of(page), tmp_path), KEEP)

    assert stub.sent[0][2]["link_preview"] is True


async def test_a_poll_is_sent_from_its_own_media_object(tmp_path: Path) -> None:
    media = poll(quiz=False, results=None)
    asked = message(1, media=media)
    gw, stub = gateway(asked)

    await gw.send_prepared(2, await gw.prepare(1, unit_of(asked), tmp_path), KEEP)

    assert stub.sent[0][2]["file"] is media


async def test_a_topic_hashtag_is_appended_to_plain_text(tmp_path: Path) -> None:
    """Phase 8: ``CaptionPolicy.hashtag`` applies to plain text too, not only media captions."""
    words = message(1, text="hi there", entities=[])
    gw, stub = gateway(words)

    await gw.send_prepared(
        2, await gw.prepare(1, unit_of(words), tmp_path), CaptionPolicy(hashtag="#general")
    )

    assert stub.sent[0][1] == "hi there\n#general"


async def test_a_topic_hashtag_is_never_appended_to_self_contained_media(tmp_path: Path) -> None:
    """A poll (like location/contact/geo/dice) has no caption slot to append a hashtag to."""
    media = poll(quiz=False, results=None)
    asked = message(1, media=media)
    gw, stub = gateway(asked)

    await gw.send_prepared(
        2, await gw.prepare(1, unit_of(asked), tmp_path), CaptionPolicy(hashtag="#general")
    )

    assert stub.sent[0][2]["file"] is media
    assert stub.sent[0][1] == ""  # no hashtag slipped into the (unused) text argument


async def test_a_message_with_media_we_cannot_rebuild_is_that_messages_problem(
    tmp_path: Path,
) -> None:
    odd = message(1, media=types.MessageMediaUnsupported())
    gw, _ = gateway(odd)
    prepared = await gw.prepare(1, unit_of(odd), tmp_path)

    with pytest.raises(PerMessage, match="unsupported_media"):
        await gw.send_prepared(2, prepared, KEEP)


async def test_send_text_posts_plain_text(tmp_path: Path) -> None:
    gw, stub = gateway()

    assert await gw.send_text(2, "[Game: Chess — không thể sao chép]") == 101
    _, text, kw = stub.sent[0]
    assert text.startswith("[Game") and kw["parse_mode"] is None and kw["link_preview"] is False


# ---- sending by the files' ids ---------------------------------------------------------------


async def test_fetch_reads_the_unit_again_and_downloads_nothing(tmp_path: Path) -> None:
    clip = message(4, media=document(VIDEO))
    gw, stub = gateway(clip)

    prepared = await gw.fetch(1, unit_of(clip))

    assert prepared.files == () and stub.downloads == []
    assert prepared.unit.ids == [4]


async def test_fetch_of_a_message_that_is_gone_fails_the_unit() -> None:
    kept = message(4, media=photo(), grouped_id=9)
    gone = message(5, media=photo(), grouped_id=9)
    gw, _ = gateway(kept)  # the other member of the album was deleted since it was read

    with pytest.raises(PerMessage, match="gone_from_source"):
        await gw.fetch(1, unit_of(kept, gone))


async def test_a_file_is_sent_again_by_its_media_object_with_the_caption_rewritten() -> None:
    pic = message(3, text="look", media=photo())
    gw, stub = gateway(pic)
    prepared = await gw.fetch(1, unit_of(pic))

    ids = await gw.send_by_reference(2, prepared, CaptionPolicy(CaptionMode.APPEND, "via X"))

    assert ids == [101]
    ((kind, what, kw),) = stub.sent
    assert kind == "file" and what is pic.media  # Telegram gets the id of what it stores
    assert kw == {
        "caption": "look\n\nvia X",
        "formatting_entities": None,
        "parse_mode": None,
        "reply_to": None,
    }
    assert stub.downloads == []  # nothing came down, and no upload argument goes with it


async def test_the_caption_can_be_dropped_when_sending_by_id() -> None:
    clip = message(4, text="caption", media=document(VIDEO))
    gw, stub = gateway(clip)

    await gw.send_by_reference(2, await gw.fetch(1, unit_of(clip)), CaptionPolicy(CaptionMode.NONE))

    assert stub.sent[0][2]["caption"] == ""


async def test_an_album_goes_by_the_list_of_its_media_in_one_call() -> None:
    members = [
        message(7, text="album", media=photo(), grouped_id=9),
        message(8, media=photo(), grouped_id=9),
    ]
    gw, stub = gateway(*members)
    prepared = await gw.fetch(1, unit_of(*members))

    ids = await gw.send_by_reference(2, prepared, CaptionPolicy(CaptionMode.APPEND, "via X"))

    assert ids == [101, 102]  # aligned with the members, in order
    ((_, files, kw),) = stub.sent
    assert files == [members[0].media, members[1].media]
    assert kw["caption"] == ["album\n\nvia X", ""] and kw["formatting_entities"] == [[], []]


@pytest.mark.parametrize(
    "error",
    [
        errors.FileReferenceExpiredError(None),
        errors.FileReferenceInvalidError(None),
        errors.FileIdInvalidError(None),
        errors.MediaEmptyError(None),
        errors.MediaInvalidError(None),
        errors.GroupedMediaInvalidError(None),
    ],
)
async def test_media_telegram_will_not_reuse_is_reported_as_such(error: Exception) -> None:
    pic = message(3, media=photo())
    gw, stub = gateway(pic)
    prepared = await gw.fetch(1, unit_of(pic))

    async def refuse(peer: Any, file: Any, **kw: Any) -> Any:
        raise error

    stub.send_file = refuse  # type: ignore[method-assign]

    with pytest.raises(FileRefExpired):
        await gw.send_by_reference(2, prepared, KEEP)


async def test_a_flood_wait_while_sending_by_id_is_still_a_flood_wait() -> None:
    pic = message(3, media=photo())
    gw, stub = gateway(pic)
    prepared = await gw.fetch(1, unit_of(pic))

    async def flood(peer: Any, file: Any, **kw: Any) -> Any:
        raise errors.FloodWaitError(None, capture=30)

    stub.send_file = flood  # type: ignore[method-assign]

    with pytest.raises(FloodWait):
        await gw.send_by_reference(2, prepared, KEEP)
