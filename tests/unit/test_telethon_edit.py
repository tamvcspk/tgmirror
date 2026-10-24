"""The Telethon side of ``tgmirror edit`` (Phase 20.3): what a live message's text becomes under a
``CaptionPolicy`` (as HTML, with its entities), when it is not safe to write back, and how
``edit_message`` answers Telegram. No network: Telethon message objects and a stub client.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from telethon import errors, types
from telethon.tl import custom

from tgmirror.core.errors import PerMessage
from tgmirror.core.gateway import CaptionPolicy, RemoveHashtags, Replace
from tgmirror.core.telethon_gateway import TelethonGateway, _rewrite_message, map_exception

NOW = datetime(2026, 1, 1, tzinfo=UTC)
STRIP = CaptionPolicy(ops=(RemoveHashtags(("#ads",)),), text_too=True)


def message(text: str, *, entities: list[Any] | None = None, media: Any = None, **kw: Any) -> Any:
    return custom.Message(
        id=7,
        peer_id=types.PeerChannel(1),
        date=NOW,
        message=text,
        entities=entities or [],
        media=media,
        **kw,
    )


def photo() -> types.MessageMediaPhoto:
    return types.MessageMediaPhoto(
        photo=types.Photo(id=1, access_hash=1, file_reference=b"", date=NOW, sizes=[], dc_id=1)
    )


def test_a_hashtag_is_taken_out_and_the_formatting_around_it_is_kept() -> None:
    entities = [types.MessageEntityBold(0, 5), types.MessageEntityHashtag(6, 4)]
    live = message("Hello #ads", entities=entities, media=photo())

    rewrite = _rewrite_message(live, STRIP)

    assert rewrite.before == "<strong>Hello</strong> #ads"
    assert rewrite.after == "<strong>Hello</strong>"
    assert rewrite.lossless


def test_plain_text_is_left_alone_unless_the_transform_asks_for_it() -> None:
    live = message("Hello #ads", entities=[types.MessageEntityHashtag(6, 4)])
    captions_only = CaptionPolicy(ops=(RemoveHashtags(("#ads",)),))

    assert _rewrite_message(live, captions_only).after == "Hello #ads"
    assert _rewrite_message(live, STRIP).after == "Hello"


def test_a_message_the_operations_would_empty_is_left_as_it_is() -> None:
    live = message("#ads", entities=[types.MessageEntityHashtag(0, 4)])

    rewrite = _rewrite_message(live, STRIP)

    assert rewrite.before == rewrite.after == "#ads"


def test_a_service_message_and_a_poll_have_no_text_to_change() -> None:
    service = message("", action=types.MessageActionChatJoinedByLink(inviter_id=1))
    poll = message(
        "Question #ads",
        entities=[types.MessageEntityHashtag(9, 4)],
        media=types.MessageMediaPoll(
            poll=types.Poll(id=1, question=types.TextWithEntities("Q", []), answers=[], hash=0),
            results=types.PollResults(),
        ),
    )

    assert _rewrite_message(service, STRIP).before == _rewrite_message(service, STRIP).after
    assert _rewrite_message(poll, STRIP).before == _rewrite_message(poll, STRIP).after


def test_formatting_html_cannot_carry_is_not_lossless() -> None:
    """A spoiler has no HTML tag: writing the HTML back would lose it, so ``undo`` could not
    restore the message and it must never be edited."""
    live = message("secret #ads", entities=[types.MessageEntitySpoiler(0, 6)])

    assert not _rewrite_message(live, STRIP).lossless


def test_what_telegram_detects_by_itself_does_not_make_a_message_lossy() -> None:
    entities = [
        types.MessageEntityHashtag(0, 4),
        types.MessageEntityMention(5, 6),
        types.MessageEntityUrl(12, 15),
    ]
    live = message("#ads @someone https://x.org/a ok", entities=entities)

    assert _rewrite_message(live, STRIP).lossless


def test_a_hand_made_link_is_kept_apart_from_a_bare_url() -> None:
    entities = [types.MessageEntityTextUrl(0, 4, url="https://x.org/")]

    assert _rewrite_message(message("link #ads", entities=entities), STRIP).lossless


def test_replace_works_on_the_live_text() -> None:
    live = message("price 10", media=photo())

    rewrite = _rewrite_message(live, CaptionPolicy(ops=(Replace("10", "20"),)))

    assert (rewrite.before, rewrite.after) == ("price 10", "price 20")


class EditClient:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.raises = raises
        self.edits: list[tuple[Any, ...]] = []

    async def get_input_entity(self, ref: int) -> Any:
        return ref

    async def edit_message(self, peer: Any, msg_id: int, text: str, **kw: Any) -> None:
        self.edits.append((peer, msg_id, text, kw))
        if self.raises:
            raise self.raises


async def test_edit_message_writes_the_text_with_its_entities() -> None:
    client = EditClient()

    changed = await TelethonGateway(client).edit_message(5, 7, "<strong>Hi</strong> there")  # type: ignore[arg-type]

    assert changed is True
    peer, msg_id, text, kw = client.edits[0]
    assert (peer, msg_id, text) == (5, 7, "Hi there")
    assert [type(e) for e in kw["formatting_entities"]] == [types.MessageEntityBold]


async def test_telegram_saying_it_already_reads_that_is_not_an_error() -> None:
    client = EditClient(errors.MessageNotModifiedError(None))

    assert await TelethonGateway(client).edit_message(5, 7, "same") is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raised",
    [
        errors.MessageAuthorRequiredError(None),
        errors.MessageEditTimeExpiredError(None),
        errors.MessageEmptyError(None),
        errors.MessageIdInvalidError(None),
    ],
)
def test_a_message_that_cannot_be_edited_is_a_per_message_problem(raised: BaseException) -> None:
    assert isinstance(map_exception(raised), PerMessage)


def test_links_into_the_pairs_source_are_pointed_at_the_copies_in_another_chat() -> None:
    """``edit --mirror`` (phase 20.5): the message sits in the destination, the link names the
    source, so ``LinkRewrite.names`` says which chat the links are into."""
    from tgmirror.core.gateway import LinkRewrite

    entities = [types.MessageEntityTextUrl(4, 4, url="https://t.me/srcchan/4")]
    live = message("see docs", entities=entities)
    policy = CaptionPolicy(links=LinkRewrite({4: 9}, "t.me/c/55", ("srcchan",)))

    rewrite = _rewrite_message(live, policy)

    assert rewrite.before == 'see <a href="https://t.me/srcchan/4">docs</a>'
    assert rewrite.after == 'see <a href="https://t.me/c/55/9">docs</a>'
    unnamed = CaptionPolicy(links=LinkRewrite({4: 9}, "t.me/c/55"))  # then only its own chat counts
    assert _rewrite_message(live, unnamed).after == rewrite.before
