"""Topics of a forum, changed in place: ``tgmirror topics split`` / ``merge`` / ``delete`` and the
cheap ``rename`` / ``close`` / ``reopen`` (docs/07-inspect-transform.md section 11.3, docs/06-lo-
trinh.md Phase 20b, decision D10).

Telegram has no call that moves a message to another topic (spike 4, not verified), so a move is
what ``repost`` is: every unit is downloaded and uploaded again — into the other topic — and only
then is its original deleted. A split and a merge are therefore a ``move`` operation, planned like
every operation on a chat's own messages (the ids are frozen, nothing changes until the person
confirms how many, ``engine/ops.py`` applies it write-ahead):

* **split** — the messages of one topic that the transform's routing rules name another topic for go
  to that topic (found by its title, made if there is none), in their original order; the rest stay;
* **merge** — every message of the later topics goes after the messages of the first topic, topic
  by topic in the order given, and the later topics are deleted once they are empty.

A topic is deleted only when it holds nothing but service messages (``OpRunner._drop_topics``): one
message the plan did not take keeps the topic. General is never deleted. Nothing is created while
planning — the destination topics of a split are only named here.
"""

import asyncio
import json
from collections import deque
from collections.abc import Sequence
from contextlib import aclosing
from dataclasses import dataclass

from tgmirror.core.config import Limits
from tgmirror.core.gateway import (
    GENERAL_TOPIC_ID,
    ChannelInfo,
    ChatKind,
    TelegramGateway,
    TopicInfo,
    Unit,
)
from tgmirror.core.limiter import DELETE, EDIT, Sleep
from tgmirror.engine import planner
from tgmirror.engine.flood import FloodGuard, inspect_guard
from tgmirror.engine.ops import (
    SAMPLE_EDGE,
    DeletePlan,
    NotAdmin,
    NullReporter,
    OpError,
    Reporter,
    Sample,
    _sample,
    plan_delete,
)
from tgmirror.engine.transform import Transform
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec, Rule
from tgmirror.store.db import Store
from tgmirror.store.ops import (
    DELETE_ORIGINAL,
    DROP_TOPICS,
    SILENT,
    Op,
    OpKind,
    OpSpec,
    PlannedItem,
)

TOPIC_ORDER = 10**12  # unit ids of a merge are ``topic position * TOPIC_ORDER + first message id``


class TopicError(OpError):
    """A topic operation that cannot be planned (exit code 2): said in the person's terms by the
    CLI through ``key``."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


@dataclass(frozen=True, slots=True)
class MoveSummary:
    """What a split or a merge would do, for the person to judge before confirming."""

    how: str  # ``split`` or ``merge``
    messages: int
    units: int
    albums: int
    by_dest: dict[str, int]  # messages per destination topic, in the order they are posted
    new_topics: tuple[str, ...]  # destination topics that do not exist yet: made when applied
    into_existing: tuple[str, ...]  # destinations that already hold messages: moved ones go after
    drop: tuple[str, ...]  # topics deleted once emptied
    first: tuple[Sample, ...]
    last: tuple[Sample, ...]


@dataclass(frozen=True, slots=True)
class MovePlan:
    """``op`` is ``None`` when nothing would move (no row is made)."""

    op: Op | None
    summary: MoveSummary


def folded(title: str) -> str:
    return title.strip().casefold()


def topic_named(topics: Sequence[TopicInfo], name: str) -> TopicInfo:
    """The topic the person named: its title (any case) or its id. ``TopicError`` for none, or for
    a title two topics share (it would be a guess)."""
    by_id = [t for t in topics if name.strip().isdecimal() and t.id == int(name.strip())]
    found = by_id or [t for t in topics if folded(t.title) == folded(name)]
    if not found:
        raise TopicError("topics.no_such_topic", name=name)
    if len(found) > 1:
        raise TopicError(
            "topics.ambiguous_topic", name=name, ids=", ".join(str(t.id) for t in found)
        )
    return found[0]


async def _forum(gateway: TelegramGateway, chat: ChannelInfo) -> ChannelInfo:
    """The forum as it is now: it must be one, and the account must administer it."""
    current = await gateway.get_channel(chat.id)
    if current.kind is not ChatKind.FORUM:
        raise TopicError("err.not_a_forum", title=current.title)
    if not current.is_admin:
        raise NotAdmin(current)
    return current


async def _scan(
    guard: FloodGuard, gateway: TelegramGateway, chat: ChannelInfo, topic_ids: Sequence[int]
) -> dict[int, list[Unit]]:
    """The units of ``topic_ids`` by topic, each topic in ascending order of its first message (the
    forum is read once, oldest first)."""
    spec = FilterSpec(include=(Rule(topic=tuple(topic_ids)),))
    found: dict[int, list[Unit]] = {topic_id: [] for topic_id in topic_ids}
    async with aclosing(
        planner.units(guard.reader(gateway), chat.id, matcher=Matcher(spec), complete_albums=False)
    ) as stream:
        async for item in stream:
            if isinstance(item, planner.Skip):
                continue
            found[item.topic_id or GENERAL_TOPIC_ID].append(item)
    return found


class _Plan:
    """Accumulates the frozen items and the figures of the summary."""

    def __init__(self) -> None:
        self.items: list[PlannedItem] = []
        self.units = self.albums = 0
        self.by_dest: dict[str, int] = {}
        self.first: list[Sample] = []
        self.last: deque[Sample] = deque(maxlen=SAMPLE_EDGE)

    def add(self, unit: Unit, order: int, title: str, dest_id: int | None) -> None:
        self.units += 1
        self.albums += unit.is_album
        unit_id = order * TOPIC_ORDER + unit.ids[0]
        noted = json.dumps({"dest": title, "dest_id": dest_id})
        for message in unit.messages:
            self.items.append(PlannedItem(message.id, unit_id, message.grouped_id, noted))
            self.by_dest[title] = self.by_dest.get(title, 0) + 1
            sample = _sample(message)
            if len(self.first) < SAMPLE_EDGE:
                self.first.append(sample)
            else:
                self.last.append(sample)


async def _finish(
    store: Store,
    gateway: TelegramGateway,
    guard: FloodGuard,
    forum: ChannelInfo,
    plan: _Plan,
    how: str,
    topics: Sequence[TopicInfo],
    drop: Sequence[TopicInfo],
    *,
    silent: bool,
) -> MovePlan:
    existing = {folded(t.title): t for t in topics}
    new = tuple(title for title in plan.by_dest if folded(title) not in existing)
    into: list[str] = []
    for title in plan.by_dest:
        target = existing.get(folded(title))
        if target is None or target.id == GENERAL_TOPIC_ID:
            continue
        left = await guard.read(
            "topic_message_ids",
            lambda topic_id=target.id: gateway.topic_message_ids(forum.id, topic_id, 1),
        )
        if left:
            into.append(title)
    summary = MoveSummary(
        how=how,
        messages=len(plan.items),
        units=plan.units,
        albums=plan.albums,
        by_dest=plan.by_dest,
        new_topics=new,
        into_existing=tuple(into),
        drop=tuple(t.title for t in drop),
        first=tuple(plan.first),
        last=tuple(plan.last),
    )
    if not plan.items:
        return MovePlan(None, summary)
    options = json.dumps(
        {
            DELETE_ORIGINAL: True,
            SILENT: silent,
            DROP_TOPICS: [t.id for t in drop],
            "how": how,
        },
        sort_keys=True,
    )
    op = await store.create_op(OpSpec(OpKind.MOVE, forum, "{}", options), plan.items)
    return MovePlan(op, summary)


async def plan_split(
    store: Store,
    gateway: TelegramGateway,
    chat: ChannelInfo,
    topic: TopicInfo,
    transform: Transform,
    limits: Limits,
    *,
    silent: bool = True,
    notifier: Reporter | None = None,
    nap: Sleep = asyncio.sleep,
) -> MovePlan:
    """Freeze the units of ``topic`` that the transform's routing rules send to another topic."""
    if not transform.topics.routing:
        raise TopicError("topics.split_needs_rules")
    if transform.caption or transform.rewrite_links or transform.keep_forward_header:
        raise TopicError("topics.split_only_routing")  # a move changes no text
    forum = await _forum(gateway, chat)
    guard = await inspect_guard(store, limits, notifier=notifier or NullReporter(), nap=nap)
    topics = await guard.read("list_topics", lambda: gateway.list_topics(forum.id))
    titles = {t.id: t.title for t in topics}
    units = (await _scan(guard, gateway, forum, [topic.id]))[topic.id]
    plan = _Plan()
    for unit in units:
        routed = transform.topics.route(unit, titles)
        if routed is None or folded(routed.title) == folded(topic.title):
            continue  # stays where it is
        known = next((t.id for t in topics if folded(t.title) == folded(routed.title)), None)
        plan.add(unit, 0, routed.title, known)
    return await _finish(store, gateway, guard, forum, plan, "split", topics, (), silent=silent)


async def plan_merge(
    store: Store,
    gateway: TelegramGateway,
    chat: ChannelInfo,
    names: Sequence[str],
    limits: Limits,
    *,
    silent: bool = True,
    notifier: Reporter | None = None,
    nap: Sleep = asyncio.sleep,
) -> MovePlan:
    """Freeze every unit of the later topics, topic by topic, to be posted after the first one's."""
    forum = await _forum(gateway, chat)
    guard = await inspect_guard(store, limits, notifier=notifier or NullReporter(), nap=nap)
    topics = await guard.read("list_topics", lambda: gateway.list_topics(forum.id))
    chosen = [topic_named(topics, name) for name in names]
    if len({t.id for t in chosen}) != len(chosen):
        raise TopicError("topics.merge_same_topic")
    if len(chosen) < 2:
        raise TopicError("topics.merge_needs_two")
    target, later = chosen[0], chosen[1:]
    if any(t.id == GENERAL_TOPIC_ID for t in later):
        raise TopicError("topics.merge_general")  # General cannot be deleted
    found = await _scan(guard, gateway, forum, [t.id for t in later])
    plan = _Plan()
    for order, topic in enumerate(later, start=1):
        for unit in found[topic.id]:
            plan.add(unit, order, target.title, target.id)
    return await _finish(store, gateway, guard, forum, plan, "merge", topics, later, silent=silent)


async def plan_topic_delete(
    store: Store,
    gateway: TelegramGateway,
    chat: ChannelInfo,
    name: str,
    limits: Limits,
    *,
    notifier: Reporter | None = None,
    nap: Sleep = asyncio.sleep,
) -> tuple[TopicInfo, DeletePlan]:
    """Plan deleting a topic with all its messages: an ordinary ``delete`` of the topic's messages
    that also removes the topic once it is empty. The plan holds no operation when nothing is in the
    topic (then ``drop_topic`` removes the empty shell)."""
    forum = await _forum(gateway, chat)
    guard = await inspect_guard(store, limits, notifier=notifier or NullReporter(), nap=nap)
    topics = await guard.read("list_topics", lambda: gateway.list_topics(forum.id))
    topic = topic_named(topics, name)
    if topic.id == GENERAL_TOPIC_ID:
        raise TopicError("topics.delete_general")
    spec = FilterSpec(include=(Rule(topic=(topic.id,)),))
    plan = await plan_delete(
        store,
        gateway,
        forum,
        spec,
        limits,
        notifier=notifier,
        nap=nap,
        options_json=json.dumps({DROP_TOPICS: [topic.id]}),
    )
    return topic, plan


async def find_topic(
    store: Store,
    gateway: TelegramGateway,
    chat: ChannelInfo,
    name: str,
    limits: Limits,
    *,
    nap: Sleep = asyncio.sleep,
) -> tuple[ChannelInfo, TopicInfo]:
    """The forum (administered) and the topic ``name`` stands for, before a one-shot change."""
    forum = await _forum(gateway, chat)
    guard = await inspect_guard(store, limits, notifier=NullReporter(), nap=nap)
    topics = await guard.read("list_topics", lambda: gateway.list_topics(forum.id))
    return forum, topic_named(topics, name)


async def change_topic(
    store: Store,
    gateway: TelegramGateway,
    forum: ChannelInfo,
    topic: TopicInfo,
    limits: Limits,
    *,
    title: str | None = None,
    closed: bool | None = None,
    nap: Sleep = asyncio.sleep,
) -> None:
    """Rename, close or reopen a topic (nothing in it changes). One write, paced and retried by
    ``FloodGuard`` like every other (hard rule 1); General has no such change."""
    if topic.id == GENERAL_TOPIC_ID:
        raise TopicError("topics.edit_general")
    guard = await inspect_guard(store, limits, notifier=NullReporter(), nap=nap)
    await guard.pace(1, action=EDIT)
    await guard.write(
        "edit_topic",
        1,
        lambda: gateway.edit_topic(forum.id, topic.id, title=title, closed=closed),
    )


async def drop_topic(
    store: Store,
    gateway: TelegramGateway,
    forum: ChannelInfo,
    topic: TopicInfo,
    limits: Limits,
    *,
    nap: Sleep = asyncio.sleep,
) -> None:
    """Delete a topic that no plan found any message in. It is read once more first: a message
    posted since keeps it (``TopicError``)."""
    if topic.id == GENERAL_TOPIC_ID:
        raise TopicError("topics.delete_general")
    guard = await inspect_guard(store, limits, notifier=NullReporter(), nap=nap)
    left = await guard.read(
        "topic_message_ids", lambda: gateway.topic_message_ids(forum.id, topic.id, 1)
    )
    if left:
        raise TopicError("topics.not_empty", name=topic.title)
    await guard.pace(1, action=DELETE)
    await guard.write("delete_topic", 1, lambda: gateway.delete_topic(forum.id, topic.id))
