"""Phase 8: routing a forum unit into the right destination topic.

``engine/runner.py`` calls ``TopicResolver.resolve`` right before sending a batch whose units
carry a source topic id (``Batch.topic_id is not None``; a forum's General topic has no
``reply_to`` at all in Telethon, so it never reaches here — it is sent with no topic, which a
forum destination defaults to General on its own, unverified on a real account, docs/06-lo-trinh.md
open question 9).

A destination that is itself a forum gets the topic mapped (created the first time it is seen,
then remembered in ``topic_map``); any other destination gets ``RunOptions.topic_as_hashtag``'s
fallback instead — a hashtag standing in for the topic name, appended where there is a slot for
it (``core/gateway.py::CaptionPolicy.hashtag``, docs/01-kien-truc.md "Đích khác loại nguồn").
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tgmirror.core.gateway import ChatKind, MessageReader
from tgmirror.store.db import Store
from tgmirror.store.runs import Run


@dataclass(frozen=True, slots=True)
class TopicRoute:
    """Where a unit should land: a destination topic id, or a hashtag standing in for one."""

    dst_topic_id: int | None = None
    hashtag: str | None = None


def _hashtag_of(title: str) -> str:
    """A hashtag Telegram will actually parse (word characters only, no spaces) from an arbitrary
    topic title. Not ``filters.model.normalize_hashtag``: that one validates a user-typed filter
    value and rejects anything with whitespace, which most topic titles have."""
    slug = re.sub(r"\W+", "_", title, flags=re.UNICODE).strip("_").casefold()
    return f"#{slug}" if slug else "#topic"


class TopicResolver:
    """Caches the pair's ``topic_map`` and the source's topic titles for one run, so a run with
    several new topics makes at most one ``list_topics`` call and one ``create_topic`` call per
    topic it actually meets.

    ``create`` is a title -> destination topic id call already wrapped in ``FloodGuard.write`` by
    the caller (``engine/runner.py``), so this class never touches the gateway directly (hard
    rule 1, enforced by ``tests/unit/test_architecture.py``)."""

    def __init__(
        self,
        reader: MessageReader,
        store: Store,
        run: Run,
        create: Callable[[str], Awaitable[int]],
    ) -> None:
        self._reader = reader
        self._store = store
        self._run = run
        self._create = create
        self._map: dict[int, int] | None = None
        self._titles: dict[int, str] | None = None

    async def resolve(self, src_topic_id: int) -> TopicRoute:
        if self._run.dst_kind is not ChatKind.FORUM:
            hashtag = None
            if self._run.options.topic_as_hashtag:
                hashtag = _hashtag_of(await self._title_of(src_topic_id))
            return TopicRoute(hashtag=hashtag)
        if self._map is None:
            self._map = dict(await self._store.topic_map(self._run.id))
        if src_topic_id in self._map:
            return TopicRoute(self._map[src_topic_id])
        title = await self._title_of(src_topic_id)
        dst_topic_id = await self._create(title)
        # The create call and this save happen back to back, nothing in between: a crash right
        # here just means the next run's resolve recreates the topic once more (rule 5 forbids a
        # real transaction spanning the Telegram call; docs/01-kien-truc.md, "Ánh xạ topic").
        await self._store.save_topic(self._run.id, src_topic_id, dst_topic_id, title)
        self._map[src_topic_id] = dst_topic_id
        return TopicRoute(dst_topic_id)

    async def _title_of(self, src_topic_id: int) -> str:
        if self._titles is None:
            topics = await self._reader.list_topics(self._run.src_id)
            self._titles = {t.id: t.title for t in topics}
        return self._titles.get(src_topic_id, f"Topic {src_topic_id}")
