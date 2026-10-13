"""Phase 8: routing a unit into the right destination topic; phase 19b: by hashtag, and by name.

``engine/runner.py`` calls ``TopicResolver.resolve`` right before sending a batch that carries a
source topic id (``Batch.topic_id is not None``; a forum's General topic has no ``reply_to`` at
all in Telethon, so it never reaches here — it is sent with no topic, which a forum destination
defaults to General on its own, unverified on a real account, docs/06-lo-trinh.md open question
9) or the title of a destination topic a hashtag rule chose (``Batch.dst_topic``, phase 19b).

A destination that is itself a forum gets the topic mapped (created the first time it is seen,
then remembered in ``topic_map``); any other destination gets ``RunOptions.topic_as_hashtag``'s
fallback instead — a hashtag standing in for the topic name, appended where there is a slot for
it (``core/gateway.py::CaptionPolicy.hashtag``, docs/01-kien-truc.md "Đích khác loại nguồn").

A destination topic is found **by its title**: the topics the destination already has are read
once per run, so a topic that exists (created by an earlier run, a crash that never recorded it,
another source cloned into the same forum, or the user) is reused instead of created again, and
two source topics ``topic_map`` gives one name end up in one topic. ``topic_map`` (the table)
only remembers which destination topic a source topic id went to.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tgmirror.core.gateway import GENERAL_TOPIC_ID, ChatKind, MessageReader
from tgmirror.engine.transform import Transform
from tgmirror.store.db import Store
from tgmirror.store.runs import Run


@dataclass(frozen=True, slots=True)
class TopicRoute:
    """Where a unit should land: a destination topic id, or a hashtag standing in for one."""

    dst_topic_id: int | None = None
    hashtag: str | None = None


def _topic_route(dst_topic_id: int) -> TopicRoute:
    # General is the topic a post without a topic lands in: no id to send it to
    return TopicRoute(None if dst_topic_id == GENERAL_TOPIC_ID else dst_topic_id)


class TopicResolver:
    """Caches the pair's ``topic_map``, the source's topic titles and the destination's topics for
    one run, so a run with several new topics makes at most one ``list_topics`` call per side and
    one ``create_topic`` call per topic it actually has to create.

    ``create`` is a title -> destination topic id call already wrapped in ``FloodGuard.write`` by
    the caller (``engine/runner.py``), so this class never touches the gateway directly (hard
    rule 1, enforced by ``tests/unit/test_architecture.py``). ``dst_reader`` reads the
    destination's topics (live Telegram, even for a restore whose source is a backup directory)."""

    def __init__(
        self,
        reader: MessageReader,
        store: Store,
        run: Run,
        create: Callable[[str], Awaitable[int]],
        *,
        dst_reader: MessageReader | None = None,
        transform: Transform | None = None,
        on_limit: Callable[[int], None] | None = None,
    ) -> None:
        self._reader = reader
        self._dst_reader = dst_reader or reader
        self._store = store
        self._run = run
        self._create = create
        self._rules = (transform or Transform.from_json(run.options.transform_json)).topics
        self._map: dict[int, int] | None = None
        self._titles: dict[int, str] | None = None
        self._dst_ids: dict[str, int] | None = None  # destination topic id by case-folded title
        # ``auto: all`` names topics after hashtags nobody wrote down: a guard on how many a run
        # may create (``on_limit`` is told once, with the limit, when it holds one back)
        self._named = {r.topic.strip().casefold() for r in self._rules.routes}
        if self._rules.default:
            self._named.add(self._rules.default.strip().casefold())
        self._auto_created = 0
        self._on_limit = on_limit
        self._limit_told = False

    async def resolve(self, src_topic_id: int | None, dst_title: str | None = None) -> TopicRoute:
        """``dst_title`` is the topic a hashtag rule chose; it wins over ``src_topic_id``."""
        if dst_title is not None:  # needs a forum: ``begin_run`` refused any other destination
            automatic = self._rules.auto_all and dst_title.strip().casefold() not in self._named
            found = await self._topic_titled(dst_title, automatic=automatic)
            # past the guard the message goes where it would with no rule: General
            return TopicRoute() if found is None else _topic_route(found)
        if src_topic_id is None:
            return TopicRoute()
        if self._run.dst_kind is not ChatKind.FORUM:
            hashtag = None
            if self._run.options.topic_as_hashtag:
                title = await self._title_of(src_topic_id)
                hashtag = self._rules.hashtag_of(self._rules.rename(src_topic_id, title))
            return TopicRoute(hashtag=hashtag)
        if self._map is None:
            self._map = dict(await self._store.topic_map(self._run.id))
        if src_topic_id in self._map:
            return _topic_route(self._map[src_topic_id])
        title = self._rules.rename(src_topic_id, await self._title_of(src_topic_id))
        dst_topic_id = await self._topic_titled(title)
        assert dst_topic_id is not None  # only an automatic topic can be held back
        # The create call and this save happen back to back, nothing in between: a crash right
        # here just means the next run finds the topic by its title (rule 5 forbids a real
        # transaction spanning the Telegram call; docs/01-kien-truc.md, "Ánh xạ topic").
        await self._store.save_topic(self._run.id, src_topic_id, dst_topic_id, title)
        self._map[src_topic_id] = dst_topic_id
        return _topic_route(dst_topic_id)

    async def _title_of(self, src_topic_id: int) -> str:
        if self._titles is None:
            topics = await self._reader.list_topics(self._run.src_id)
            self._titles = {t.id: t.title for t in topics}
        return self._titles.get(src_topic_id, f"Topic {src_topic_id}")

    async def _topic_titled(self, title: str, *, automatic: bool = False) -> int | None:
        """The destination topic called ``title``: one that is there, else a new one. ``None``
        for an ``automatic`` topic (named after a hashtag by ``auto: all``) that the run's limit
        on new ones holds back."""
        if self._dst_ids is None:
            topics = await self._dst_reader.list_topics(self._run.dst_id)
            self._dst_ids = {}
            for topic in topics:  # the first of two with one title is the one reused
                self._dst_ids.setdefault(topic.title.strip().casefold(), topic.id)
        key = title.strip().casefold()
        found = self._dst_ids.get(key)
        if found is None:
            if automatic and self._auto_created >= self._rules.auto_limit:
                if not self._limit_told and self._on_limit is not None:
                    self._limit_told = True
                    self._on_limit(self._rules.auto_limit)
                return None
            found = self._dst_ids[key] = await self._create(title)
            if automatic:
                self._auto_created += 1
        return found
