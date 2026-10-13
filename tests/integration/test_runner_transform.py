"""A transform (phase 19, caption operations) end to end on ``FakeGateway`` and a real SQLite
file: which units it sends again and how, that decision D3 still guards it, and that the pair
remembers it for ``run``."""

import random
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import LIMITS, Rig
from tgmirror.core.gateway import MediaKind
from tgmirror.engine.runner import Runner, RunnerTiming
from tgmirror.engine.runs import (
    InvalidOptions,
    NeedsAcknowledgement,
    RunRequest,
    begin_run,
    check_options,
)
from tgmirror.engine.transform import Transform, TransformError
from tgmirror.store.db import Store
from tgmirror.store.runs import Run, RunStatus


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    r.tmp = tmp_path / "scratch"  # type: ignore[attr-defined]
    yield r
    for store in r._stores:
        await store.close()


def runner(rig: Rig, store: Store) -> Runner:
    return Runner(
        store,
        rig.gw,
        LIMITS,
        reporter=rig.recorder,
        sleep=rig.sleep,
        rng=random.Random(0),
        timing=RunnerTiming(poll_interval=0.2, heartbeat_interval=3600),
        tmp_dir=rig.tmp,  # type: ignore[attr-defined]
        mono=rig.mono,
    )


def transform(*items: dict[str, Any]) -> str:
    return Transform.from_data({"caption": list(items)}).to_json()


async def begin(rig: Rig, store: Store, **options: Any) -> Run:
    request = RunRequest(batch_size=rig.batch_size, **options)
    return (await begin_run(store, rig.gw, rig.src, rig.dst, request)).run


def by_reference(rig: Rig) -> list[list[int]]:
    return [list(c.args[1]) for c in rig.gw.calls_to("send_by_reference")]  # type: ignore[call-overload]


def sample(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "just text #ads")  # 1
    rig.gw.add_message(rig.src.id, "clip #ads #keep", media=MediaKind.VIDEO)  # 2
    rig.gw.add_message(rig.src.id, "", media=MediaKind.PHOTO)  # 3: nothing to rewrite


async def test_only_the_units_a_transform_changes_leave_the_forward_road(rig: Rig) -> None:
    sample(rig)
    store = await rig.store()
    ops = transform({"remove_hashtags": ["#ads"]})

    final = await runner(rig, store).run(await begin(rig, store, mode="auto", transform_json=ops))

    assert (final.status, final.done, final.failed) == (RunStatus.DONE, 3, 0)
    assert rig.copy_calls() == [[1], [3]]  # plain text, and a photo with no caption to rewrite
    assert by_reference(rig) == [[2]]  # the captioned clip is sent again by its file id
    assert rig.dst_texts == ["just text #ads", "clip #keep", ""]


async def test_text_too_rewrites_plain_text_as_well(rig: Rig) -> None:
    sample(rig)
    store = await rig.store()
    ops = transform({"remove_hashtags": ["#ads"]}, {"text_too": True})

    await runner(rig, store).run(await begin(rig, store, mode="auto", transform_json=ops))

    assert rig.copy_calls() == [[3]]
    assert rig.dst_texts == ["just text", "clip #keep", ""]


async def test_operations_run_in_the_order_they_were_written(rig: Rig) -> None:
    sample(rig)
    store = await rig.store()
    ops = transform({"replace": {"regex": "clip", "with": "video"}}, {"append": "via B"})

    await runner(rig, store).run(await begin(rig, store, mode="auto", transform_json=ops))

    assert rig.dst_texts == ["just text #ads", "video #ads #keep\n\nvia B", ""]


async def test_a_transform_on_a_protected_source_needs_the_users_word(rig: Rig) -> None:
    """Decision D3: a transform downloads like ``--caption`` does, so it is gated the same way."""
    rig.gw.add_message(rig.src.id, "clip #ads", media=MediaKind.VIDEO)  # 1
    rig.gw.add_message(rig.src.id, "pic #ads", media=MediaKind.PHOTO)  # 2
    rig.gw.channels[rig.src.id] = replace(rig.src, noforwards=True)
    store = await rig.store()
    ops = transform({"clear": True})

    with pytest.raises(NeedsAcknowledgement):
        await begin(rig, store, mode="auto", transform_json=ops)
    run = await begin(rig, store, mode="auto", transform_json=ops, protected_ack=True)

    final = await runner(rig, store).run(run)

    assert final.status is RunStatus.DONE and run.options.src_protected
    assert by_reference(rig) == []  # never by id from a protected source
    assert rig.dst_texts == ["", ""]


def test_options_that_contradict_a_transform_are_refused_and_a_caption_flag_is_not() -> None:
    ops = transform({"clear": True})

    check_options(RunRequest(caption="none", transform_json=ops))  # a flag on top of a file: fine
    with pytest.raises(InvalidOptions) as forward_only:
        check_options(RunRequest(mode="copy", transform_json=ops))
    with pytest.raises(TransformError):
        check_options(RunRequest(transform_json='{"caption": [{"nope": 1}]}'))

    assert forward_only.value.key == "transform_needs_reupload"


async def test_the_pair_remembers_the_transform_for_the_next_run(rig: Rig) -> None:
    sample(rig)
    store = await rig.store()
    ops = transform({"remove_hashtags": "all"})
    first = await begin(rig, store, mode="auto", transform_json=ops)

    await runner(rig, store).run(first)

    mirror = await store.find_mirror(rig.src.id, rig.dst.id)
    assert mirror is not None and mirror.options.transform_json == ops
