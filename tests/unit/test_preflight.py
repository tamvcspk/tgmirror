"""``engine/preflight.py``: what a run would do, from the same router the run uses (phase 18a)."""

from datetime import UTC, datetime

import pytest

from tgmirror.core.config import Limits
from tgmirror.core.gateway import CaptionMode, MediaKind, SrcMessage, Unit
from tgmirror.engine.preflight import PlanOptions, Preflight, plan_to_dict
from tgmirror.engine.transform import Transform

NOW = datetime(2024, 1, 1, tzinfo=UTC)
MB = 1024**2
_ids = iter(range(1, 10_000))


def msg(text: str = "", media: MediaKind = MediaKind.TEXT, **kw: object) -> SrcMessage:
    return SrcMessage(id=next(_ids), date=NOW, text=text, media=media, **kw)  # type: ignore[arg-type]


def unit(*members: SrcMessage) -> Unit:
    return Unit(members)


def plan(units: list[Unit], limits: Limits | None = None, **options: object):
    pre = Preflight(PlanOptions(**options), limits or Limits())  # type: ignore[arg-type]
    for u in units:
        pre.add(u)
    return pre.plan()


def way(result, name: str):
    return next((w for w in result.ways if w.strategy == name), None)


def test_a_plain_clone_forwards_everything_in_batches() -> None:
    units = [unit(msg(f"t{i}")) for i in range(45)]

    result = plan(units, Limits(batch_size=20))

    copy = way(result, "copy")
    assert (copy.units, copy.messages) == (45, 45)
    assert result.write_calls == 3  # 20 + 20 + 5
    assert result.transfer_bytes == 0


def test_a_rewritten_caption_sends_captioned_units_again_by_file_id() -> None:
    captioned = unit(msg("look http://t.me/src", MediaKind.PHOTO, size=2 * MB))
    plain = unit(msg("just text"))

    result = plan([captioned, plain], mode="auto", caption=CaptionMode.STRIP_LINKS)

    assert way(result, "reference").units == 1
    assert way(result, "copy").units == 1
    assert result.transfer_bytes == 0  # by file id: nothing is downloaded


def test_a_protected_source_downloads_what_has_to_be_rewritten() -> None:
    captioned = unit(msg("cap", MediaKind.VIDEO, size=30 * MB))

    result = plan([captioned], mode="auto", caption=CaptionMode.NONE, by_reference=False)

    assert way(result, "reupload").bytes == 30 * MB
    assert result.transfer_bytes == 30 * MB and result.largest_file == 30 * MB


def test_reupload_mode_downloads_everything() -> None:
    result = plan([unit(msg("a")), unit(msg("b", MediaKind.PHOTO, size=MB))], mode="reupload")

    assert way(result, "reupload").units == 2
    assert result.write_calls == 2


def test_things_that_cannot_be_copied_are_counted_by_kind() -> None:
    poll = unit(msg("", MediaKind.POLL, title="q?"))
    game = unit(msg("", MediaKind.GAME, title="g"))
    units = [poll, game, unit(msg("ok"))]

    stops = plan(units, mode="reupload")
    assert dict(stops.blocked) == {"game": 1} and dict(stops.left_out) == {"poll": 1}

    skips = plan(units, mode="reupload", ignore_unsupported=True, reset_polls=True)
    assert skips.blocked == () and dict(skips.left_out) == {"game": 1}
    assert way(skips, "reupload").units == 2  # the poll is re-created, with --reset-polls


def test_a_forwarded_poll_is_not_a_problem() -> None:
    result = plan([unit(msg("", MediaKind.POLL, title="q?"))], mode="copy")

    assert way(result, "copy").units == 1 and result.blocked == () and result.left_out == ()


def test_a_backup_downloads_everything_and_writes_nothing() -> None:
    units = [unit(msg("a")), unit(msg("b", MediaKind.PHOTO, size=3 * MB))]

    result = plan(units, kind="backup")

    assert way(result, "download").units == 2
    assert result.transfer_bytes == 3 * MB
    assert result.write_calls == 0


def test_a_restore_uploads_everything() -> None:
    result = plan([unit(msg("a", MediaKind.DOCUMENT, size=MB))], kind="restore")

    assert way(result, "reupload").bytes == MB and result.transfer_bytes == MB


def test_pacing_is_the_limiters_floor_for_the_calls() -> None:
    limits = Limits(batch_size=10, min_delay=2.0, long_pause_every=2, long_pause_range=(10.0, 30.0))
    units = [unit(msg(str(i))) for i in range(40)]  # 4 forward calls

    result = plan(units, limits)

    assert result.write_calls == 4
    assert result.pacing_seconds == pytest.approx(4 * 2.0 + 2 * 20.0)


def routing(**options: object) -> Transform:
    return Transform.from_data({"topics": {"hashtag_to_topic": options}})


def test_hashtag_routing_counts_units_by_topic_and_cuts_batches_where_it_changes() -> None:
    tagged = lambda tag: unit(msg(tag, hashtags=(tag,)))  # noqa: E731
    units = [
        tagged("#py"), tagged("#py"), tagged("#rs"), tagged("#py"),  # three stretches
        unit(msg("none")),
    ]  # fmt: skip

    result = plan(
        units,
        Limits(batch_size=20),
        transform=routing(map={"#py": "Python", "#rs": "Rust"}),
    )

    assert dict(result.topics) == {"Python": 3, "Rust": 1, None: 1}
    # one forward per stretch of units for the same place, not ceil(5 / 20) = 1
    assert result.write_calls == 4
    assert plan_to_dict(result)["topics"] == [
        {"topic": "Python", "units": 3},
        {"topic": "Rust", "units": 1},
        {"topic": None, "units": 1},
    ]


def test_a_default_or_auto_names_where_the_rest_goes() -> None:
    units = [unit(msg("a", hashtags=("#py",))), unit(msg("none"))]

    result = plan(units, transform=routing(map={"#py": "Python"}, default="Misc"))
    assert dict(result.topics) == {"Python": 1, "Misc": 1}
    result = plan(units, transform=routing(map={"#py": "Python"}, auto="all"))
    assert dict(result.topics) == {"Python": 1, None: 1}


def test_without_routing_there_is_no_topic_breakdown() -> None:
    result = plan([unit(msg("a", hashtags=("#py",)))], transform=Transform())

    assert result.topics == ()
