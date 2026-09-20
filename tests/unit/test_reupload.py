"""Strategy B's building blocks without Telegram or SQLite: which strategy a unit takes, what to
do with what cannot be copied, and the window/pipeline that downloads ahead."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tgmirror.core.gateway import CaptionMode, MediaKind, Prepared, SrcMessage, Unit
from tgmirror.engine.batcher import Batch, batches
from tgmirror.engine.planner import Skip
from tgmirror.engine.reupload import (
    UNKNOWN_SIZE,
    ActionKind,
    Options,
    Pipeline,
    Ready,
    UnsupportedMedia,
    Window,
    left_out,
    placeholder_text,
    plan_unit,
    reserve_size,
)
from tgmirror.engine.runs import InvalidOptions, ModeUnsupported, RunRequest, check_options
from tgmirror.engine.strategy import Strategy, has_caption, has_files, router
from tgmirror.store.msgmap import MessageResult
from tgmirror.store.runs import RunOptions

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def msg(i: int, text: str = "", media: MediaKind = MediaKind.TEXT, **kw: object) -> SrcMessage:
    return SrcMessage(id=i, date=NOW, text=text, media=media, **kw)  # type: ignore[arg-type]


def unit(*messages: SrcMessage) -> Unit:
    return Unit(tuple(messages))


def album(first: int, count: int, caption: str = "") -> Unit:
    return unit(
        *(
            msg(first + i, caption if i == 0 else "", MediaKind.PHOTO, grouped_id=7)
            for i in range(count)
        )
    )


def test_a_captioned_unit_of_files_goes_by_id_when_the_source_allows_saving_content() -> None:
    route = router("auto", CaptionMode.NONE, by_reference=True)

    assert route(unit(msg(1, "look", MediaKind.VIDEO))) is Strategy.REFERENCE
    assert route(album(1, 3, "album")) is Strategy.REFERENCE
    assert route(unit(msg(2, "words"))) is Strategy.COPY  # no caption, nothing to rewrite
    assert route(unit(msg(3, "", MediaKind.PHOTO))) is Strategy.COPY


def test_a_captioned_unit_that_is_not_a_file_is_still_downloaded_or_rebuilt() -> None:
    route = router("auto", CaptionMode.NONE, by_reference=True)

    assert route(unit(msg(1, "where", MediaKind.GEO))) is Strategy.REUPLOAD  # no file to reuse
    assert route(unit(msg(2, "q?", MediaKind.POLL))) is Strategy.REUPLOAD


def test_without_the_sources_leave_a_captioned_unit_is_downloaded() -> None:
    assert router("auto", CaptionMode.NONE)(unit(msg(1, "look", MediaKind.VIDEO))) is (
        Strategy.REUPLOAD
    )


def test_the_users_own_mode_is_never_second_guessed() -> None:
    plain = unit(msg(1, "look", MediaKind.VIDEO))

    assert router("reupload", CaptionMode.KEEP, by_reference=True)(plain) is Strategy.REUPLOAD
    assert router("copy", CaptionMode.KEEP, by_reference=True)(plain) is Strategy.COPY
    assert router("auto", CaptionMode.KEEP, by_reference=True)(plain) is Strategy.COPY


def test_what_can_be_sent_by_id_is_a_unit_made_of_files_only() -> None:
    assert has_files(unit(msg(1, "", MediaKind.DOCUMENT)))
    assert has_files(album(1, 2))
    assert not has_files(unit(msg(2, "words")))
    assert not has_files(unit(msg(3, "", MediaKind.CONTACT)))


async def test_a_unit_sent_by_id_is_a_batch_of_its_own_like_a_reupload() -> None:
    route = router("auto", CaptionMode.NONE, by_reference=True)
    units = stream(
        unit(msg(1, "words")),
        unit(msg(2, "a", MediaKind.PHOTO)),
        unit(msg(3, "b", MediaKind.PHOTO)),
    )

    got = [b async for b in batches(units, 10, route=route)]

    assert [(b.strategy, b.ids) for b in got] == [
        (Strategy.COPY, [1]),
        (Strategy.REFERENCE, [2]),
        (Strategy.REFERENCE, [3]),
    ]


# ---- which strategy ---------------------------------------------------------------------------


def test_a_caption_is_the_text_of_a_media_message_only() -> None:
    assert has_caption(unit(msg(1, "look", MediaKind.PHOTO)))
    assert has_caption(album(1, 2, "album"))
    assert not has_caption(unit(msg(1, "just words")))
    assert not has_caption(unit(msg(1, "a link http://x", MediaKind.WEBPAGE)))
    assert not has_caption(unit(msg(1, "", MediaKind.VIDEO)))


@pytest.mark.parametrize(
    ("mode", "caption", "captioned", "plain"),
    [
        ("copy", CaptionMode.KEEP, Strategy.COPY, Strategy.COPY),
        ("auto", CaptionMode.KEEP, Strategy.COPY, Strategy.COPY),
        ("auto", CaptionMode.APPEND, Strategy.REUPLOAD, Strategy.COPY),
        ("auto", CaptionMode.NONE, Strategy.REUPLOAD, Strategy.COPY),
        ("auto", CaptionMode.STRIP_LINKS, Strategy.REUPLOAD, Strategy.COPY),
        ("reupload", CaptionMode.KEEP, Strategy.REUPLOAD, Strategy.REUPLOAD),
        ("reupload", CaptionMode.NONE, Strategy.REUPLOAD, Strategy.REUPLOAD),
    ],
)
def test_router(mode: str, caption: CaptionMode, captioned: Strategy, plain: Strategy) -> None:
    route = router(mode, caption)

    assert route(unit(msg(1, "cap", MediaKind.PHOTO))) is captioned
    assert route(unit(msg(2, "text only"))) is plain


# ---- batching by strategy ---------------------------------------------------------------------


async def stream(*items: Unit | Skip) -> AsyncIterator[Unit | Skip]:
    for item in items:
        yield item


async def collect(*items: Unit | Skip, size: int = 10, mode: str = "auto") -> list[Batch]:
    route = router(mode, CaptionMode.APPEND)
    return [b async for b in batches(stream(*items), size, route=route)]


async def test_a_reuploaded_unit_is_a_batch_of_its_own_and_copies_group_around_it() -> None:
    result = await collect(
        unit(msg(1, "a")),
        unit(msg(2, "b")),
        unit(msg(3, "cap", MediaKind.PHOTO)),
        unit(msg(4, "c")),
        album(5, 3, "album"),
        unit(msg(8, "d")),
    )

    assert [(b.strategy, b.ids) for b in result] == [
        (Strategy.COPY, [1, 2]),
        (Strategy.REUPLOAD, [3]),
        (Strategy.COPY, [4]),
        (Strategy.REUPLOAD, [5, 6, 7]),  # an album stays whole
        (Strategy.COPY, [8]),
    ]


async def test_in_reupload_mode_every_unit_is_alone() -> None:
    result = await collect(unit(msg(1, "a")), unit(msg(2, "b")), size=100, mode="reupload")

    assert [b.ids for b in result] == [[1], [2]]
    assert {b.strategy for b in result} == {Strategy.REUPLOAD}


async def test_skips_are_credited_to_the_batch_that_follows_them() -> None:
    result = await collect(Skip(3, 3), unit(msg(4, "cap", MediaKind.PHOTO)), Skip(2, 6))

    first, last = result
    assert (first.skipped, first.last_id, first.strategy) == (3, 4, Strategy.REUPLOAD)
    assert (last.units, last.skipped, last.last_id) == ((), 2, 6)


async def test_copy_batches_are_unchanged_without_a_router() -> None:
    result = [b async for b in batches(stream(unit(msg(1)), unit(msg(2)), unit(msg(3))), 2)]

    assert [b.ids for b in result] == [[1, 2], [3]]
    assert {b.strategy for b in result} == {Strategy.COPY}


# ---- what to do with a unit -------------------------------------------------------------------


PLAIN = Options()


def test_ordinary_media_is_sent() -> None:
    assert plan_unit(unit(msg(1, "x", MediaKind.VIDEO)), PLAIN).kind is ActionKind.SEND
    assert plan_unit(album(1, 3), PLAIN).kind is ActionKind.SEND
    assert plan_unit(unit(msg(1, "words")), PLAIN).kind is ActionKind.SEND
    assert plan_unit(unit(msg(1, media=MediaKind.GEO)), PLAIN).kind is ActionKind.SEND
    assert plan_unit(unit(msg(1, media=MediaKind.CONTACT)), PLAIN).kind is ActionKind.SEND


def test_a_poll_is_dropped_without_reset_polls_and_never_gets_a_placeholder() -> None:
    poll = unit(msg(1, media=MediaKind.POLL, title="Q?"))

    dropped = plan_unit(poll, Options(placeholder=True, ignore_unsupported=True))

    assert (dropped.kind, dropped.reason) == (ActionKind.DROP, "unsupported:poll")
    assert plan_unit(poll, Options(reset_polls=True)).kind is ActionKind.SEND


def test_an_unanswered_quiz_is_dropped_without_reset_polls_and_unsupported_with_it() -> None:
    quiz = unit(msg(1, media=MediaKind.POLL, quiz_unanswered=True, title="2+2?"))

    assert plan_unit(quiz, PLAIN) == plan_unit(quiz, Options(ignore_unsupported=True))
    assert plan_unit(quiz, PLAIN).reason == "unsupported:quiz"
    with pytest.raises(UnsupportedMedia) as caught:
        plan_unit(quiz, Options(reset_polls=True))
    assert (caught.value.kind, caught.value.msg_id) == ("quiz", 1)
    kept = plan_unit(quiz, Options(reset_polls=True, ignore_unsupported=True))
    assert (kept.kind, kept.reason) == (ActionKind.DROP, "unsupported:quiz")


@pytest.mark.parametrize("media", [MediaKind.GAME, MediaKind.INVOICE])
def test_a_game_or_an_invoice_stops_the_run_unless_told_otherwise(media: MediaKind) -> None:
    thing = unit(msg(4, media=media, title="Chess"))

    with pytest.raises(UnsupportedMedia) as caught:
        plan_unit(thing, PLAIN)
    assert (caught.value.kind, caught.value.msg_id) == (media.value, 4)
    assert plan_unit(thing, Options(ignore_unsupported=True)).kind is ActionKind.DROP
    posted = plan_unit(thing, Options(placeholder=True))  # implies ignoring
    assert posted.kind is ActionKind.PLACEHOLDER and posted.reason == f"unsupported:{media.value}"
    assert "Chess" in posted.text


def test_placeholder_text_names_the_thing_and_says_it_could_not_be_copied() -> None:
    assert placeholder_text("game", "Chess") == "[Game: Chess — không thể sao chép]"
    assert placeholder_text("invoice", None) == "[Hóa đơn — không thể sao chép]"


def test_left_out_results_are_skipped_rows_with_the_placeholder_id() -> None:
    action = plan_unit(unit(msg(9, media=MediaKind.GAME)), Options(ignore_unsupported=True))

    assert left_out(unit(msg(9, media=MediaKind.GAME)), action, 55) == [
        MessageResult(9, 55, "unsupported:game", skipped=True)
    ]


def test_a_result_is_copied_failed_or_skipped_and_nothing_else() -> None:
    MessageResult(1, 5)
    MessageResult(1, None, "why")
    MessageResult(1, None, "why", skipped=True)
    with pytest.raises(ValueError):
        MessageResult(1)
    with pytest.raises(ValueError):
        MessageResult(1, 5, "why")
    with pytest.raises(ValueError):
        MessageResult(1, 5, skipped=True)


def test_options_come_from_the_run_options() -> None:
    options = Options.of(
        RunOptions(caption="append", caption_text="x", reset_polls=True, placeholder=True)
    )

    assert options.caption.mode is CaptionMode.APPEND and options.caption.text == "x"
    assert (options.reset_polls, options.ignore_unsupported, options.placeholder) == (
        True,
        False,
        True,
    )


def test_run_options_survive_json_and_older_json_gets_the_defaults() -> None:
    options = RunOptions(caption="none", ignore_unsupported=True)

    assert RunOptions.from_json(options.to_json()) == options
    old = RunOptions.from_json('{"batch_size": 5, "dst_base_id": 3, "pushdown": true}')
    assert (old.caption, old.reset_polls, old.placeholder) == ("keep", False, False)
    assert options.for_pair(9).caption == "none" and options.for_pair(9).dst_base_id == 9


# ---- options that contradict each other ----------------------------------------------------------


@pytest.mark.parametrize(
    ("request_", "key"),
    [
        (RunRequest(caption="shout"), "caption_unknown"),
        (RunRequest(caption="append"), "caption_text_missing"),
        (RunRequest(caption="append", caption_text="  "), "caption_text_missing"),
        (RunRequest(caption="none", caption_text="x"), "caption_text_unused"),
        (RunRequest(mode="copy", caption="none"), "caption_needs_reupload"),
        (RunRequest(mode="auto", reset_polls=True), "reupload_flags_need_reupload"),
        (RunRequest(mode="copy", placeholder=True), "reupload_flags_need_reupload"),
    ],
)
def test_contradicting_options_are_refused(request_: RunRequest, key: str) -> None:
    with pytest.raises(InvalidOptions) as caught:
        check_options(request_)
    assert caught.value.key == key


def test_good_options_and_unknown_modes() -> None:
    check_options(RunRequest(mode="reupload", caption="append", caption_text="x", placeholder=True))
    check_options(RunRequest(mode="auto", caption="strip-links"))
    with pytest.raises(ModeUnsupported):
        check_options(RunRequest(mode="teleport"))


# ---- the window -------------------------------------------------------------------------------


async def settles(coro: object, seconds: float = 0.05) -> bool:
    """``True`` when the awaitable finishes within ``seconds``."""
    task = asyncio.ensure_future(coro)  # type: ignore[arg-type]
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()
    return bool(done)


async def test_the_window_holds_back_a_unit_that_does_not_fit_until_room_is_made() -> None:
    window = Window(units=3, max_bytes=100)
    await window.reserve(60)

    assert not await settles(window.reserve(60))  # 120 > 100
    assert await settles(window.reserve(40))  # exactly the budget
    await window.release(60)
    assert await settles(window.reserve(60))


async def test_the_window_holds_back_by_count_too() -> None:
    window = Window(units=2, max_bytes=10**9)
    await window.reserve(1)
    await window.reserve(1)

    assert not await settles(window.reserve(1))
    await window.release(1)
    assert await settles(window.reserve(1))


async def test_a_unit_bigger_than_the_budget_still_goes_through_when_the_disk_is_clear() -> None:
    window = Window(units=2, max_bytes=10)

    assert await settles(window.reserve(1000))
    assert not await settles(window.reserve(1))  # but nothing joins it


async def test_a_unit_bigger_than_reserved_is_booked_and_holds_the_next_one_back() -> None:
    window = Window(units=3, max_bytes=100)
    await window.reserve(10)
    await window.grow(200)  # it turned out to weigh 210: what is on disk cannot be refused

    assert not await settles(window.reserve(10))
    await window.release(210)
    assert await settles(window.reserve(10))


def test_a_download_is_reserved_for_what_telegram_said_and_a_stand_in_when_it_did_not() -> None:
    sized = unit(msg(1, "", MediaKind.VIDEO, size=5_000_000))
    unsized = unit(msg(2, "", MediaKind.PHOTO))
    pair = unit(
        msg(3, "", MediaKind.PHOTO, grouped_id=9, size=100),
        msg(4, "", MediaKind.PHOTO, grouped_id=9),
    )

    assert reserve_size(sized) == 5_000_000
    assert reserve_size(unsized) == UNKNOWN_SIZE  # never booked at nothing
    assert reserve_size(pair) == 100 + UNKNOWN_SIZE
    assert reserve_size(unit(msg(5, "words"))) == 0  # nothing to download
    assert reserve_size(unit(msg(6, "?", MediaKind.POLL))) == 0


# ---- the pipeline -----------------------------------------------------------------------------


def left(folder: Path) -> list[Path]:
    return list(folder.iterdir())


def batch(i: int) -> Batch:
    return Batch((unit(msg(i, "x")),))


async def numbers(*ids: int, then: Exception | None = None) -> AsyncIterator[Batch]:
    for i in ids:
        yield batch(i)
    if then is not None:
        raise then


async def never_idle() -> None:
    return None


def files_ready(tmp_path: Path, batch_: Batch, window: Window) -> Ready:
    path = tmp_path / str(batch_.ids[0])
    path.write_bytes(b"x")
    return Ready(batch_, prepared=Prepared(batch_.units[0], (path,)), reserved=1)


async def test_the_pipeline_hands_the_batches_over_in_order(tmp_path: Path) -> None:
    window = Window(2, 100)

    async def make(b: Batch) -> Ready:
        await window.reserve(1)
        return files_ready(tmp_path, b, window)

    pipeline = Pipeline(window, make, poll_interval=0.05)
    got: list[int] = []
    async for ready in pipeline.stream(numbers(1, 2, 3, 4), never_idle):
        got.append(ready.batch.ids[0])
        await pipeline.finish(ready)

    assert got == [1, 2, 3, 4] and left(tmp_path) == []


async def test_finish_deletes_the_files_and_is_safe_to_repeat(tmp_path: Path) -> None:
    window = Window(1, 100)
    await window.reserve(1)
    ready = files_ready(tmp_path, batch(1), window)
    pipeline = Pipeline(window, lambda b: asyncio.sleep(0), poll_interval=0.05)  # type: ignore[arg-type, return-value]

    await pipeline.finish(ready)
    await pipeline.finish(ready)

    assert left(tmp_path) == []
    assert await settles(window.reserve(1))  # the room was given back once


async def test_an_error_arrives_after_the_batches_that_came_before_it() -> None:
    window = Window(3, 100)

    async def make(b: Batch) -> Ready:
        if b.ids[0] == 3:
            raise RuntimeError("boom")
        return Ready(b)

    pipeline = Pipeline(window, make, poll_interval=0.05)
    got: list[int] = []
    with pytest.raises(RuntimeError, match="boom"):
        async for ready in pipeline.stream(numbers(1, 2, 3, 4), never_idle):
            got.append(ready.batch.ids[0])

    assert got == [1, 2]


async def test_an_error_from_reading_the_source_arrives_in_order_too() -> None:
    pipeline = Pipeline(Window(1, 1), lambda b: _plain(b), poll_interval=0.05)
    got: list[int] = []
    with pytest.raises(ValueError, match="source"):
        async for ready in pipeline.stream(numbers(1, then=ValueError("source")), never_idle):
            got.append(ready.batch.ids[0])

    assert got == [1]


async def _plain(b: Batch) -> Ready:
    return Ready(b)


async def test_leaving_early_removes_what_was_downloaded_ahead(tmp_path: Path) -> None:
    window = Window(3, 100)

    async def make(b: Batch) -> Ready:
        await window.reserve(1)
        return files_ready(tmp_path, b, window)

    pipeline = Pipeline(window, make, poll_interval=0.05)
    stream = pipeline.stream(numbers(1, 2, 3), never_idle)
    first = await anext(stream)  # 2 (and maybe 3) are being fetched meanwhile
    await asyncio.sleep(0.05)
    await pipeline.finish(first)
    await stream.aclose()  # type: ignore[attr-defined]

    assert left(tmp_path) == []


async def test_the_idle_hook_can_end_a_wait_for_a_slow_download() -> None:
    hang = asyncio.Event()

    async def slow(b: Batch) -> Ready:
        await hang.wait()
        return Ready(b)

    async def stop() -> None:
        raise LookupError("stop")

    pipeline = Pipeline(Window(1, 1), slow, poll_interval=0.02)

    with pytest.raises(LookupError):
        async for _ in pipeline.stream(numbers(1), stop):
            pass


class Died(BaseException):
    """Not an ``Exception``: the producer does not catch it, so its task simply dies."""


async def test_a_download_task_that_dies_is_reported_instead_of_waited_for_for_ever() -> None:
    async def make(b: Batch) -> Ready:
        raise Died

    pipeline = Pipeline(Window(1, 1), make, poll_interval=0.02)

    with pytest.raises(Died):
        async with asyncio.timeout(5):  # a bug here shows as a timeout, not as a hung test run
            async for _ in pipeline.stream(numbers(1), never_idle):
                pass
