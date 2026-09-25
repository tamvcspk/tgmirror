"""Pushdown versus a full scan (docs/06-lo-trinh.md, phase 3 done-criterion).

For many random channels (singles and albums, hashtags, dates) and many filters, reading through
``plan_read`` must yield exactly the units a full scan plus the client matcher yields: pushdown is
an optimisation and may never lose a message or split an album (hard rules 4 and "narrow safely").
"""

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.gateway import NO_FILTER, MediaKind, Unit
from tgmirror.engine import planner
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import plan_read

START = datetime(2024, 1, 1, tzinfo=UTC)
KINDS = [MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.AUDIO, MediaKind.STICKER, MediaKind.DOCUMENT]
TAGS = ["#a", "#b", "#c"]
WORDS = ["giveaway", "news", "ads", "hello"]

SPECS: dict[str, dict[str, Any]] = {
    "photos": {"include": [{"media": ["photo"]}]},
    "videos, album all": {"include": [{"media": ["video"]}], "album": "all"},
    "photos, album first": {"include": [{"media": ["photo"]}], "album": "first"},
    "hashtag": {"include": [{"hashtag": ["#a"]}]},
    "hashtag, album first": {"include": [{"hashtag": ["#a"]}], "album": "first"},
    "hashtag and media": {"include": [{"hashtag": ["#b"], "media": ["photo"]}]},
    "two hashtags": {"include": [{"hashtag": ["#a", "#b"]}]},
    "keyword": {"include": [{"contains": ["news"]}]},
    "two rules": {"include": [{"media": ["video"]}, {"hashtag": ["#c"]}]},
    "photos without ads": {
        "include": [{"media": ["photo"]}],
        "exclude": [{"contains": ["ads"]}],
    },
    "since": {"date": {"from": "2024-01-11"}},
    "until": {"date": {"to": "2024-01-21"}},
    "date range": {"date": {"from": "2024-01-08", "to": "2024-01-22"}},
    "id range": {"id": {"from": 40, "to": 190}},
    "date and hashtag": {"include": [{"hashtag": ["#a"]}], "date": {"from": "2024-01-10"}},
    "id range and photos": {"include": [{"media": ["photo"]}], "id": {"from": 33, "to": 150}},
    "everything": {},
    "topic": {"include": [{"topic": [1, 2]}]},
    "from_user": {"include": [{"from_user": [100]}]},
}


def channel(seed: int) -> tuple[FakeGateway, int]:
    """A channel where every unit (single or album) is dated one 6 hours after the previous."""
    rng = random.Random(seed)
    gw = FakeGateway()
    src = gw.add_channel("Source").id
    for index in range(90):
        when = START + timedelta(hours=6 * index)
        tags = tuple(t for t in TAGS if rng.random() < 0.3)
        text = " ".join([rng.choice(WORDS), *tags])
        topic = rng.choice([1, 2, 3])  # a whole unit shares one topic, like a real forum album
        sender = rng.choice([100, 200, None])
        if rng.random() < 0.35:  # an album: the caption and its hashtags sit on the first member
            size = rng.randint(2, 6)
            gid = gw._alloc_group()
            for i in range(size):
                gw.add_message(
                    src,
                    text if i == 0 else "",
                    media=rng.choice(KINDS[:3]),
                    grouped_id=gid,
                    hashtags=tags if i == 0 else (),
                    date=when,
                    topic_id=topic,
                    from_user_id=sender,
                )
        else:
            gw.add_message(
                src,
                text,
                media=rng.choice([*KINDS, MediaKind.TEXT]),
                hashtags=tags,
                date=when,
                topic_id=topic,
                from_user_id=sender,
            )
        if rng.random() < 0.05:
            gw.add_message(src, is_service=True, date=when)
    return gw, src


async def units_of(gw: FakeGateway, src: int, spec: FilterSpec, *, pushdown: bool) -> list[Unit]:
    plan = plan_read(spec, 0, pushdown=pushdown)
    stream = planner.units(
        gw,
        src,
        min_id=plan.min_id,
        filters=plan.server,
        matcher=Matcher(spec),
        complete_albums=plan.complete_albums,
    )
    return [item async for item in stream if isinstance(item, Unit)]


async def messages_read(gw: FakeGateway, src: int, spec: FilterSpec) -> int:
    plan = plan_read(spec, 0)
    return len([m async for m in gw.iter_messages(src, min_id=plan.min_id, filters=plan.server)])


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("name", SPECS)
async def test_pushdown_yields_exactly_what_a_full_scan_yields(name: str, seed: int) -> None:
    gw, src = channel(seed)
    spec = FilterSpec.from_data(SPECS[name])

    pushed = await units_of(gw, src, spec, pushdown=True)
    scanned = await units_of(gw, src, spec, pushdown=False)

    assert [u.ids for u in pushed] == [u.ids for u in scanned]


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("name", SPECS)
async def test_no_unit_is_ever_split(name: str, seed: int) -> None:
    gw, src = channel(seed)
    whole = {}
    for m in gw.messages[src]:
        if m.grouped_id is not None:
            whole.setdefault(m.grouped_id, []).append(m.id)
    spec = FilterSpec.from_data(SPECS[name])

    for unit in await units_of(gw, src, spec, pushdown=True):
        if unit.is_album:
            assert unit.ids == whole[unit.grouped_id]


@pytest.mark.parametrize(
    "name", ["photos", "hashtag", "since", "until", "date range", "id range", "id range and photos"]
)
async def test_pushdown_reads_fewer_messages_than_a_full_scan(name: str) -> None:
    gw, src = channel(1)
    spec = FilterSpec.from_data(SPECS[name])

    assert await messages_read(gw, src, spec) < len(gw.messages[src])


async def test_the_full_scan_reads_everything() -> None:
    gw, src = channel(1)
    plan = plan_read(FilterSpec.from_data(SPECS["photos"]), 0, pushdown=False)

    assert plan.server == NO_FILTER
    assert len([m async for m in gw.iter_messages(src, min_id=plan.min_id)]) == len(
        gw.messages[src]
    )
