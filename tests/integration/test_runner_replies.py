"""Keeping replies (phase 19c, ``reply: keep``) end to end on ``FakeGateway`` and a real SQLite
file: a message that answered another one answers the copy of it in the destination, found through
``msg_map`` (so across runs too); one whose original has no copy is sent without its reply and
counted; a forward cannot carry a reply, so those messages are sent again and decision D3 guards
that like any other."""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import Rig
from tgmirror.core.gateway import ChatKind, MediaKind
from tgmirror.engine.runs import (
    InvalidOptions,
    NeedsAcknowledgement,
    RunRequest,
    begin_run,
    check_options,
)
from tgmirror.engine.transform import Transform
from tgmirror.store.db import Store
from tgmirror.store.runs import Run, RunStatus

KEEP = Transform(keep_replies=True).to_json()


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


async def go(rig: Rig, store: Store, **options: Any) -> Run:
    request = RunRequest(**{"mode": "auto", "batch_size": rig.batch_size, **options})
    run = (await begin_run(store, rig.gw, rig.src, rig.dst, request)).run
    return await rig.runner(store).run(run)


def answers(rig: Rig) -> dict[str, str | None]:
    """Destination text -> the text of the message it answers there (``None``: it answers none)."""
    posted = {m.id: m for m in rig.gw.messages[rig.dst.id]}
    return {
        m.text: (posted[m.reply_to_id].text if m.reply_to_id in posted else None)
        for m in posted.values()
    }


def calls(rig: Rig, method: str) -> list[list[int]]:
    """The ids of every call (``copy_messages`` takes src, dst, ids; the sends dst, ids)."""
    at = 2 if method == "copy_messages" else 1
    return [list(c.args[at]) for c in rig.gw.calls_to(method)]  # type: ignore[call-overload]


# ---- the reply ---------------------------------------------------------------------------------


async def test_a_reply_answers_the_copy_of_the_message_it_answered(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "question")  # 1
    add(rig.src.id, "answer", reply_to_id=1)  # 2
    add(rig.src.id, "unrelated")  # 3
    store = await rig.store()

    final = await go(rig, store, transform_json=KEEP)

    assert (final.status, final.done) == (RunStatus.DONE, 3)
    assert answers(rig) == {"question": None, "answer": "question", "unrelated": None}
    # only the reply is sent again; the rest are still forwarded
    assert calls(rig, "copy_messages") == [[1], [3]]
    assert calls(rig, "send_prepared") == [[2]]
    assert len(rig.gw.calls_to("send_prepared")) == 1
    assert final.reply_lost == 0


async def test_without_the_transform_a_reply_is_a_plain_forward(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "question")
    rig.gw.add_message(rig.src.id, "answer", reply_to_id=1)
    store = await rig.store()

    final = await go(rig, store)

    assert final.done == 2
    assert rig.gw.calls_to("send_prepared") == []
    assert answers(rig) == {"question": None, "answer": None}


async def test_a_copy_made_by_an_earlier_run_is_found_through_the_pair(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "question")  # 1
    store = await rig.store()
    await go(rig, store, transform_json=KEEP)

    rig.gw.add_message(rig.src.id, "answer", reply_to_id=1)  # 2, in a later run
    final = await go(rig, store, transform_json=KEEP)

    assert (final.done, final.reply_lost) == (1, 0)
    assert answers(rig) == {"question": None, "answer": "question"}


async def test_a_reply_to_what_was_not_copied_is_sent_without_it_and_counted(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "old", hashtags=("#drop",))  # 1: the filter leaves it out
    add(rig.src.id, "answer", reply_to_id=1)  # 2
    add(rig.src.id, "kept")  # 3
    add(rig.src.id, "reply to kept", reply_to_id=3)  # 4
    store = await rig.store()
    left_out = '{"exclude": [{"hashtag": ["#drop"]}]}'

    final = await go(rig, store, transform_json=KEEP, filters_json=left_out, pushdown=False)

    assert (final.status, final.done, final.skipped_filter) == (RunStatus.DONE, 3, 1)
    assert answers(rig) == {"answer": None, "kept": None, "reply to kept": "kept"}
    assert final.reply_lost == 1  # the message is in ``done`` all the same
    assert final.stats["reply_lost"] == 1


async def test_a_reply_to_a_message_that_failed_has_no_copy_to_answer(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "bad")  # 1
    rig.gw.add_message(rig.src.id, "answer", reply_to_id=1)  # 2
    rig.gw.poison(rig.src.id, 1)
    store = await rig.store()

    final = await go(rig, store, transform_json=KEEP)

    assert (final.done, final.failed, final.reply_lost) == (1, 1, 1)
    assert answers(rig) == {"answer": None}


# ---- what is sent again, and how ----------------------------------------------------------------


async def test_a_media_reply_goes_by_file_id_and_an_album_answers_as_a_whole(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "question")  # 1
    add(rig.src.id, "pic", media=MediaKind.PHOTO, reply_to_id=1)  # 2
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO, MediaKind.VIDEO], "album", reply_to_id=1)  # 3, 4
    store = await rig.store()

    final = await go(rig, store, transform_json=KEEP)

    assert (final.done, final.reply_lost) == (4, 0)
    assert calls(rig, "send_by_reference") == [[2], [3, 4]]  # nothing downloaded
    assert rig.gw.calls_to("prepare") == []
    posted = rig.gw.messages[rig.dst.id]
    first = posted[0].id
    assert [m.reply_to_id for m in posted] == [None, first, first, first]


async def test_a_poll_is_still_forwarded_and_keeps_its_votes_not_its_reply(rig: Rig) -> None:
    """A poll sent again would lose its votes: ``auto`` keeps forwarding it."""
    rig.gw.add_message(rig.src.id, "question")  # 1
    rig.gw.add_message(rig.src.id, "which?", media=MediaKind.POLL, reply_to_id=1)  # 2
    store = await rig.store()

    final = await go(rig, store, transform_json=KEEP)

    assert final.done == 2
    assert rig.gw.calls_to("send_prepared") == [] and rig.gw.calls_to("send_by_reference") == []
    assert [m.reply_to_id for m in rig.gw.messages[rig.dst.id]] == [None, None]


async def test_a_protected_source_needs_the_users_word_to_keep_replies(rig: Rig) -> None:
    """Decision D3: a reply is kept by sending the message again, which downloads on a protected
    source, so it is gated like ``--caption`` and a transform are."""
    rig.gw.add_message(rig.src.id, "question")
    rig.gw.add_message(rig.src.id, "answer", reply_to_id=1)
    rig.gw.channels[rig.src.id] = replace(rig.src, noforwards=True)
    store = await rig.store()
    request = RunRequest(mode="auto", batch_size=3, transform_json=KEEP)

    with pytest.raises(NeedsAcknowledgement):
        await begin_run(store, rig.gw, rig.src, rig.dst, request)
    # what the user's statement lets through is the long way for every message (``--mode reupload``)
    stated = replace(request, mode="reupload", protected_ack=True)
    run = (await begin_run(store, rig.gw, rig.src, rig.dst, stated)).run
    final = await rig.runner(store).run(run)

    assert final.status is RunStatus.DONE
    assert rig.gw.calls_to("send_by_reference") == []  # never by id from a protected source
    assert answers(rig) == {"question": None, "answer": "question"}


def test_forwarding_only_cannot_keep_replies() -> None:
    with pytest.raises(InvalidOptions) as refused:
        check_options(RunRequest(mode="copy", transform_json=KEEP))

    assert refused.value.key == "transform_needs_reupload"


# ---- with hashtag routing ----------------------------------------------------------------------


@pytest.fixture
async def forum(rig: Rig) -> Rig:
    rig.dst = rig.gw.add_channel("Forum Copy", kind=ChatKind.FORUM)
    return rig


def routed_keep() -> str:
    return Transform.from_data(
        {
            "reply": "keep",
            "topics": {"hashtag_to_topic": {"map": {"#py": "Python", "#rs": "Rust"}}},
        }
    ).to_json()


async def test_a_reply_stays_in_its_topic_and_is_not_kept_across_topics(forum: Rig) -> None:
    add = forum.gw.add_message
    add(forum.src.id, "py question", hashtags=("#py",))  # 1
    add(forum.src.id, "py answer", hashtags=("#py",), reply_to_id=1)  # 2: same topic
    add(forum.src.id, "rs answer", hashtags=("#rs",), reply_to_id=1)  # 3: another topic
    store = await forum.store()

    final = await go(forum, store, transform_json=routed_keep())

    assert (final.done, final.reply_lost) == (3, 1)
    assert answers(forum) == {
        "py question": None,
        "py answer": "py question",
        "rs answer": None,
    }
    topics = {t.title: t.id for t in forum.gw.topics[forum.dst.id]}
    landed = {m.text: m.topic_id for m in forum.gw.messages[forum.dst.id]}
    assert landed == {
        "py question": topics["Python"],
        "py answer": topics["Python"],
        "rs answer": topics["Rust"],
    }
