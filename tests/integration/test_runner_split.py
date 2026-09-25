"""Strategy B with the bytes uploaded *before* the post (docs/04, docs/05).

A unit goes: bytes up (no message exists, nothing pending) -> wait until the gap since the last post
is long enough, the time the bytes took counting towards it -> write-ahead -> post -> commit. What
this file pins down: the order, that the upload leaves no pending row behind, that its time counts
against the pace, that a FloodWait on the post does not upload again, and that the daily cap is
checked before any bytes are spent. The last tests run the real Telethon gateway with its request
pool on a stub client, under the real runner and store.
"""

import sqlite3
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import LIMITS, Crash, Rig
from tests.integration.test_runner_reupload import begin, rows, runner
from tests.unit.test_telethon_pool import POOL, PoolStub, big_video
from tests.unit.test_telethon_reupload import NOW, message
from tgmirror.core import telethon_gateway
from tgmirror.core.errors import DailyCapReached, FloodWait
from tgmirror.core.gateway import MediaKind
from tgmirror.core.telethon_gateway import TelethonGateway
from tgmirror.store.runs import RunStatus

Events = list[tuple[str, Any]]

# reads sleep too (the next unit is fetched meanwhile): make them negligible next to the pace
FAST_READS = LIMITS.model_copy(update={"read_delay": 0.001})
SLACK = 0.05


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    r.tmp = tmp_path / "scratch"  # type: ignore[attr-defined]
    yield r
    for store in r._stores:
        await store.close()


def files_in(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file()]


def pending_now(rig: Rig) -> int:
    with sqlite3.connect(rig.path) as db:
        return int(
            db.execute("SELECT COUNT(*) FROM msg_map WHERE status = 'pending'").fetchone()[0]
        )


def watch(rig: Rig, *, uploading: float = 0.0) -> Events:
    """Log ``upload``, ``sleep`` and ``post`` in the order they happen, with the number of rows that
    are pending at that moment. ``uploading`` seconds pass on the fake clock during each upload."""
    events: Events = []
    upload, post, sleep = rig.gw.upload_prepared, rig.gw.send_prepared, rig.sleep

    async def uploading_(prepared: Any, on_transfer: Any = None) -> Any:
        events.append(("upload", pending_now(rig)))
        rig._now += uploading
        return await upload(prepared, on_transfer)

    async def posting(
        dst: int, prepared: Any, caption: Any, on_transfer: Any = None, *, topic: int | None = None
    ) -> Any:
        events.append(("post", pending_now(rig)))
        return await post(dst, prepared, caption, on_transfer, topic=topic)

    async def sleeping(seconds: float) -> None:
        events.append(("sleep", seconds))
        await sleep(seconds)

    rig.gw.upload_prepared = uploading_  # type: ignore[method-assign]
    rig.gw.send_prepared = posting  # type: ignore[method-assign]
    rig.sleep = sleeping  # type: ignore[method-assign]
    return events


def clips(rig: Rig, count: int = 3) -> None:
    for i in range(count):
        rig.gw.add_message(rig.src.id, f"clip{i}", media=MediaKind.VIDEO, size=1000)


def kinds(events: Events) -> list[str]:
    return [kind for kind, _ in events if kind != "sleep"]


def paced(events: Events, unit: int) -> float:
    """The pace slept between the upload and the post of the ``unit``-th unit (0 is the first)."""
    uploads = [i for i, (kind, _) in enumerate(events) if kind == "upload"]
    posts = [i for i, (kind, _) in enumerate(events) if kind == "post"]
    return sum(s for kind, s in events[uploads[unit] : posts[unit]] if kind == "sleep")


# ---- the order and what is pending --------------------------------------------------------------


async def test_the_bytes_go_up_first_with_nothing_pending_and_only_the_post_is_pending(
    rig: Rig,
) -> None:
    clips(rig)
    events = watch(rig)
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.done == 3
    assert kinds(events) == ["upload", "post"] * 3
    uploads = [pending for kind, pending in events if kind == "upload"]
    posts = [pending for kind, pending in events if kind == "post"]
    assert uploads == [0, 0, 0]  # nothing to reconcile if the run dies while bytes go up
    assert posts == [1, 1, 1]  # exactly the unit being posted


async def test_a_kill_while_the_bytes_go_up_leaves_nothing_to_reconcile(rig: Rig) -> None:
    clips(rig, 2)
    store = await rig.store()
    original = rig.gw.upload_prepared
    calls = 0

    async def dies_during_the_second_upload(prepared: Any, on_transfer: Any = None) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Crash
        return await original(prepared, on_transfer)

    rig.gw.upload_prepared = dies_during_the_second_upload  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await runner(rig, store).run(await begin(rig, store))
    assert [r[1] for r in rows(rig)] == ["done"]  # no pending row for the unit that was uploading
    rig.gw.upload_prepared = original  # type: ignore[method-assign]

    resumed = await runner(rig, store).run(await begin(rig, store, force=True))

    assert rig.dst_texts == ["clip0", "clip1"] and resumed.status is RunStatus.DONE
    assert "reconciled" not in rig.recorder.codes and "reconcile_resend" not in rig.recorder.codes


# ---- the pace ------------------------------------------------------------------------------------


async def test_the_time_the_bytes_took_counts_towards_the_gap_before_the_post(rig: Rig) -> None:
    clips(rig, 3)
    events = watch(rig, uploading=0.5)  # 0.5 of the 2 s delay is spent while uploading
    store = await rig.store()

    await runner(rig, store, limits=FAST_READS).run(await begin(rig, store))

    assert paced(events, 0) == pytest.approx(0, abs=SLACK)  # the first write is never held
    assert paced(events, 1) == pytest.approx(1.5, abs=SLACK)
    assert paced(events, 2) == pytest.approx(1.5, abs=SLACK)


async def test_an_upload_longer_than_the_delay_leaves_no_wait_at_all(rig: Rig) -> None:
    clips(rig, 3)
    events = watch(rig, uploading=5.0)
    store = await rig.store()

    await runner(rig, store, limits=FAST_READS).run(await begin(rig, store))

    assert [paced(events, n) for n in range(3)] == pytest.approx([0, 0, 0], abs=SLACK)


async def test_without_upload_time_the_gap_is_the_whole_delay_as_before(rig: Rig) -> None:
    clips(rig, 2)
    events = watch(rig, uploading=0.0)
    store = await rig.store()

    await runner(rig, store, limits=FAST_READS).run(await begin(rig, store))

    assert paced(events, 1) == pytest.approx(LIMITS.min_delay, abs=SLACK)


async def test_the_time_of_an_upload_never_shortens_a_long_pause(rig: Rig) -> None:
    clips(rig, 4)
    events = watch(rig, uploading=9.0)
    store = await rig.store()
    limits = FAST_READS.model_copy(update={"long_pause_every": 2, "long_pause_range": (30.0, 30.0)})

    await runner(rig, store, limits=limits).run(await begin(rig, store))

    # the delay is covered by the upload, the rest the run must take is not
    assert [paced(events, n) for n in range(4)] == pytest.approx([0, 0, 30.0, 0], abs=SLACK)


# ---- the daily cap, flood and repeats ---------------------------------------------------


async def test_a_unit_that_cannot_be_posted_today_is_not_uploaded(rig: Rig) -> None:
    clips(rig, 3)
    rig.batch_size = 1
    store = await rig.store()
    limits = LIMITS.model_copy(update={"daily_cap": 1})

    with pytest.raises(DailyCapReached):
        await runner(rig, store, limits=limits).run(await begin(rig, store))

    assert len(rig.gw.calls_to("upload_prepared")) == 1  # the second unit's bytes were never sent
    assert [r[1] for r in rows(rig)] == ["done"]


async def test_a_flood_wait_while_the_bytes_go_up_is_sat_out_and_repeated(rig: Rig) -> None:
    clips(rig, 1)
    rig.gw.fail_next("upload_prepared", FloodWait(5))
    events = watch(rig)
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.done == 1 and sum(s for k, s in events if k == "sleep") >= 5
    assert (
        len(rig.gw.calls_to("upload_prepared")) == 2 and len(rig.gw.calls_to("send_prepared")) == 1
    )
    assert [pending for kind, pending in events if kind == "upload"] == [0, 0]


async def test_a_flood_wait_on_the_post_does_not_upload_the_file_again(rig: Rig) -> None:
    clips(rig, 1)
    rig.gw.fail_next("send_prepared", FloodWait(5))
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.done == 1
    assert len(rig.gw.calls_to("send_prepared")) == 2  # the post is repeated
    assert len(rig.gw.calls_to("upload_prepared")) == 1  # the bytes are not


async def test_what_cannot_be_uploaded_ahead_takes_the_old_way(rig: Rig) -> None:
    """A unit with no file (a text, a poll re-created) has nothing to upload before its post."""
    rig.gw.add_message(rig.src.id, "just words")
    events = watch(rig)
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.done == 1 and rig.dst_texts == ["just words"]
    assert kinds(events) == ["upload", "post"]  # the call is made, and does nothing


# ---- the real gateway, its request pool and the runner together --------------------------


class Hybrid:
    """The FakeGateway for everything but strategy B's file half, which is the real
    ``TelethonGateway`` (request pool included) over a stub client."""

    def __init__(self, fake: Any, real: TelethonGateway) -> None:
        self._fake = fake
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fake, name)

    async def prepare(self, src: int, unit: Any, tmp: Path, on_transfer: Any = None) -> Any:
        return await self._real.prepare(src, unit, tmp, on_transfer)

    async def upload_prepared(self, prepared: Any, on_transfer: Any = None) -> Any:
        return await self._real.upload_prepared(prepared, on_transfer)

    async def send_prepared(
        self, dst: int, prepared: Any, caption: Any, on_transfer: Any = None, *, topic: Any = None
    ) -> Any:
        return await self._real.send_prepared(dst, prepared, caption, on_transfer, topic=topic)


@pytest.fixture
def small_big_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telethon_gateway, "BIG_FILE", 1024 * 1024)


def pooled_rig(rig: Rig, sizes: list[int]) -> tuple[PoolStub, Events, Callable[[], None]]:
    """Source messages of these sizes in the fake and, with the same ids, in the stub client."""
    import os

    blob = os.urandom(max(sizes))
    messages = []
    for i, size in enumerate(sizes, start=1):
        rig.gw.add_message(rig.src.id, f"clip{i}", media=MediaKind.VIDEO, size=size)
        messages.append(
            message(i, media=big_video(size)).__class__(
                id=i,
                peer_id=message(i).peer_id,
                date=NOW,
                message=f"clip{i}",
                media=big_video(size),
            )
        )
    stub = PoolStub(*messages, blob=blob)
    real = TelethonGateway(stub, POOL, sleep=rig.sleep)  # type: ignore[arg-type]
    fake = rig.gw
    rig.gw = Hybrid(fake, real)  # type: ignore[assignment]
    events: Events = []
    original_call, original_send = stub._call, stub.send_file

    async def call(sender: Any, request: Any) -> Any:
        events.append((type(request).__name__, pending_now(rig)))
        return await original_call(sender, request)

    async def send(peer: Any, file: Any, **kw: Any) -> Any:
        events.append(("post", pending_now(rig)))
        return await original_send(peer, file, **kw)

    stub._call = call  # type: ignore[method-assign]
    stub.send_file = send  # type: ignore[method-assign]
    return stub, events, lambda: None


@pytest.mark.usefixtures("small_big_files")
async def test_the_whole_path_with_the_pool_uploads_first_then_paces_then_posts(
    rig: Rig, tmp_path: Path
) -> None:
    sizes = [2 * 1024 * 1024 + 5] * 3
    stub, events, _ = pooled_rig(rig, sizes)
    order: Events = []
    sleeping = rig.sleep

    async def note_sleep(seconds: float) -> None:
        order.append(("sleep", seconds))
        await sleeping(seconds)

    rig.sleep = note_sleep  # type: ignore[method-assign]
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert (final.status, final.done, final.failed) == (RunStatus.DONE, 3, 0)
    saves = [pending for name, pending in events if name == "SaveBigFilePartRequest"]
    posts = [pending for name, pending in events if name == "post"]
    assert saves and set(saves) == {0}  # while bytes go up, no row is pending
    assert posts == [1, 1, 1]  # while a unit is posted, exactly its row is
    names = [name for name, _ in events]
    first_post = names.index("post")
    assert "SaveBigFilePartRequest" in names[:first_post]  # the bytes were up before the post
    # every part of every file went through the pool, and the files came down through it too
    assert len(stub.downloads) == 0  # Telethon's own download was never used
    assert files_in(rig.tmp) == []  # type: ignore[attr-defined]
    assert [r[1] for r in rows(rig)] == ["done"] * 3
    uploads = [t for t in rig.recorder.transfers if t.phase.value == "upload"]
    assert uploads and uploads[-1].done == uploads[-1].total


@pytest.mark.usefixtures("small_big_files")
async def test_the_whole_path_repeats_only_the_post_after_a_flood_wait(rig: Rig) -> None:
    from telethon import errors

    stub, events, _ = pooled_rig(rig, [2 * 1024 * 1024])
    original = stub.send_file
    failed = False

    async def flood_once(peer: Any, file: Any, **kw: Any) -> Any:
        nonlocal failed
        if not failed:
            failed = True
            raise errors.FloodWaitError(None, capture=5)
        return await original(peer, file, **kw)

    stub.send_file = flood_once  # type: ignore[method-assign]
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.done == 1
    parts = [name for name, _ in events if name == "SaveBigFilePartRequest"]
    assert len(parts) == 4  # 2 MiB in 512 KiB parts, once: the file did not go up again


async def test_a_transport_flood_while_fetching_is_logged_apart_and_sat_out(rig: Rig) -> None:
    """The first real run: HTTP 429 on the connection during a big download. It is a FloodWait
    the run sits out and repeats (``flood_log`` says what it was), not a failed run."""
    clips(rig, 1)
    rig.gw.fail_next("prepare", FloodWait(60, transport=True))
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.status is RunStatus.DONE and final.done == 1
    assert len(rig.gw.calls_to("prepare")) == 2  # the fetch was repeated after the wait
    with sqlite3.connect(rig.path) as db:
        logged = db.execute("SELECT kind, seconds, method FROM flood_log").fetchall()
    assert logged == [("transport_429", 60, "prepare")]
