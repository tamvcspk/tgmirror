"""Telethon boundary: the total of a range (``count``) and the progress of downloads and uploads
(``on_transfer``). No network: a stub client records what the gateway asks of it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telethon import errors, types
from telethon.tl.functions.messages import SearchRequest

from tests.unit.test_telethon_messages import HistoryClient, gateway_on
from tests.unit.test_telethon_messages import message as history_message
from tests.unit.test_telethon_reupload import (
    KEEP,
    VIDEO,
    document,
    gateway,
    message,
    photo,
    unit_of,
)
from tgmirror.core.errors import FloodWait, NoPermission
from tgmirror.core.gateway import MediaKind, ServerFilter, TransferPhase

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DOWN, UP = TransferPhase.DOWNLOAD, TransferPhase.UPLOAD


class CountingClient(HistoryClient):
    """``HistoryClient`` that answers ``messages.search`` the way Telegram does (for a limit of 1):
    the total, the newest message older than ``offset_id``, and where that message stands in the
    whole list (``offset_id_offset``). ``min_id``/``max_id`` are ignored for the total, as seen on
    a real channel."""

    def __init__(self, ids: list[int] | None = None, *, says_position: bool = True) -> None:
        super().__init__()
        self.ids = sorted(range(1, 121) if ids is None else ids)
        self.says_position = says_position
        self.requests: list[Any] = []
        self.raises: BaseException | None = None
        self.total: int | None = None  # set to answer without a count, like a plain ``Messages``

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if self.raises:
            raise self.raises
        older = [i for i in reversed(self.ids) if not request.offset_id or i < request.offset_id]
        answer = SimpleNamespace(count=len(self.ids), messages=older[: request.limit])
        if self.total == -1:
            del answer.count
        if self.says_position and request.offset_id and answer.messages:
            above = sum(1 for i in self.ids if i >= request.offset_id)
            if above:  # a zero is left out, as an optional field of the answer would be
                answer.offset_id_offset = above
        return answer


async def count(client: CountingClient, min_id: int = 0, **filters: Any) -> int:
    return await gateway_on(client).count(-1001, min_id=min_id, filters=ServerFilter(**filters))


# ---- count ------------------------------------------------------------------------------------


async def test_the_total_of_a_channel_is_one_search_that_asks_for_no_narrowing() -> None:
    client = CountingClient()

    assert await count(client) == 120

    (request,) = client.requests
    assert isinstance(request, SearchRequest)
    assert request.peer == "peer:-1001" and request.q == ""
    assert isinstance(request.filter, types.InputMessagesFilterEmpty)
    assert (request.offset_id, request.limit) == (0, 1)
    assert client.iter_calls == [] and client.lookups == []  # nothing was read


async def test_a_range_is_counted_from_where_telegram_says_the_messages_stand() -> None:
    """Telegram gives the whole total whatever ``min_id`` says; the position of the first message
    older than an id says how many are newer, and two positions bound a range exactly."""
    ids = [i for i in range(1, 115) if i % 3 != 0]  # deleted messages leave gaps in the ids
    client = CountingClient(ids)

    assert await count(client, min_id=57) == len([i for i in ids if i > 57])
    assert await count(client, max_id=57) == len([i for i in ids if i <= 57])
    assert await count(client, min_id=20, max_id=90) == len([i for i in ids if 20 < i <= 90])
    assert {r.offset_id for r in client.requests} == {0, 58, 21, 91}
    assert all((r.min_id, r.max_id) == (0, 0) for r in client.requests)  # ignored anyway


async def test_the_narrowing_goes_with_every_request_of_the_count() -> None:
    client = CountingClient()

    await count(client, min_id=40, max_id=100, media=MediaKind.PHOTO, search="#k")

    assert len(client.requests) == 3
    for request in client.requests:
        assert isinstance(request.filter, types.InputMessagesFilterPhotos) and request.q == "#k"


async def test_a_range_that_starts_below_the_oldest_message_counts_everything() -> None:
    client = CountingClient([50, 60, 70])

    assert await count(client, min_id=10) == 3  # nothing is older than 11: all are newer


async def test_a_range_that_starts_past_the_newest_message_is_nothing_more_than_the_total() -> None:
    """Telegram leaves ``offset_id_offset`` out here: it does not say, so the total stays the answer
    (the runner then caps it by the id span, which is 0 for a run with nothing new)."""
    client = CountingClient([50, 60, 70])

    assert await count(client, min_id=500) == 3


async def test_a_telegram_that_gives_no_positions_leaves_the_total_as_the_upper_bound() -> None:
    client = CountingClient(says_position=False)

    assert await count(client, min_id=57) == 120


async def test_a_since_date_becomes_a_position_without_the_albums_margin() -> None:
    client = CountingClient()
    client.after = [[history_message(80)]]
    since = datetime(2024, 1, 1, tzinfo=UTC)

    assert await count(client, since=since) == 41  # ids 80..120: from the first message at the date

    (lookup,) = client.lookups
    assert lookup == {"limit": 1, "offset_date": since - timedelta(seconds=1), "reverse": True}
    assert {r.offset_id for r in client.requests} == {0, 80}  # up to the first message at the date


async def test_an_until_date_excludes_the_first_message_that_is_too_late() -> None:
    client = CountingClient()
    client.after = [[history_message(100)]]

    assert await count(client, until=NOW) == 99  # ids below 100 are before the date

    assert {r.offset_id for r in client.requests} == {0, 100}


async def test_nothing_after_the_since_date_is_a_count_of_zero_without_asking() -> None:
    client = CountingClient()
    client.after = [[]]

    assert await count(client, since=NOW) == 0
    assert client.requests == []


async def test_an_empty_range_is_zero_without_asking() -> None:
    client = CountingClient()

    assert await count(client, min_id=50, max_id=50) == 0
    assert client.requests == []


async def test_an_answer_without_a_total_is_counted_by_its_messages() -> None:
    client = CountingClient()
    client.total = -1  # answer without ``count``

    assert await count(client) == 1  # the one message asked for


async def test_the_errors_of_the_count_are_mapped_like_any_other() -> None:
    client = CountingClient()
    client.raises = errors.FloodWaitError(None, capture=7)
    with pytest.raises(FloodWait):
        await count(client)

    unknown = CountingClient()
    unknown.unknown = {-1001}
    with pytest.raises(NoPermission):
        await count(unknown)


# ---- progress of a download -------------------------------------------------------------------


def collector() -> tuple[list[tuple[TransferPhase, int, int, int]], Any]:
    events: list[tuple[TransferPhase, int, int, int]] = []

    def on_transfer(phase: TransferPhase, msg_id: int, done: int, total: int) -> None:
        events.append((phase, msg_id, done, total))

    return events, on_transfer


async def test_a_download_reports_bytes_under_the_message_id(tmp_path: Path) -> None:
    clip = message(4, media=document(VIDEO))
    gw, _ = gateway(clip)
    events, on_transfer = collector()

    await gw.prepare(1, unit_of(clip), tmp_path, on_transfer)

    assert events == [(DOWN, 4, 2, 4), (DOWN, 4, 4, 4)]  # what the stub client's callback gave


async def test_a_callback_that_raises_never_fails_the_transfer(tmp_path: Path) -> None:
    clip = message(4, media=document(VIDEO))
    gw, _ = gateway(clip)

    def broken(phase: TransferPhase, msg_id: int, done: int, total: int) -> None:
        raise RuntimeError("the display is gone")

    prepared = await gw.prepare(1, unit_of(clip), tmp_path, broken)

    assert len(prepared.files) == 1


# ---- progress of an upload --------------------------------------------------------------------


async def test_an_upload_hands_telethon_a_callback_only_when_asked_to(tmp_path: Path) -> None:
    clip = message(4, media=document(VIDEO))
    gw, stub = gateway(clip)
    prepared = await gw.prepare(1, unit_of(clip), tmp_path)

    await gw.send_prepared(2, prepared, KEEP)
    assert "progress_callback" not in stub.sent[0][2]

    events, on_transfer = collector()
    await gw.send_prepared(2, prepared, KEEP, on_transfer)
    stub.sent[1][2]["progress_callback"](512, 1024)  # Telethon: (sent, total)
    assert events == [(UP, 4, 512, 1024)]


async def test_a_single_photo_reports_too(tmp_path: Path) -> None:
    pic = message(3, media=photo())
    gw, stub = gateway(pic)
    events, on_transfer = collector()
    prepared = await gw.prepare(1, unit_of(pic), tmp_path)

    await gw.send_prepared(2, prepared, KEEP, on_transfer)
    stub.sent[0][2]["progress_callback"](10, 10)

    assert events == [(UP, 3, 10, 10)]


async def test_an_album_reports_as_a_whole_under_its_first_message(tmp_path: Path) -> None:
    """Each member uploads (and reports) on its own; the album folds that into one running byte
    total under the album's first message id, not the member's own."""
    members = [
        message(7, media=document(mime="application/pdf", size=4), grouped_id=9),
        message(8, media=document(mime="application/pdf", size=4), grouped_id=9),
    ]
    gw, _ = gateway(*members)
    events, on_transfer = collector()
    prepared = await gw.prepare(1, unit_of(*members), tmp_path)

    await gw.send_prepared(2, prepared, KEEP, on_transfer)  # the stub's files are 4 bytes each

    assert events == [(UP, 7, 4, 8), (UP, 7, 8, 8)]  # 8 = the declared size of both members
