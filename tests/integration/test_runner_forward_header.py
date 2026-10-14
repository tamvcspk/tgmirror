"""Keeping the "Forwarded from" header (phase 19c2, ``forward_header: keep``) end to end on
``FakeGateway`` and a real SQLite file: the default forward has no header (decision D2), ``keep`` is
the same forward with it, and nothing that has to send a message again can claim it."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import Rig
from tgmirror.core.gateway import ChatKind, MediaKind
from tgmirror.engine.runs import InvalidOptions, RunRequest, begin_run, check_options
from tgmirror.engine.transform import Transform, TransformError
from tgmirror.store.db import Store
from tgmirror.store.runs import Run, RunStatus

KEEP = Transform(keep_forward_header=True).to_json()


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


async def test_the_default_forward_has_no_header_and_keep_leaves_it_on(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "one")
    store = await rig.store()
    await go(rig, store)
    plain = rig.gw.messages[rig.dst.id][0]

    other = rig.gw.add_channel("Other copy")
    request = RunRequest(mode="auto", transform_json=KEEP)
    run = (await begin_run(store, rig.gw, rig.src, other, request)).run
    await rig.runner(store).run(run)
    kept = rig.gw.messages[other.id][0]

    assert not plain.is_forward and plain.fwd_from_id is None
    assert kept.is_forward and kept.fwd_from_id == rig.src.id
    assert (plain.text, kept.text) == ("one", "one")


async def test_a_forwarded_album_stays_one_album_and_nothing_is_downloaded(rig: Rig) -> None:
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO, MediaKind.VIDEO], "album")
    rig.gw.add_message(rig.src.id, "clip", media=MediaKind.VIDEO)
    store = await rig.store()

    final = await go(rig, store, transform_json=KEEP)

    posted = rig.gw.messages[rig.dst.id]
    assert (final.status, final.done) == (RunStatus.DONE, 3)
    assert all(m.is_forward for m in posted)
    assert len({m.grouped_id for m in posted[:2]}) == 1 and posted[0].grouped_id is not None
    assert rig.gw.calls_to("prepare") == [] and rig.gw.calls_to("send_prepared") == []


async def test_routing_into_topics_is_still_a_forward_and_keeps_the_header(rig: Rig) -> None:
    rig.dst = rig.gw.add_channel("Forum Copy", kind=ChatKind.FORUM)
    rig.gw.add_message(rig.src.id, "py", hashtags=("#py",))
    store = await rig.store()
    rules = Transform.from_data(
        {"forward_header": "keep", "topics": {"hashtag_to_topic": {"map": {"#py": "Python"}}}}
    ).to_json()

    final = await go(rig, store, transform_json=rules)

    (posted,) = rig.gw.messages[rig.dst.id]
    topic = rig.gw.topics[rig.dst.id][0]
    assert final.done == 1 and posted.is_forward and posted.topic_id == topic.id


async def test_the_pair_remembers_the_header_for_the_next_run(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "one")
    store = await rig.store()
    await go(rig, store, transform_json=KEEP)
    rig.gw.add_message(rig.src.id, "two")

    request = RunRequest(mode="auto", batch_size=3, transform_json=KEEP)
    run = (await begin_run(store, rig.gw, rig.src, rig.dst, request)).run
    await rig.runner(store).run(run)

    assert [m.is_forward for m in rig.gw.messages[rig.dst.id]] == [True, True]


# ---- what cannot go with it --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "forward_header: keep\ncaption: [{append: x}]",
        "forward_header: keep\nreply: keep",
        "forward_header: keep\ntopics: {hashtag_to_topic: {auto: all, strip: true}}",
    ],
)
def test_a_transform_cannot_ask_for_the_header_and_for_a_message_sent_again(text: str) -> None:
    import yaml

    with pytest.raises(TransformError, match="forward_header: keep cannot go with"):
        Transform.from_data(yaml.safe_load(text))


def test_a_flag_layered_on_top_cannot_get_around_it() -> None:
    from dataclasses import replace

    with pytest.raises(TransformError):
        replace(Transform(keep_forward_header=True), keep_replies=True)


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "reupload"},
        {"caption": "none"},
        {"topic_as_hashtag": True},
    ],
)
def test_options_that_send_a_message_again_are_refused_with_the_header(
    options: dict[str, Any],
) -> None:
    with pytest.raises(InvalidOptions) as refused:
        check_options(RunRequest(transform_json=KEEP, **options))

    assert refused.value.key == "forward_header_needs_forward"
    check_options(RunRequest(mode="copy", transform_json=KEEP))  # a forward is what it asks for


def test_the_default_is_spelt_drop() -> None:
    import yaml

    assert Transform.from_data(yaml.safe_load("forward_header: drop")).is_empty
    with pytest.raises(TransformError, match="`keep`"):
        Transform.from_data(yaml.safe_load("forward_header: yes"))
