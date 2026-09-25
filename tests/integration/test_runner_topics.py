"""Forum topic routing end to end on ``FakeGateway`` and a real SQLite file: no network.

What phase 8 is judged by (docs/06-lo-trinh.md): a batch never mixes source topics, each lands in
the right destination topic, a topic met for the first time (this run or a later one) is created
once and remembered, General needs no mapping at all, and a non-forum destination falls back to a
hashtag when asked.
"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import LIMITS, Rig
from tgmirror.core.gateway import ChatKind, MediaKind
from tgmirror.engine.runner import Runner, RunnerTiming
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store.db import Store
from tgmirror.store.runs import Run


@pytest.fixture
async def rig(tmp_path: Path) -> Any:
    r = Rig(tmp_path)
    r.src = r.gw.add_channel("Forum", kind=ChatKind.FORUM)
    r.dst = r.gw.add_channel("Forum Copy", kind=ChatKind.FORUM)
    yield r
    for store in r._stores:
        await store.close()


async def begin(rig: Rig, store: Store, **options: Any) -> Run:
    merged = {"mode": "auto", "batch_size": rig.batch_size, **options}
    return (await begin_run(store, rig.gw, rig.src, rig.dst, RunRequest(**merged))).run


def copy_calls(rig: Rig) -> list[tuple[list[int], int | None]]:
    """``(ids, topic)`` of every ``copy_messages`` call, in order."""
    return [(list(c.args[2]), c.args[3]) for c in rig.gw.calls_to("copy_messages")]


def dst_topics(rig: Rig) -> dict[str, int | None]:
    """Destination text -> the topic it landed in, for messages actually copied."""
    return {m.text: m.topic_id for m in rig.gw.messages[rig.dst.id]}


async def topic_map(rig: Rig, run: Run) -> dict[int, int]:
    with sqlite3.connect(rig.path) as db:
        (mirror_id,) = db.execute("SELECT mirror_id FROM runs WHERE id = ?", (run.id,)).fetchone()
        rows = db.execute(
            "SELECT src_topic_id, dst_topic_id FROM topic_map WHERE mirror_id = ?", (mirror_id,)
        ).fetchall()
    return dict(rows)


async def test_a_batch_cuts_on_topic_change_and_each_lands_in_its_mapped_topic(
    rig: Rig,
) -> None:
    ann = rig.gw.add_topic(rig.src.id, "Announcements")
    off = rig.gw.add_topic(rig.src.id, "Off-topic")
    rig.gw.add_message(rig.src.id, "a1", topic_id=ann.id)
    rig.gw.add_message(rig.src.id, "a2", topic_id=ann.id)
    rig.gw.add_message(rig.src.id, "o1", topic_id=off.id)
    rig.gw.add_message(rig.src.id, "a3", topic_id=ann.id)  # back to Announcements: cuts again
    store = await rig.store()

    run = await Runner(store, rig.gw, LIMITS, timing=RunnerTiming(0.1, 3600)).run(
        await begin(rig, store, batch_size=10)
    )

    assert run.done == 4
    calls = copy_calls(rig)
    assert [ids for ids, _ in calls] == [[1, 2], [3], [4]]  # cut at every topic change
    topics = [t for _, t in calls]
    assert topics[0] == topics[2] and topics[0] is not None  # both Announcements batches agree
    assert topics[1] not in (None, topics[0])  # Off-topic maps somewhere else
    mapping = await topic_map(rig, run)
    assert set(mapping) == {ann.id, off.id}
    assert dst_topics(rig) == {
        "a1": mapping[ann.id],
        "a2": mapping[ann.id],
        "o1": mapping[off.id],
        "a3": mapping[ann.id],
    }


async def test_general_needs_no_topic_map_entry_or_create_call(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "hello")  # no topic_id: Telethon gives none for General either
    store = await rig.store()

    run = await Runner(store, rig.gw, LIMITS).run(await begin(rig, store))

    assert run.done == 1
    assert rig.gw.calls_to("create_topic") == []
    assert await topic_map(rig, run) == {}
    ((ids, topic),) = copy_calls(rig)
    assert ids == [1] and topic is None  # left to Telegram's own default


async def test_a_topic_new_to_a_later_run_is_created_once_and_remembered(rig: Rig) -> None:
    first_topic = rig.gw.add_topic(rig.src.id, "First")
    rig.gw.add_message(rig.src.id, "a1", topic_id=first_topic.id)
    store = await rig.store()
    await Runner(store, rig.gw, LIMITS).run(await begin(rig, store))
    first_creations = len(rig.gw.calls_to("create_topic"))

    second_topic = rig.gw.add_topic(rig.src.id, "Second")
    rig.gw.add_message(rig.src.id, "b1", topic_id=second_topic.id)
    rig.gw.add_message(rig.src.id, "a2", topic_id=first_topic.id)  # an old topic: no new create
    second_run = await Runner(store, rig.gw, LIMITS).run(await begin(rig, store))

    assert first_creations == 1
    assert len(rig.gw.calls_to("create_topic")) == 2  # exactly one more, for "Second"
    mapping = await topic_map(rig, second_run)
    assert set(mapping) == {first_topic.id, second_topic.id}
    assert dst_topics(rig)["a2"] == mapping[first_topic.id]  # the remembered mapping, not a new one


async def test_non_forum_destination_falls_back_to_a_hashtag_when_asked(rig: Rig) -> None:
    rig.dst = rig.gw.add_channel("Broadcast copy")  # not a forum
    topic = rig.gw.add_topic(rig.src.id, "General Discussion")
    rig.gw.add_message(rig.src.id, "hello there", media=MediaKind.TEXT, topic_id=topic.id)
    store = await rig.store()

    run = await Runner(store, rig.gw, LIMITS).run(
        await begin(rig, store, mode="reupload", topic_as_hashtag=True)
    )

    assert run.done == 1
    assert rig.gw.calls_to("create_topic") == []  # nothing to create on a non-forum destination
    (sent,) = rig.gw.messages[rig.dst.id]
    assert sent.text == "hello there\n#general_discussion"


async def test_non_forum_destination_drops_the_topic_silently_without_the_flag(rig: Rig) -> None:
    rig.dst = rig.gw.add_channel("Broadcast copy")
    topic = rig.gw.add_topic(rig.src.id, "General Discussion")
    rig.gw.add_message(rig.src.id, "hello there", media=MediaKind.TEXT, topic_id=topic.id)
    store = await rig.store()

    await Runner(store, rig.gw, LIMITS).run(await begin(rig, store))  # --mode copy: no rewriting

    (sent,) = rig.gw.messages[rig.dst.id]
    assert sent.text == "hello there"  # forwarded as is; no hashtag can be added


async def test_auto_mode_sends_every_topic_message_again_to_carry_its_hashtag(rig: Rig) -> None:
    """``auto`` with ``--caption keep`` would forward everything, and a forward cannot add the
    hashtag: with ``topic_as_hashtag`` a forum unit outside General is sent again instead, even
    with no caption of its own. A poll (nowhere to put it) and General stay forwarded."""
    rig.dst = rig.gw.add_channel("Broadcast copy")  # not a forum
    topic = rig.gw.add_topic(rig.src.id, "News")
    rig.gw.add_message(rig.src.id, "text", topic_id=topic.id)
    rig.gw.add_message(rig.src.id, "", media=MediaKind.PHOTO, size=10, topic_id=topic.id)
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO, MediaKind.PHOTO], topic_id=topic.id)
    rig.gw.add_message(rig.src.id, "q?", media=MediaKind.POLL, topic_id=topic.id)
    rig.gw.add_message(rig.src.id, "general")  # no topic: nothing to add
    store = await rig.store()

    run = await Runner(store, rig.gw, LIMITS).run(await begin(rig, store, topic_as_hashtag=True))

    assert run.done == 6
    assert [m.text for m in rig.gw.messages[rig.dst.id]] == [
        "text\n#news",
        "#news",
        "#news",  # the album gets it once, as its only caption
        "",
        "q?",  # forwarded: a poll has no caption
        "general",
    ]
    assert [list(c.args[2]) for c in rig.gw.calls_to("copy_messages")] == [[5], [6]]


async def test_album_gets_the_hashtag_once_on_its_caption(rig: Rig) -> None:
    rig.dst = rig.gw.add_channel("Broadcast copy")
    topic = rig.gw.add_topic(rig.src.id, "News")
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO, MediaKind.PHOTO], "look", topic_id=topic.id)
    store = await rig.store()

    await Runner(store, rig.gw, LIMITS).run(
        await begin(rig, store, mode="reupload", topic_as_hashtag=True)
    )

    assert [m.text for m in rig.gw.messages[rig.dst.id]] == ["look\n#news", ""]
