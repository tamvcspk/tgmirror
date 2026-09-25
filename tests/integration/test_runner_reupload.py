"""Strategy B end to end on ``FakeGateway`` and a real SQLite file: no network.

What phase 6 is judged by (docs/06-lo-trinh.md): a unit is downloaded and sent again on its own
(an album whole, in order), the next one is downloaded while this one uploads, the files are gone
afterwards, what cannot be copied is never left out silently, and a run that is killed, refused
or stopped in the middle loses and duplicates nothing.
"""

import asyncio
import random
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_runner import LIMITS, Crash, Rig
from tgmirror.core.config import Limits
from tgmirror.core.errors import FileRefExpired, FloodWait, PerMessage
from tgmirror.core.gateway import CaptionMode, CaptionPolicy, MediaKind
from tgmirror.engine.endpoints import SourceRestricted
from tgmirror.engine.reupload import UnsupportedMedia
from tgmirror.engine.runner import RunControl, Runner, RunnerTiming
from tgmirror.engine.runs import NeedsAcknowledgement, RunRequest, begin_run
from tgmirror.store.db import Store, utc_now
from tgmirror.store.runs import Run, RunStatus


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    r.tmp = tmp_path / "scratch"  # type: ignore[attr-defined]
    yield r
    for store in r._stores:
        await store.close()


def runner(
    rig: Rig,
    store: Store,
    *,
    limits: Limits = LIMITS,
    control: RunControl | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> Runner:
    return Runner(
        store,
        rig.gw,
        limits,
        reporter=rig.recorder,
        control=control,
        sleep=sleep or rig.sleep,
        rng=random.Random(0),
        timing=RunnerTiming(poll_interval=0.2, heartbeat_interval=3600),
        tmp_dir=rig.tmp,  # type: ignore[attr-defined]
        mono=rig.mono,
    )


async def begin(rig: Rig, store: Store, **options: Any) -> Run:
    request = RunRequest(batch_size=rig.batch_size, **{"mode": "reupload", **options})
    return (await begin_run(store, rig.gw, rig.src, rig.dst, request)).run


def protect(rig: Rig) -> None:
    """The source restricts saving content: a forward is refused, only reupload can copy."""
    rig.gw.channels[rig.src.id] = replace(rig.src, noforwards=True)


def leftovers(rig: Rig) -> list[Path]:
    root: Path = rig.tmp  # type: ignore[attr-defined]
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def rows(rig: Rig) -> list[tuple[int, str, int | None, str | None]]:
    with sqlite3.connect(rig.path) as db:
        return db.execute(
            "SELECT src_msg_id, status, dst_msg_id, reason FROM msg_map ORDER BY src_msg_id"
        ).fetchall()


def sent_units(rig: Rig) -> list[list[int]]:
    return [list(c.args[1]) for c in rig.gw.calls_to("send_prepared")]  # type: ignore[call-overload]


def by_reference(rig: Rig) -> list[list[int]]:
    return [list(c.args[1]) for c in rig.gw.calls_to("send_by_reference")]  # type: ignore[call-overload]


# ---- decision D3 at the start of every run ----------------------------------------------------


async def test_a_run_that_can_download_reads_the_source_again_and_needs_the_users_word(
    rig: Rig,
) -> None:
    rig.fill(1)
    store = await rig.store()
    protect(rig)  # the owner turned "Restrict saving content" on; the user administers the source

    with pytest.raises(NeedsAcknowledgement):
        await begin(rig, store)
    with pytest.raises(NeedsAcknowledgement):  # a caption change downloads too
        await begin(rig, store, mode="auto", caption="none")
    assert rig.gw.calls_to("get_channel") != [] and saved_runs_count(rig) == 0

    assert (await begin(rig, store, protected_ack=True)).options.protected_ack


async def test_a_protected_source_this_account_does_not_administer_needs_the_statement(
    rig: Rig,
) -> None:
    rig.fill(1)
    store = await rig.store()
    rig.gw.channels[rig.src.id] = replace(rig.src, noforwards=True, is_admin=False)

    with pytest.raises(SourceRestricted):
        await begin(rig, store)
    assert saved_runs_count(rig) == 0

    # with the user's statement (they own it through another account) the run starts:
    # the responsibility is theirs
    assert (await begin(rig, store, protected_ack=True)).options.protected_ack


async def test_a_run_that_only_forwards_does_not_read_the_source_again(rig: Rig) -> None:
    rig.fill(1)
    store = await rig.store()

    await begin(rig, store, mode="auto")

    assert rig.gw.calls_to("get_channel") == []


def saved_runs_count(rig: Rig) -> int:
    with sqlite3.connect(rig.path) as db:
        return int(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0])


# ---- what a reupload run does ---------------------------------------------------------------


async def test_every_unit_is_sent_on_its_own_and_nothing_is_forwarded(rig: Rig) -> None:
    protect(rig)  # a forward would be refused: this only works because nothing is forwarded
    gw = rig.gw
    rig.fill(3)
    gw.add_album(rig.src.id, [MediaKind.PHOTO] * 3, caption="album")  # 4 5 6
    rig.fill(2, start=7)
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store, protected_ack=True))

    assert final.status is RunStatus.DONE and final.stats == {"done": 8, "failed": 0}
    assert gw.calls_to("copy_messages") == []
    assert sent_units(rig) == [[1], [2], [3], [4, 5, 6], [7], [8]]
    assert rig.dst_texts == ["m1", "m2", "m3", "album", "", "", "m7", "m8"]
    groups = [m.grouped_id for m in gw.messages[rig.dst.id]]
    assert groups[3] == groups[4] == groups[5] is not None  # the album is still one album
    assert groups[0] is groups[1] is groups[2] is groups[6] is groups[7] is None
    assert final.cursor_src_id == 8


async def test_the_files_are_kept_until_the_unit_is_sent_and_gone_afterwards(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "clip", media=MediaKind.VIDEO, size=10)
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO] * 2)
    store = await rig.store()
    seen: list[int] = []
    original = rig.gw.send_prepared

    async def watching(
        dst: int,
        prepared: Any,
        caption: CaptionPolicy,
        on_transfer: Any = None,
        *,
        topic: int | None = None,
    ) -> list[int]:
        seen.append(
            len(leftovers(rig))
        )  # the fake also refuses to send a unit whose files are gone
        return await original(dst, prepared, caption, on_transfer, topic=topic)

    rig.gw.send_prepared = watching  # type: ignore[method-assign]

    await runner(rig, store).run(await begin(rig, store))

    assert seen and all(n >= 1 for n in seen)
    assert leftovers(rig) == []


async def test_a_text_message_needs_no_download_and_goes_out_as_text(rig: Rig) -> None:
    rig.fill(2)
    store = await rig.store()

    await runner(rig, store).run(await begin(rig, store))

    prepared = [c.args[2] for c in rig.gw.calls_to("prepare")]
    assert len(prepared) == 2 and leftovers(rig) == []
    assert rig.dst_texts == ["m1", "m2"]


async def test_the_run_can_be_continued_delta_style_and_only_new_messages_are_sent(
    rig: Rig,
) -> None:
    rig.fill(2)
    store = await rig.store()
    await runner(rig, store).run(await begin(rig, store))
    rig.fill(2, start=3)

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.stats == {"done": 2, "failed": 0}
    assert rig.dst_texts == ["m1", "m2", "m3", "m4"]


# ---- sending by the files' ids ---------------------------------------------------------------


def captioned_media(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "plain")  # 1: forwarded
    rig.gw.add_message(rig.src.id, "look", media=MediaKind.VIDEO, size=5_000_000_000)  # 2
    rig.gw.add_album(rig.src.id, [MediaKind.VIDEO] * 3, caption="album")  # 3 4 5


async def test_a_captioned_file_goes_by_its_id_without_touching_the_disk(rig: Rig) -> None:
    captioned_media(rig)
    store = await rig.store()

    final = await runner(rig, store).run(
        await begin(rig, store, mode="auto", caption="append", caption_text="via X")
    )

    assert (final.status, final.done, final.failed) == (RunStatus.DONE, 5, 0)
    assert by_reference(rig) == [[2], [3, 4, 5]] and rig.copy_calls() == [[1]]
    assert rig.gw.calls_to("prepare") == [] and sent_units(rig) == []
    assert leftovers(rig) == [] and not rig.tmp.exists()  # type: ignore[attr-defined]
    assert rig.dst_texts == ["plain", "look\n\nvia X", "album\n\nvia X", "", ""]
    albums = [m.grouped_id for m in rig.gw.messages[rig.dst.id][2:]]
    assert len(set(albums)) == 1 and albums[0] is not None  # one album at the destination
    assert not rig.recorder.transfers  # nothing to report: nothing was transferred


async def test_a_source_that_restricts_saving_content_never_sends_by_id(rig: Rig) -> None:
    """Decision D3: even a run the user vouched for downloads and uploads, it does not reuse ids."""
    rig.gw.add_message(rig.src.id, "look", media=MediaKind.VIDEO)  # 1
    rig.gw.add_album(rig.src.id, [MediaKind.VIDEO] * 3, caption="album")  # 2 3 4
    protect(rig)
    store = await rig.store()

    run = await begin(rig, store, mode="auto", caption="none", protected_ack=True)
    await runner(rig, store).run(run)

    assert run.options.src_protected
    assert by_reference(rig) == [] and rig.gw.calls_to("prepare") != []
    assert sent_units(rig) == [[1], [2, 3, 4]] and rig.copy_calls() == []


async def test_a_run_that_can_not_download_does_not_read_the_source_or_mark_it_protected(
    rig: Rig,
) -> None:
    rig.fill(2)
    store = await rig.store()

    run = (await begin_run(store, rig.gw, rig.src, rig.dst, RunRequest())).run

    assert not run.options.src_protected and rig.gw.calls_to("get_channel") == []


async def test_reupload_mode_still_downloads_even_where_ids_could_be_reused(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "look", media=MediaKind.PHOTO)
    store = await rig.store()

    await runner(rig, store).run(await begin(rig, store))  # mode reupload: what the user asked

    assert by_reference(rig) == [] and sent_units(rig) == [[1]]


async def test_a_stale_reference_is_refreshed_once_and_the_unit_goes_on_by_id(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "look", media=MediaKind.PHOTO)
    rig.gw.fail_next("send_by_reference", FileRefExpired("FILE_REFERENCE_EXPIRED"))
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store, mode="auto", caption="none"))

    assert final.done == 1 and rig.dst_texts == [""]
    assert len(rig.gw.calls_to("send_by_reference")) == 2
    assert len(rig.gw.calls_to("fetch")) == 2  # once before, once to refresh the reference
    assert rig.gw.calls_to("prepare") == [] and "reference_fallback" not in rig.recorder.codes


async def test_media_telegram_will_not_reuse_goes_the_long_way(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "look", media=MediaKind.PHOTO)
    rig.gw.add_message(rig.src.id, "next", media=MediaKind.PHOTO)
    rig.gw.fail_next("send_by_reference", FileRefExpired("MEDIA_EMPTY"), times=2)  # first unit
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store, mode="auto", caption="none"))

    assert (final.done, final.failed) == (2, 0) and rig.dst_texts == ["", ""]
    assert sent_units(rig) == [[1]]  # downloaded and uploaded, as reupload mode would
    assert by_reference(rig) == [[1], [1], [2]]  # the next unit is tried by id again
    assert "reference_fallback" in rig.recorder.codes and leftovers(rig) == []


async def test_a_unit_gone_from_the_source_fails_alone_when_it_is_read_again(rig: Rig) -> None:
    for name in ("a", "b", "c"):
        rig.gw.add_message(rig.src.id, name, media=MediaKind.PHOTO)
    rig.gw.fail_next("fetch", PerMessage("gone_from_source"))  # the first unit
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store, mode="auto", caption="none"))

    assert (final.done, final.failed) == (2, 1)
    assert [(r[0], r[1], r[3]) for r in rows(rig)] == [
        (1, "failed", "gone_from_source"),
        (2, "done", None),
        (3, "done", None),
    ]


async def test_killed_after_sending_by_id_the_next_run_finds_it_and_sends_nothing_twice(
    rig: Rig,
) -> None:
    rig.gw.add_message(rig.src.id, "a", media=MediaKind.PHOTO)
    rig.gw.add_message(rig.src.id, "b", media=MediaKind.PHOTO)
    store = await rig.store()
    original = rig.gw.send_by_reference
    calls = 0

    async def dies_after_the_second_one(
        dst: int, prepared: Any, caption: Any, *, topic: int | None = None
    ) -> list[int]:
        nonlocal calls
        calls += 1
        ids = await original(dst, prepared, caption, topic=topic)
        if calls == 2:
            raise Crash  # Telegram made the message, tgmirror never heard
        return ids

    rig.gw.send_by_reference = dies_after_the_second_one  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await runner(rig, store).run(await begin(rig, store, mode="auto", caption="none"))
    rig.gw.send_by_reference = original  # type: ignore[method-assign]

    resumed = await runner(rig, store).run(
        await begin(rig, store, mode="auto", caption="none", force=True)
    )

    assert rig.dst_texts == ["", ""]  # not three
    assert resumed.status is RunStatus.DONE and "reconciled" in rig.recorder.codes
    assert [r[1] for r in rows(rig)] == ["done", "done"]


# ---- downloading ahead ----------------------------------------------------------------------


def watch_order(rig: Rig) -> list[tuple[str, int]]:
    order: list[tuple[str, int]] = []
    prepare, send = rig.gw.prepare, rig.gw.send_prepared

    async def prepare_(src: int, unit: Any, tmp: Path, on_transfer: Any = None) -> Any:
        order.append(("prepare", unit.ids[0]))
        return await prepare(src, unit, tmp, on_transfer)

    async def send_(
        dst: int,
        prepared: Any,
        caption: CaptionPolicy,
        on_transfer: Any = None,
        *,
        topic: int | None = None,
    ) -> list[int]:
        order.append(("send", prepared.unit.ids[0]))
        await asyncio.sleep(0.05)  # an upload takes time: the pipeline has a chance to look ahead
        ids = await send(dst, prepared, caption, on_transfer, topic=topic)
        order.append(("sent", prepared.unit.ids[0]))
        return ids

    rig.gw.prepare = prepare_  # type: ignore[method-assign]
    rig.gw.send_prepared = send_  # type: ignore[method-assign]
    return order


async def test_the_next_unit_is_downloaded_while_this_one_uploads(rig: Rig) -> None:
    for i in range(3):
        rig.gw.add_message(rig.src.id, f"clip{i}", media=MediaKind.VIDEO, size=10)
    order = watch_order(rig)
    store = await rig.store()

    await runner(rig, store, limits=LIMITS.model_copy(update={"prefetch": 1})).run(
        await begin(rig, store)
    )

    # 2 was fetched during 1's upload, but 3 not before 1 was out of the way: two on disk at most
    assert order.index(("prepare", 2)) < order.index(("sent", 1)) < order.index(("prepare", 3))
    assert [e for e in order if e[0] == "sent"] == [("sent", 1), ("sent", 2), ("sent", 3)]
    assert leftovers(rig) == []


async def test_without_prefetch_each_unit_is_downloaded_after_the_previous_one_is_sent(
    rig: Rig,
) -> None:
    for i in range(3):
        rig.gw.add_message(rig.src.id, f"clip{i}", media=MediaKind.VIDEO, size=10)
    order = watch_order(rig)
    store = await rig.store()

    await runner(rig, store, limits=LIMITS.model_copy(update={"prefetch": 0})).run(
        await begin(rig, store)
    )

    assert [e for e in order if e[0] != "send"] == [
        ("prepare", 1),
        ("sent", 1),
        ("prepare", 2),
        ("sent", 2),
        ("prepare", 3),
        ("sent", 3),
    ]


async def test_the_disk_budget_stops_the_lookahead(rig: Rig) -> None:
    for i in range(3):
        rig.gw.add_message(rig.src.id, f"clip{i}", media=MediaKind.VIDEO, size=600_000)
    order = watch_order(rig)
    store = await rig.store()
    limits = LIMITS.model_copy(update={"prefetch": 3, "tmp_budget_mb": 1})  # two do not fit

    await runner(rig, store, limits=limits).run(await begin(rig, store))

    assert [kind for kind, _ in order if kind != "send"] == ["prepare", "sent"] * 3


async def test_a_file_without_a_size_still_counts_against_the_budget(rig: Rig) -> None:
    for i in range(3):
        rig.gw.add_message(rig.src.id, f"clip{i}", media=MediaKind.VIDEO)  # Telegram gave no size
    order = watch_order(rig)
    store = await rig.store()
    limits = LIMITS.model_copy(update={"prefetch": 3, "tmp_budget_mb": 1})

    await runner(rig, store, limits=limits).run(await begin(rig, store))

    # each is reserved as 1 MiB, the whole budget: never two on disk at once
    assert [kind for kind, _ in order if kind != "send"] == ["prepare", "sent"] * 3


async def test_a_file_bigger_than_telegram_said_holds_the_next_download_back(rig: Rig) -> None:
    for i in range(3):
        rig.gw.add_message(rig.src.id, f"clip{i}", media=MediaKind.VIDEO, size=10)  # a small lie
    original = rig.gw.prepare

    async def fat(src: int, unit: Any, tmp: Path, on_transfer: Any = None) -> Any:
        prepared = await original(src, unit, tmp, on_transfer)
        for path in prepared.files:
            path.write_bytes(b"x" * 2_000_000)  # what is really on disk
        return prepared

    rig.gw.prepare = fat  # type: ignore[method-assign]
    order = watch_order(rig)
    store = await rig.store()
    limits = LIMITS.model_copy(update={"prefetch": 3, "tmp_budget_mb": 1})

    await runner(rig, store, limits=limits).run(await begin(rig, store))

    assert [kind for kind, _ in order if kind != "send"] == ["prepare", "sent"] * 3


async def test_the_transfers_are_reported_as_they_go(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "clip", media=MediaKind.VIDEO, size=1000)
    store = await rig.store()

    await runner(rig, store).run(await begin(rig, store))

    seen = [(t.phase.value, t.msg_id, t.done, t.total) for t in rig.recorder.transfers]
    assert seen == [
        ("download", 1, 0, 1000),
        ("download", 1, 1000, 1000),
        ("upload", 1, 0, 1000),
        ("upload", 1, 1000, 1000),
    ]


async def test_a_stop_is_noticed_while_the_next_unit_is_still_downloading(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "clip", media=MediaKind.VIDEO, size=10)
    store = await rig.store()
    started = asyncio.Event()

    async def never_finishes(src: int, unit: Any, tmp: Path, on_transfer: Any = None) -> Any:
        started.set()
        await asyncio.Event().wait()

    rig.gw.prepare = never_finishes  # type: ignore[method-assign]
    control = RunControl()
    task = asyncio.create_task(runner(rig, store, control=control).run(await begin(rig, store)))
    await asyncio.wait_for(started.wait(), 2)

    control.request_stop()
    final = await asyncio.wait_for(task, 5)

    assert final.status is RunStatus.STOPPED and rows(rig) == []
    assert leftovers(rig) == []


# ---- captions: the reason a run in auto mode uploads at all --------------------------------------


async def test_in_auto_mode_only_captioned_media_is_sent_again_and_order_is_kept(
    rig: Rig,
) -> None:
    gw = rig.gw
    gw.add_message(rig.src.id, "plain")  # 1
    gw.add_message(rig.src.id, "look", media=MediaKind.PHOTO)  # 2: a caption
    gw.add_message(rig.src.id, "plain too")  # 3
    gw.add_message(rig.src.id, "", media=MediaKind.PHOTO)  # 4: media without a caption
    gw.add_album(rig.src.id, [MediaKind.PHOTO] * 2, caption="album")  # 5 6
    gw.add_message(rig.src.id, "end")  # 7
    store = await rig.store()

    final = await runner(rig, store).run(
        await begin(rig, store, mode="auto", caption="append", caption_text="via X")
    )

    assert final.stats == {"done": 7, "failed": 0}
    assert by_reference(rig) == [[2], [5, 6]]  # the rest went the cheap way
    assert rig.copy_calls() == [[1], [3, 4], [7]]
    assert rig.dst_texts == ["plain", "look\n\nvia X", "plain too", "", "album\n\nvia X", "", "end"]
    assert rig.gw.calls_to("send_by_reference")[0].args[2] == CaptionPolicy(
        CaptionMode.APPEND, "via X"
    )
    # the source lets content be saved: no file went down or up
    assert rig.gw.calls_to("prepare") == [] and sent_units(rig) == []


async def test_caption_none_removes_captions_only_from_media(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "words")
    rig.gw.add_message(rig.src.id, "caption", media=MediaKind.VIDEO)
    store = await rig.store()

    await runner(rig, store).run(await begin(rig, store, mode="auto", caption="none"))

    assert rig.dst_texts == ["words", ""]


async def test_a_keep_caption_auto_run_never_uploads_and_never_creates_scratch_space(
    rig: Rig,
) -> None:
    rig.gw.add_message(rig.src.id, "look", media=MediaKind.PHOTO)
    store = await rig.store()

    await runner(rig, store).run(await begin(rig, store, mode="auto"))

    assert rig.gw.calls_to("prepare") == [] and rig.copy_calls() == [[1]]


# ---- what cannot be copied ------------------------------------------------------------------


def dst_kinds(rig: Rig) -> list[MediaKind]:
    return [m.media for m in rig.gw.messages[rig.dst.id]]


async def test_a_poll_is_left_out_with_a_warning_unless_polls_may_be_reset(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "before")
    rig.gw.add_message(rig.src.id, "", media=MediaKind.POLL, title="Best colour?")
    rig.gw.add_message(rig.src.id, "after")
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.stats == {"done": 2, "failed": 0, "skipped_unsupported": 1}
    assert rows(rig)[1] == (2, "skipped", None, "unsupported:poll")
    assert rig.dst_texts == ["before", "after"] and final.cursor_src_id == 3
    assert ("skipped_unsupported", {"id": 2, "reason": "unsupported:poll"}) in rig.recorder.notices


async def test_polls_are_recreated_with_reset_polls(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "", media=MediaKind.POLL, title="Best colour?")
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store, reset_polls=True))

    assert final.stats == {"done": 1, "failed": 0} and dst_kinds(rig) == [MediaKind.POLL]


async def test_a_game_stops_the_run_unless_told_to_leave_it_out(rig: Rig) -> None:
    rig.fill(2)
    rig.gw.add_message(rig.src.id, "", media=MediaKind.GAME, title="Chess")  # 3
    rig.fill(1, start=4)
    store = await rig.store()
    run = await begin(rig, store)

    with pytest.raises(UnsupportedMedia) as caught:
        await runner(rig, store).run(run)

    assert (caught.value.kind, caught.value.msg_id) == ("game", 3)
    stopped = await store.get_run(run.id)
    assert stopped is not None and stopped.status is RunStatus.FAILED
    assert stopped.fail_reason is not None and "UnsupportedMedia" in stopped.fail_reason
    assert rig.dst_texts == ["m1", "m2"] and stopped.cursor_src_id == 2  # progress kept
    assert [r[1] for r in rows(rig)] == ["done", "done"]  # nothing recorded for the game
    assert leftovers(rig) == []


async def test_ignore_unsupported_leaves_it_out_and_the_run_goes_on(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "", media=MediaKind.INVOICE, title="Shop")
    rig.fill(1, start=2)
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store, ignore_unsupported=True))

    assert final.status is RunStatus.DONE
    assert final.stats == {"done": 1, "failed": 0, "skipped_unsupported": 1}
    assert rows(rig)[0] == (1, "skipped", None, "unsupported:invoice")
    assert rig.dst_texts == ["m2"]


async def test_a_placeholder_stands_in_for_what_was_left_out(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "", media=MediaKind.GAME, title="Chess")
    rig.gw.add_message(rig.src.id, "", media=MediaKind.POLL, quiz_unanswered=True, title="2+2?")
    store = await rig.store()

    final = await runner(rig, store).run(
        await begin(rig, store, placeholder=True, reset_polls=True)
    )

    assert final.stats == {"done": 0, "failed": 0, "skipped_unsupported": 2}
    assert rig.dst_texts == [
        "[Game: Chess — không thể sao chép]",
        "[Quiz: 2+2? — không thể sao chép]",
    ]
    assert rows(rig) == [
        (1, "skipped", 1, "unsupported:game"),  # the row remembers the note that took its place
        (2, "skipped", 2, "unsupported:quiz"),
    ]
    assert [c.method for c in rig.gw.calls if c.method == "send_text"] == ["send_text"] * 2


async def test_a_dropped_poll_gets_no_placeholder(rig: Rig) -> None:
    """Leaving a poll out is the user's choice (no --reset-polls), not a limit of Telegram."""
    rig.gw.add_message(rig.src.id, "", media=MediaKind.POLL, title="Best colour?")
    store = await rig.store()

    await runner(rig, store).run(await begin(rig, store, placeholder=True))

    assert rig.gw.calls_to("send_text") == [] and rig.dst_texts == []


async def test_an_unanswered_quiz_is_unsupported_even_when_polls_may_be_reset(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "", media=MediaKind.POLL, quiz_unanswered=True, title="2+2?")
    store = await rig.store()

    with pytest.raises(UnsupportedMedia) as caught:
        await runner(rig, store).run(await begin(rig, store, reset_polls=True))

    assert caught.value.kind == "quiz"


# ---- what goes wrong on the way -------------------------------------------------------------


async def test_a_message_deleted_before_it_is_fetched_fails_and_the_run_goes_on(rig: Rig) -> None:
    rig.fill(3)
    rig.gw.fail_next("prepare", PerMessage("gone_from_source"))  # the first fetch
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.status is RunStatus.DONE and final.stats == {"done": 2, "failed": 1}
    assert rows(rig)[0] == (1, "failed", None, "gone_from_source")
    assert rig.dst_texts == ["m2", "m3"]


async def test_a_flood_while_sending_repeats_the_call_with_the_same_files(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "clip", media=MediaKind.VIDEO, size=10)
    rig.gw.fail_next("send_prepared", FloodWait(20))
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.stats == {"done": 1, "failed": 0} and rig.dst_texts == ["clip"]
    assert len(rig.gw.calls_to("send_prepared")) == 2  # refused once, then accepted
    assert len(rig.gw.calls_to("prepare")) == 1  # no second download for the second try
    assert sum(rig.delays) >= 20  # it waited the flood out
    assert leftovers(rig) == []


async def test_a_flood_while_fetching_is_waited_out_and_the_fetch_repeated(rig: Rig) -> None:
    rig.fill(2)
    rig.gw.fail_next("prepare", FloodWait(7))
    store = await rig.store()

    final = await runner(rig, store).run(await begin(rig, store))

    assert final.stats == {"done": 2, "failed": 0} and rig.dst_texts == ["m1", "m2"]
    assert sum(rig.delays) >= 7


async def test_a_flood_that_is_too_long_parks_the_run_and_forgets_nothing(rig: Rig) -> None:
    rig.fill(3)
    rig.gw.fail_next("send_prepared", FloodWait(10_000), times=1)
    store = await rig.store()
    run = await begin(rig, store)

    with pytest.raises(FloodWait):
        await runner(rig, store).run(run)

    parked = await store.get_run(run.id)
    assert parked is not None and parked.status is RunStatus.WAITING_FLOOD
    assert rows(rig) == [] and rig.dst_texts == [] and leftovers(rig) == []


# ---- killed in the middle -------------------------------------------------------------------


async def test_killed_after_the_upload_the_next_run_finds_it_and_sends_nothing_twice(
    rig: Rig,
) -> None:
    rig.gw.add_message(rig.src.id, "a", media=MediaKind.PHOTO)
    rig.gw.add_message(rig.src.id, "b", media=MediaKind.PHOTO)
    store = await rig.store()
    original = rig.gw.send_prepared
    calls = 0

    async def dies_after_the_second_upload(
        dst: int, prepared: Any, caption: Any, on_transfer: Any = None, *, topic: int | None = None
    ) -> list[int]:
        nonlocal calls
        calls += 1
        ids = await original(dst, prepared, caption, on_transfer, topic=topic)
        if calls == 2:
            raise Crash  # Telegram made the message, tgmirror never heard
        return ids

    rig.gw.send_prepared = dies_after_the_second_upload  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await runner(rig, store).run(await begin(rig, store))
    rig.gw.send_prepared = original  # type: ignore[method-assign]

    resumed = await runner(rig, store).run(await begin(rig, store, force=True))

    assert rig.dst_texts == ["a", "b"]  # not "a", "b", "b"
    assert resumed.status is RunStatus.DONE and "reconciled" in rig.recorder.codes
    assert [r[1] for r in rows(rig)] == ["done", "done"]


async def test_killed_before_the_upload_the_next_run_sends_the_unit_once(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "a", media=MediaKind.PHOTO)
    store = await rig.store()
    original = rig.gw.send_prepared

    async def dies_first(
        dst: int, prepared: Any, caption: Any, on_transfer: Any = None, *, topic: int | None = None
    ) -> list[int]:
        raise Crash

    rig.gw.send_prepared = dies_first  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await runner(rig, store).run(await begin(rig, store))
    rig.gw.send_prepared = original  # type: ignore[method-assign]

    await runner(rig, store).run(await begin(rig, store, force=True))

    assert rig.dst_texts == ["a"] and [r[1] for r in rows(rig)] == ["done"]


async def test_downloads_a_killed_run_left_behind_are_removed_by_the_next_one(rig: Rig) -> None:
    stale = rig.tmp / "run-99" / "1234"  # type: ignore[attr-defined]
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"left over from a run that was killed")
    rig.fill(1)
    store = await rig.store()
    there_when_fetching: list[bool] = []
    original = rig.gw.prepare

    async def looking(src: int, unit: Any, tmp: Path, on_transfer: Any = None) -> Any:
        there_when_fetching.append(stale.exists())  # is it still on disk while the run works?
        return await original(src, unit, tmp, on_transfer)

    rig.gw.prepare = looking  # type: ignore[method-assign]

    await runner(rig, store).run(await begin(rig, store))

    # cleared when the run starts, not merely when it ends: until then it would sit on the disk
    # next to the run's own downloads, outside the ``tmp_budget_mb`` the window keeps
    assert there_when_fetching == [False]
    assert not stale.exists()


# ---- retry ----------------------------------------------------------------------------------


async def test_a_retry_of_a_reupload_run_uploads_again_what_failed(rig: Rig) -> None:
    rig.fill(3)
    rig.gw.fail_next("send_prepared", PerMessage("MEDIA_INVALID"))  # the first unit is refused
    store = await rig.store()
    first = await runner(rig, store).run(await begin(rig, store))
    assert first.stats == {"done": 2, "failed": 1}

    request = RunRequest(batch_size=3, mode="reupload", retry_of=first.id)
    retry = (await begin_run(store, rig.gw, rig.src, rig.dst, request)).run
    final = await runner(rig, store).run(retry)

    assert final.stats == {"done": 1, "failed": 0}
    assert sent_units(rig)[-1] == [1] and rig.dst_texts == ["m2", "m3", "m1"]
    assert utc_now() >= final.started_at
