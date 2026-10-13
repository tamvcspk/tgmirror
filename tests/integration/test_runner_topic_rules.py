"""Routing by hashtag and naming topics (phase 19b) end to end on ``FakeGateway`` and a real SQLite
file: what judges the phase — a channel without topics cloned into a forum puts each message in the
topic its hashtag names, in source order within each topic, and a topic is found by its name before
one is created."""

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import Rig
from tgmirror.core.gateway import ChatKind, MediaKind
from tgmirror.engine.runs import InvalidOptions, RunRequest, begin_run, check_options
from tgmirror.engine.transform import Transform
from tgmirror.store.db import Store
from tgmirror.store.runs import Run, RunStatus


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    r.dst = r.gw.add_channel("Forum Copy", kind=ChatKind.FORUM)
    yield r
    for store in r._stores:
        await store.close()


def topics(**sections: Any) -> str:
    return Transform.from_data({"topics": sections}).to_json()


def routing(**options: Any) -> dict[str, Any]:
    return {"hashtag_to_topic": options}


async def begin(rig: Rig, store: Store, **options: Any) -> Run:
    request = RunRequest(**{"mode": "auto", "batch_size": rig.batch_size, **options})
    return (await begin_run(store, rig.gw, rig.src, rig.dst, request)).run


async def go(rig: Rig, store: Store, **options: Any) -> Run:
    run = await begin(rig, store, **options)
    return await rig.runner(store).run(run)  # the fake clock: no real waiting


def landed(rig: Rig) -> dict[str, int | None]:
    """Destination text -> the topic it landed in."""
    return {m.text: m.topic_id for m in rig.gw.messages[rig.dst.id]}


def topic_ids(rig: Rig) -> dict[str, int]:
    return {t.title: t.id for t in rig.gw.topics.get(rig.dst.id, [])}


def by_reference(rig: Rig) -> list[list[int]]:
    """The ids of every send by file id."""
    return [list(c.args[1]) for c in rig.gw.calls_to("send_by_reference")]  # type: ignore[call-overload]


def sample(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "py one", hashtags=("#python",))  # 1
    add(rig.src.id, "rs one", hashtags=("#rust",))  # 2
    add(rig.src.id, "py two", hashtags=("#python",))  # 3
    add(rig.src.id, "nothing")  # 4
    add(rig.src.id, "both", hashtags=("#rust", "#python"))  # 5: the first rule wins, not this order


# ---- routing -----------------------------------------------------------------------------------


async def test_each_message_lands_in_the_topic_its_hashtag_names_in_source_order(rig: Rig) -> None:
    sample(rig)
    store = await rig.store()
    rules = topics(**routing(map={"#python": "Python", "#rust": "Rust"}))

    final = await go(rig, store, transform_json=rules)

    assert (final.status, final.done) == (RunStatus.DONE, 5)
    ids = topic_ids(rig)
    assert set(ids) == {"Python", "Rust"}  # created once each, nothing for the unmatched
    assert landed(rig) == {
        "py one": ids["Python"],
        "rs one": ids["Rust"],
        "py two": ids["Python"],
        "nothing": None,  # routing is not selecting: no rule, no default, General
        "both": ids["Python"],  # #python is the first rule
    }
    # a batch ends where the place changes, so each topic got its messages in the source's order
    calls = [(list(c.args[2]), c.args[3]) for c in rig.gw.calls_to("copy_messages")]  # type: ignore[call-overload]
    assert [ids_ for ids_, _ in calls] == [[1], [2], [3], [4], [5]]
    assert len(rig.gw.calls_to("create_topic")) == 2


async def test_a_list_routes_what_it_names_and_leaves_the_rest_to_general(rig: Rig) -> None:
    """Routing never drops a message: the unlisted ones are cloned too, into General."""
    sample(rig)
    store = await rig.store()

    final = await go(rig, store, transform_json=topics(**routing(map={"#python": "Python"})))

    assert (final.done, final.skipped_filter) == (5, 0)
    assert landed(rig)["rs one"] is None and landed(rig)["nothing"] is None


async def test_selecting_what_is_cloned_is_the_filters_job(rig: Rig) -> None:
    """To take only some hashtags the filter says so (what ``--hashtag`` makes); the transform then
    only decides where they go."""
    sample(rig)
    store = await rig.store()
    only_python = topics(**routing(map={"#python": "Python"}))
    taken = '{"include": [{"hashtag": ["#python"]}]}'

    final = await go(rig, store, transform_json=only_python, filters_json=taken, pushdown=False)

    assert (final.done, final.skipped_filter) == (3, 2)  # py one, py two, both
    assert sorted(landed(rig)) == ["both", "py one", "py two"]


# ---- auto: all ---------------------------------------------------------------------------------


async def test_auto_all_makes_a_topic_for_every_hashtag_as_it_is_met(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "a", hashtags=("#news",))  # 1
    add(rig.src.id, "b", hashtags=("#machine_learning", "#news"))  # 2: its first hashtag
    add(rig.src.id, "c", hashtags=("#news",))  # 3
    add(rig.src.id, "d")  # 4: no hashtag: General
    store = await rig.store()

    final = await go(rig, store, transform_json=topics(**routing(auto="all")))

    assert (final.status, final.done) == (RunStatus.DONE, 4)
    ids = topic_ids(rig)
    assert set(ids) == {"News", "Machine learning"}
    assert landed(rig) == {
        "a": ids["News"], "b": ids["Machine learning"], "c": ids["News"], "d": None,
    }  # fmt: skip


async def test_auto_all_stops_creating_topics_at_its_limit_and_says_so(rig: Rig) -> None:
    add = rig.gw.add_message
    for tag in ("#one", "#two", "#three", "#one"):
        add(rig.src.id, f"m {tag}", hashtags=(tag,))
    store = await rig.store()
    rules = topics(**routing(auto="all", limit=2))

    await go(rig, store, transform_json=rules)

    ids = topic_ids(rig)
    assert set(ids) == {"One", "Two"}  # the third hashtag did not get a topic
    assert landed(rig)["m #three"] is None  # it went to General
    assert landed(rig)["m #one"] == ids["One"]  # one that exists is always found
    assert [c for c, _ in rig.recorder.notices if c == "topics_limit"] == ["topics_limit"]


async def test_auto_all_fills_in_around_explicit_names_and_strips_what_it_used(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "clip #python #tips", media=MediaKind.VIDEO, hashtags=("#python", "#tips"))
    add(rig.src.id, "clip #tips", media=MediaKind.VIDEO, hashtags=("#tips",))
    store = await rig.store()
    rules = topics(**routing(map={"#python": "Snake"}, auto="all", strip=True))

    await go(rig, store, transform_json=rules)

    ids = topic_ids(rig)
    assert [m.topic_id for m in rig.gw.messages[rig.dst.id]] == [ids["Snake"], ids["Tips"]]
    assert [m.text for m in rig.gw.messages[rig.dst.id]] == ["clip #tips", "clip"]


# ---- finding a topic by its name ---------------------------------------------------------------


async def test_a_topic_the_destination_already_has_is_reused_not_created(rig: Rig) -> None:
    existing = rig.gw.add_topic(rig.dst.id, "python")  # the user made it, in other letters
    rig.gw.add_message(rig.src.id, "py", hashtags=("#python",))
    store = await rig.store()
    rules = topics(**routing(map={"#python": "Python"}))

    await go(rig, store, transform_json=rules)

    assert rig.gw.calls_to("create_topic") == []
    assert landed(rig) == {"py": existing.id}


async def test_a_later_run_finds_the_topic_the_earlier_one_created(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "one", hashtags=("#python",))
    store = await rig.store()
    rules = topics(**routing(auto=["#python"]))
    await go(rig, store, transform_json=rules)
    rig.gw.add_message(rig.src.id, "two", hashtags=("#python",))

    await go(rig, store, transform_json=rules)

    assert len(rig.gw.calls_to("create_topic")) == 1
    assert len(set(landed(rig).values())) == 1  # both in the one Python topic


async def test_general_by_name_is_the_topic_a_post_with_none_lands_in(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "py", hashtags=("#python",))
    store = await rig.store()

    await go(rig, store, transform_json=topics(**routing(map={"#python": "General"})))

    assert rig.gw.calls_to("create_topic") == []
    assert landed(rig) == {"py": None}


# ---- strip -------------------------------------------------------------------------------------


async def test_strip_sends_a_captioned_unit_again_without_its_routing_hashtag(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "clip #python #keep", media=MediaKind.VIDEO, hashtags=("#python", "#keep"))  # 1
    add(rig.src.id, "plain #python", hashtags=("#python",))  # 2: text, not a caption
    add(rig.src.id, "clip #rust", media=MediaKind.VIDEO, hashtags=("#rust",))  # 3: no rule
    store = await rig.store()
    rules = topics(**routing(map={"#python": "Python"}, strip=True))

    await go(rig, store, transform_json=rules)

    ids = topic_ids(rig)
    assert landed(rig) == {
        "clip #keep": ids["Python"],  # sent again by its file id, the routing hashtag gone
        "plain #python": ids["Python"],  # forwarded as it is: text is the content itself
        "clip #rust": None,
    }
    assert by_reference(rig) == [[1]]  # only the unit that had to change


async def test_strip_with_text_too_takes_it_out_of_plain_text_as_well(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "plain #python end", hashtags=("#python",))
    store = await rig.store()
    rules = Transform.from_data(
        {
            "caption": [{"text_too": True}],
            "topics": routing(map={"#python": "Python"}, strip=True),
        }
    ).to_json()

    await go(rig, store, transform_json=rules)

    assert list(landed(rig)) == ["plain end"]
    assert rig.gw.calls_to("copy_messages") == []


async def test_routing_alone_never_sends_anything_again(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "clip #python", media=MediaKind.VIDEO, hashtags=("#python",))
    store = await rig.store()

    await go(rig, store, transform_json=topics(**routing(auto=["#python"])))

    assert by_reference(rig) == [] and len(rig.gw.calls_to("copy_messages")) == 1
    assert list(landed(rig)) == ["clip #python"]  # untouched


# ---- naming topics -----------------------------------------------------------------------------


async def test_topic_map_renames_and_merges_source_topics(rig: Rig) -> None:
    rig.src = rig.gw.add_channel("Forum", kind=ChatKind.FORUM)
    a = rig.gw.add_topic(rig.src.id, "Chat A")
    b = rig.gw.add_topic(rig.src.id, "Chat B")
    c = rig.gw.add_topic(rig.src.id, "Other")
    add = rig.gw.add_message
    add(rig.src.id, "a1", topic_id=a.id)
    add(rig.src.id, "b1", topic_id=b.id)
    add(rig.src.id, "c1", topic_id=c.id)
    store = await rig.store()
    rules = topics(topic_map={"chat a": "Chats", b.id: "Chats"})

    final = await go(rig, store, transform_json=rules)

    assert final.done == 3
    ids = topic_ids(rig)
    assert set(ids) == {"Chats", "Other"}  # two source topics, one destination topic
    assert landed(rig) == {"a1": ids["Chats"], "b1": ids["Chats"], "c1": ids["Other"]}
    with sqlite3.connect(rig.path) as db:
        rows = db.execute("SELECT src_topic_id, dst_topic_id FROM topic_map").fetchall()
    assert dict(rows) == {a.id: ids["Chats"], b.id: ids["Chats"], c.id: ids["Other"]}


async def test_a_topic_kept_as_a_hashtag_takes_the_renamed_name_and_the_format(rig: Rig) -> None:
    rig.src = rig.gw.add_channel("Forum", kind=ChatKind.FORUM)
    rig.dst = rig.gw.add_channel("Plain copy")
    topic = rig.gw.add_topic(rig.src.id, "Chat A")
    rig.gw.add_message(rig.src.id, "hello", topic_id=topic.id)
    store = await rig.store()
    rules = topics(topic_map={"Chat A": "Chats"}, hashtag_format="#t_{slug}")

    await go(rig, store, mode="reupload", topic_as_hashtag=True, transform_json=rules)

    assert list(landed(rig)) == ["hello\n#t_chats"]
    assert rig.gw.calls_to("create_topic") == []


# ---- what is refused ---------------------------------------------------------------------------


async def test_hashtag_routing_to_a_destination_without_topics_is_refused_before_anything(
    rig: Rig,
) -> None:
    rig.dst = rig.gw.add_channel("Plain copy")
    rig.gw.add_message(rig.src.id, "py", hashtags=("#python",))
    store = await rig.store()

    with pytest.raises(InvalidOptions) as refused:
        await begin(rig, store, transform_json=topics(**routing(auto=["#python"])))

    assert refused.value.key == "topics_need_forum"
    assert rig.gw.messages.get(rig.dst.id, []) == []
    assert await store.latest_run(rig.src.id, rig.dst.id) is None


def test_options_that_contradict_a_topic_transform_are_refused() -> None:
    stripping = topics(**routing(auto=["#a"], strip=True))
    routing_only = topics(**routing(auto=["#a"]))

    # a caption flag goes on top of the file, whatever the file does
    check_options(RunRequest(caption="strip-links", transform_json=stripping))
    with pytest.raises(InvalidOptions) as forwarded:
        check_options(RunRequest(mode="copy", transform_json=stripping))
    assert forwarded.value.key == "transform_needs_reupload"
    # a forward can go to a topic: routing alone is fine with --mode copy and with --caption
    check_options(RunRequest(mode="copy", transform_json=routing_only))
    check_options(RunRequest(caption="strip-links", transform_json=routing_only))
