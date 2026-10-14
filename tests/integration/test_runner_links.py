"""Rewriting links to messages of the source (phase 19c3, ``links: rewrite``) end to end on
``FakeGateway`` and a real SQLite file: a link points at the copy of the message it named, found
through ``msg_map`` (an earlier run's copies count); one whose message has no copy yet stays as it
is and is counted; a forward cannot change a text, so those messages are sent again."""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import Rig
from tgmirror.core.gateway import MediaKind
from tgmirror.engine.runs import InvalidOptions, RunRequest, begin_run, check_options
from tgmirror.engine.transform import Transform
from tgmirror.store.db import Store
from tgmirror.store.runs import Run, RunStatus

LINKS = Transform(rewrite_links=True).to_json()


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


def prefix(rig: Rig) -> str:
    """What a link to a message of the (username-less) destination starts with."""
    return f"t.me/c/{str(rig.dst.id).removeprefix('-100')}"


def texts(rig: Rig) -> list[str]:
    return [m.text for m in rig.gw.messages[rig.dst.id]]


async def test_a_link_to_an_earlier_message_points_at_its_copy(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "the first post")  # 1
    add(rig.src.id, "see t.me/c/555/1 for it", own_links=(1,))  # 2
    add(rig.src.id, "unrelated")  # 3
    store = await rig.store()

    final = await go(rig, store, transform_json=LINKS)

    first = rig.gw.messages[rig.dst.id][0].id
    assert (final.status, final.done, final.links_left) == (RunStatus.DONE, 3, 0)
    assert texts(rig)[1] == f"see https://{prefix(rig)}/{first} for it"
    # only the message with a link is sent again; the others are still forwarded
    assert [list(c.args[1]) for c in rig.gw.calls_to("send_prepared")] == [[2]]
    assert [list(c.args[2]) for c in rig.gw.calls_to("copy_messages")] == [[1], [3]]


async def test_a_link_to_a_later_or_missing_message_stays_and_is_counted(rig: Rig) -> None:
    add = rig.gw.add_message
    add(rig.src.id, "forward reference t.me/c/555/3", own_links=(3,))  # 1: 3 is not copied yet
    add(rig.src.id, "left out", hashtags=("#drop",))  # 2: the filter does not take it
    add(rig.src.id, "later")  # 3
    add(rig.src.id, "see t.me/c/555/2 and t.me/c/555/3", own_links=(2, 3))  # 4
    store = await rig.store()
    left_out = '{"exclude": [{"hashtag": ["#drop"]}]}'

    final = await go(rig, store, transform_json=LINKS, filters_json=left_out, pushdown=False)

    third = rig.gw.messages[rig.dst.id][1].id  # "later"
    assert (final.done, final.skipped_filter) == (3, 1)
    assert texts(rig)[0] == "forward reference t.me/c/555/3"  # 3 had no copy when this was sent
    assert texts(rig)[2] == f"see t.me/c/555/2 and https://{prefix(rig)}/{third}"
    assert final.links_left == 1 + 1  # message 1's link to 3, message 4's link to 2
    assert final.stats["links_left"] == 2


async def test_the_copies_of_an_earlier_run_count(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "target")  # 1
    store = await rig.store()
    await go(rig, store, transform_json=LINKS)
    rig.gw.add_message(rig.src.id, "see t.me/c/555/1", own_links=(1,))  # 2, in a later run

    final = await go(rig, store, transform_json=LINKS)

    target = rig.gw.messages[rig.dst.id][0].id
    assert (final.done, final.links_left) == (1, 0)
    assert texts(rig)[1] == f"see https://{prefix(rig)}/{target}"


async def test_a_destination_with_a_username_is_linked_by_it(rig: Rig) -> None:
    rig.dst = rig.gw.add_channel("Public copy", username="mirror")
    rig.gw.add_message(rig.src.id, "target")  # 1
    rig.gw.add_message(rig.src.id, "see t.me/c/555/1", own_links=(1,))  # 2
    store = await rig.store()

    await go(rig, store, transform_json=LINKS)

    copy = rig.gw.messages[rig.dst.id][0].id
    assert texts(rig)[1] == f"see https://t.me/mirror/{copy}"


async def test_a_caption_is_rewritten_and_goes_by_file_id_when_the_source_allows(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "target")  # 1
    rig.gw.add_message(
        rig.src.id, "clip, see t.me/c/555/1", media=MediaKind.VIDEO, own_links=(1,)
    )  # 2
    store = await rig.store()

    final = await go(rig, store, transform_json=LINKS)

    copy = rig.gw.messages[rig.dst.id][0].id
    assert final.done == 2 and rig.gw.calls_to("prepare") == []  # not downloaded
    assert [list(c.args[1]) for c in rig.gw.calls_to("send_by_reference")] == [[2]]
    assert texts(rig)[1] == f"clip, see https://{prefix(rig)}/{copy}"


async def test_a_protected_source_is_downloaded_not_sent_by_id(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "target")  # 1
    rig.gw.add_message(rig.src.id, "clip t.me/c/555/1", media=MediaKind.VIDEO, own_links=(1,))
    rig.gw.channels[rig.src.id] = replace(rig.src, noforwards=True)
    store = await rig.store()

    final = await go(rig, store, mode="reupload", transform_json=LINKS, protected_ack=True)

    assert final.status is RunStatus.DONE
    assert rig.gw.calls_to("send_by_reference") == [] and rig.gw.calls_to("prepare") != []


async def test_a_poll_with_a_link_is_still_forwarded(rig: Rig) -> None:
    """A poll sent again has no votes: the link is the smaller loss."""
    rig.gw.add_message(rig.src.id, "target")
    rig.gw.add_message(rig.src.id, "which? t.me/c/555/1", media=MediaKind.POLL, own_links=(1,))
    store = await rig.store()

    final = await go(rig, store, transform_json=LINKS)

    assert final.done == 2 and rig.gw.calls_to("send_prepared") == []
    assert final.links_left == 0


async def test_without_the_transform_nothing_is_touched(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "target")
    rig.gw.add_message(rig.src.id, "see t.me/c/555/1", own_links=(1,))
    store = await rig.store()

    await go(rig, store)

    assert texts(rig) == ["target", "see t.me/c/555/1"]
    assert rig.gw.calls_to("send_prepared") == []


def test_forwarding_only_cannot_rewrite_links() -> None:
    with pytest.raises(InvalidOptions) as refused:
        check_options(RunRequest(mode="copy", transform_json=LINKS))

    assert refused.value.key == "transform_needs_reupload"
