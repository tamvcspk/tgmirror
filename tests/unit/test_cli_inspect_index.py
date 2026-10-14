"""``tgmirror inspect`` and its local index (phase 18b): built on the first look, brought up to date
by what is newer, usable with no Telegram at all."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from tests.fakes import FakeGateway
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.errors import ServerFailed
from tgmirror.core.gateway import ChatKind, MediaKind

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]


def source(gateway: FakeGateway, count: int = 5) -> int:
    chan = gateway.add_channel("Source", username="srcchan")
    for i in range(1, count + 1):
        gateway.add_message(chan.id, f"m{i}", media=MediaKind.VIDEO if i % 2 else MediaKind.TEXT)
    return chan.id


def look(rt: Runtime, *flags: str) -> Any:
    result = cli.invoke(app, ["inspect", "Source", "--json", *flags], obj=rt)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def reads(gateway: FakeGateway) -> list[int]:
    """The ``min_id`` of every live read so far."""
    return [c.args[1] for c in gateway.calls_to("iter_messages")]


def test_the_first_look_builds_the_index_and_the_next_one_reads_only_what_is_newer(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = source(gateway)
    rt = make_runtime(gateway=gateway)

    first = look(rt)
    gateway.add_message(src, "m6")
    gateway.add_message(src, "m7")
    second = look(rt)

    assert first["scanned"]["messages"] == 5 and first["index"]["messages"] == 5
    assert second["scanned"]["messages"] == 7 and second["index"]["messages"] == 7
    assert second["index"]["complete"] and not second["index"]["offline"]
    assert reads(gateway) == [0, 5]  # the second look asked only for what follows id 5
    assert (rt.paths.index_dir / f"{src}.db").exists()


def test_the_text_report_says_what_it_rests_on(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source(gateway)

    result = cli.invoke(app, ["inspect", "Source"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 0, result.output
    assert "Local index: 5 messages, updated" in result.output
    assert "--refresh" in result.output


def test_offline_reports_from_the_index_without_asking_telegram_anything(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = source(gateway)
    rt = make_runtime(gateway=gateway)
    look(rt)
    gateway.add_message(src, "later")  # Telegram has more now; the index does not
    before = len(gateway.calls)

    data = look(rt, "--offline", "--media", "video")

    assert len(gateway.calls) == before  # not even listing the chats
    assert data["scanned"]["messages"] == 5 and data["selected"]["messages"] == 3
    assert data["index"]["offline"] is True


def test_offline_finds_the_source_by_id_username_or_title(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = source(gateway)
    rt = make_runtime(gateway=gateway)
    look(rt)
    bare = str(abs(src) - 1_000_000_000_000)

    for ref in ("srcchan", "@srcchan", "SOURCE", str(abs(src)), bare):  # a minus reads as an option
        result = cli.invoke(app, ["inspect", ref, "--offline", "--json"], obj=rt)
        assert result.exit_code == 0, (ref, result.output)


def test_offline_without_an_index_is_a_usage_error_that_says_how_to_build_one(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source(gateway)

    result = cli.invoke(app, ["inspect", "Source", "--offline"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 2, result.output
    assert "no index" in result.output.lower() or "There is no index" in result.output


def test_refresh_reads_everything_again_and_forgets_deleted_messages(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = source(gateway)
    rt = make_runtime(gateway=gateway)
    look(rt)
    del gateway.messages[src][1]  # message 2 is deleted at the source

    assert look(rt)["scanned"]["messages"] == 5  # a delta cannot know
    refreshed = look(rt, "--refresh")

    assert refreshed["scanned"]["messages"] == 4
    assert reads(gateway)[-1] == 0  # from the start


def test_no_index_is_the_live_scan_and_keeps_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = source(gateway)
    rt = make_runtime(gateway=gateway)

    data = look(rt, "--no-index")

    assert data["scanned"]["messages"] == 5 and "index" not in data
    assert not (rt.paths.index_dir / f"{src}.db").exists()


def test_the_filter_runs_on_the_index_like_it_does_live(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source(gateway)
    rt = make_runtime(gateway=gateway)

    live = look(rt, "--no-index", "--media", "video", "--by", "media", "--by", "month")
    indexed = look(rt, "--media", "video", "--by", "media", "--by", "month")
    cached = look(rt, "--offline", "--media", "video", "--by", "media", "--by", "month")

    for data in (indexed, cached):
        assert data["selected"] == live["selected"] and data["tables"] == live["tables"]


def test_flags_that_cannot_go_together_are_refused(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source(gateway)
    rt = make_runtime(gateway=gateway)

    for flags in (
        ["--offline", "--refresh"],
        ["--offline", "--no-index"],
        ["--refresh", "--no-index"],
    ):
        result = cli.invoke(app, ["inspect", "Source", *flags], obj=rt)
        assert result.exit_code == 2, (flags, result.output)
    assert gateway.calls_to("iter_messages") == []


# ---- inspect --quick (spike 1) ----------------------------------------------------------------


def quick_source(gateway: FakeGateway) -> int:
    chan = gateway.add_channel("Source", username="srcchan")
    gateway.add_message(chan.id, "a", media=MediaKind.PHOTO)
    gateway.add_message(chan.id, "b", media=MediaKind.VIDEO)
    gateway.add_message(chan.id, "c", media=MediaKind.VIDEO, link_domains=("example.com",))
    gateway.add_message(chan.id, "d")
    return chan.id


def test_quick_is_one_request_and_reads_no_message(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    quick_source(gateway)

    result = cli.invoke(
        app, ["inspect", "Source", "--quick", "--json"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 0, result.output
    counts = json.loads(result.output)["quick"]["counts"]
    assert counts["photo"] == 1 and counts["video"] == 2 and counts["link"] == 1
    assert counts["poll"] is None and counts["pinned"] is None  # Telegram gives none
    assert len(gateway.calls_to("media_counters")) == 1
    assert gateway.calls_to("iter_messages") == []


def test_quick_text_names_the_kinds_and_says_it_is_telegrams_own_count(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    quick_source(gateway)

    result = cli.invoke(app, ["inspect", "Source", "--quick"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 0, result.output
    assert "Photos" in result.output and "Videos" in result.output
    assert "—" in result.output and "classifies by itself" in result.output


def test_quick_counts_one_forum_topic(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    forum = gateway.add_channel("Forum", kind=ChatKind.FORUM).id
    news = gateway.add_topic(forum, "News")
    gateway.add_message(forum, "in news", media=MediaKind.PHOTO, topic_id=news.id)
    gateway.add_message(forum, "in general", media=MediaKind.PHOTO)
    gateway.add_message(forum, "also general", media=MediaKind.VIDEO)
    rt = make_runtime(gateway=gateway)

    topic = cli.invoke(
        app, ["inspect", "Forum", "--quick", "--topic", str(news.id), "--json"], obj=rt
    )
    general = cli.invoke(app, ["inspect", "Forum", "--quick", "--topic", "1", "--json"], obj=rt)

    assert json.loads(topic.output)["quick"]["counts"]["photo"] == 1
    assert json.loads(topic.output)["quick"]["topic"] == news.id
    counts = json.loads(general.output)["quick"]["counts"]
    assert (counts["photo"], counts["video"]) == (1, 1)


def test_quick_refuses_what_it_cannot_count(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    quick_source(gateway)
    rt = make_runtime(gateway=gateway)
    refused = [
        ["--media", "video"],
        ["--since", "2024-01-01"],
        ["--topic", "2", "--topic", "3"],
        ["--by", "media"],
        ["--list"],
        ["--plan", "clone"],
        ["--offline"],
        ["--not-forwarded"],
    ]

    for flags in refused:
        result = cli.invoke(app, ["inspect", "Source", "--quick", *flags], obj=rt)
        assert result.exit_code == 2, (flags, result.output)
    assert gateway.calls_to("media_counters") == []


def test_quick_that_telegram_cannot_count_is_one_sentence_not_a_crash(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """A real ``--quick --topic N`` met ``RpcCallFailError`` six times in a row."""
    quick_source(gateway)
    gateway.fail_next("media_counters", ServerFailed("internal"))

    result = cli.invoke(app, ["inspect", "Source", "--quick"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 1, result.output
    assert "internal error" in result.output and "without --topic" in result.output
    assert "unexpected" not in result.output


def test_quick_with_a_topic_that_is_not_one_is_said_before_asking_telegram(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """A real ``--quick --topic 9496`` was met with six internal errors from Telegram: ask for the
    topics first, and say plainly when the id is no topic (or the source no forum)."""
    forum = gateway.add_channel("Forum", kind=ChatKind.FORUM).id
    gateway.add_topic(forum, "News")
    quick_source(gateway)  # "Source": a plain channel
    rt = make_runtime(gateway=gateway)

    unknown = cli.invoke(app, ["inspect", "Forum", "--quick", "--topic", "9496"], obj=rt)
    plain_channel = cli.invoke(app, ["inspect", "Source", "--quick", "--topic", "2"], obj=rt)

    assert unknown.exit_code == 2 and "has no topic 9496" in unknown.output, unknown.output
    assert plain_channel.exit_code == 2 and "not a forum" in plain_channel.output
    assert gateway.calls_to("media_counters") == []
