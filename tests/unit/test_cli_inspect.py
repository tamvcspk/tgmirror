"""``tgmirror inspect`` through the real Typer app: no terminal, no network (phase 18a)."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from tests.conftest import API_ENV
from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Connection, Runtime, opened_store
from tgmirror.core.config import Config
from tgmirror.core.errors import FloodWait
from tgmirror.core.gateway import ChannelInfo, ChatKind, MediaKind
from tgmirror.core.paths import Paths

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]
MB = 1024**2


def fill(gateway: FakeGateway) -> ChannelInfo:
    src = gateway.add_channel("Source", username="srcchan")
    when = datetime(2024, 3, 1, tzinfo=UTC)
    gateway.add_message(src.id, "hello #news", hashtags=("#news",), date=when)
    gateway.add_message(src.id, "clip", media=MediaKind.VIDEO, size=5 * MB, date=when)
    gateway.add_message(
        src.id,
        "read https://www.example.com/x",
        link_domains=("example.com",),
        is_forward=True,
        fwd_from_id=-1007,
        fwd_from_name="Origin",
        date=when,
    )
    return src


def test_report_shows_totals_and_tables(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    fill(gateway)

    result = cli.invoke(
        app,
        ["inspect", "Source", "--by", "media", "--by", "forward"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    assert "Read 3 messages (3 units, 0 albums)" in result.output
    assert "By media type" in result.output and "video" in result.output
    assert "Origin (-1007)" in result.output
    assert "(not forwarded)" in result.output


def test_a_filter_reports_the_selection_next_to_the_totals(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)

    result = cli.invoke(
        app,
        ["inspect", "Source", "--media", "video", "--by", "media"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    assert "Read 3 messages" in result.output
    assert "Matches the filter: 1 messages" in result.output


def test_the_new_predicates_are_available_as_flags(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)

    result = cli.invoke(
        app,
        ["inspect", "Source", "--forwarded", "--link-domain", "example.com", "--json"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["selected"]["messages"] == 1
    assert [m["id"] for m in data["messages"]] == [3]
    assert data["messages"][0]["link"] == "https://t.me/srcchan/3"
    assert data["messages"][0]["forwarded_from"] == "-1007"


def test_json_has_the_report_the_source_and_every_selected_message(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = fill(gateway)

    result = cli.invoke(app, ["inspect", "Source", "--json"], obj=make_runtime(gateway=gateway))

    data = json.loads(result.output)
    assert data["source"]["id"] == src.id
    assert data["complete"] is True and data["filtered"] is False
    assert data["scanned"]["messages"] == 3
    assert set(data["tables"]) == {"media", "month", "hashtag"}  # the default tables
    assert len(data["messages"]) == 3


def test_list_shows_messages_and_says_how_many_more(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)

    result = cli.invoke(
        app, ["inspect", "Source", "--list", "--limit", "1"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 0, result.output
    assert "Matching messages" in result.output
    assert "2 more messages" in result.output


def test_csv_has_every_selected_message_with_a_bom_for_excel(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    out = tmp_path / "out.csv"

    result = cli.invoke(
        app,
        ["inspect", "Source", "--csv", str(out), "--media", "video"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("id,date,media,size")
    assert len(lines) == 2 and ",video," in lines[1]


def test_a_backup_directory_is_read_offline(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    out = tmp_path / "backup"
    assert (
        cli.invoke(app, ["backup", "Source", str(out)], obj=make_runtime(gateway=gateway)).exit_code
        == 0
    )

    @asynccontextmanager
    async def no_network(config: Config) -> AsyncIterator[Connection]:
        raise AssertionError("inspecting a backup must not connect")
        yield Connection(FakeAuth(logged_in=ACCOUNT), gateway)  # pragma: no cover

    offline = Runtime(
        paths=Paths.under(tmp_path / "other"),
        connect=no_network,
        prompter=make_runtime().prompter,
        interactive=False,
        env=API_ENV,
    )

    result = cli.invoke(
        app, ["inspect", str(out), "--by", "forward", "--by", "domain"], obj=offline
    )

    assert result.exit_code == 0, result.output
    assert "Read 3 messages" in result.output
    assert "(backup" in result.output
    assert "example.com" in result.output
    assert "Origin (-1007)" in result.output  # the metadata survived the round trip


def test_a_directory_that_is_not_a_backup_is_a_usage_error(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    empty = tmp_path / "plain"
    empty.mkdir()

    result = cli.invoke(app, ["inspect", str(empty)], obj=make_runtime())

    assert result.exit_code == 2
    assert "not a valid backup directory" in result.output


def test_no_source_without_a_terminal_is_a_usage_error(make_runtime: MakeRuntime) -> None:
    result = cli.invoke(app, ["inspect"], obj=make_runtime())

    assert result.exit_code == 2
    assert "SRC" in result.output


def test_an_unknown_table_is_a_usage_error(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    fill(gateway)

    result = cli.invoke(
        app, ["inspect", "Source", "--by", "mood"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 2
    assert "mood" in result.output


def test_a_bad_filter_is_refused_before_anything_is_read(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)

    result = cli.invoke(
        app, ["inspect", "Source", "--since", "yesterday"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 2
    assert not any(call.method == "iter_messages" for call in gateway.calls)


def test_a_forum_gets_a_topic_table_with_titles(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = gateway.add_channel("Forum", kind=ChatKind.FORUM)
    topic = gateway.add_topic(forum.id, "Releases")
    gateway.add_message(forum.id, "a", topic_id=topic.id)
    gateway.add_message(forum.id, "b", topic_id=topic.id)
    gateway.add_message(forum.id, "c")

    result = cli.invoke(
        app, ["inspect", "Forum", "--by", "topic"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 0, result.output
    assert f"Releases ({topic.id})" in result.output


def test_a_flood_too_long_to_wait_shows_what_was_read_and_exits_3(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)
    gateway.fail_next("iter_messages", FloodWait(100_000))

    result = cli.invoke(app, ["inspect", "Source"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 3, result.output
    assert "The source has no messages." in result.output or "Stopped early" in result.output


def test_a_flood_during_a_scan_is_logged(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    fill(gateway)
    gateway.fail_next("iter_messages", FloodWait(100_000))
    rt = make_runtime(gateway=gateway)
    cli.invoke(app, ["inspect", "Source"], obj=rt)

    async def count() -> int:
        async with opened_store(rt) as store:
            return await store.flood_count_since(datetime(2000, 1, 1, tzinfo=UTC))

    assert asyncio.run(count()) == 1


# ---- --plan: the pre-flight -------------------------------------------------------------------


def _ways(data: dict) -> dict[str, dict]:
    return {w["strategy"]: w for w in data["plan"]["ways"]}


def test_plan_clone_counts_forwards_and_batches(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)

    result = cli.invoke(
        app, ["inspect", "Source", "--plan", "clone", "--json"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)["plan"]
    assert plan["kind"] == "clone"
    assert _ways(json.loads(result.output))["copy"]["units"] == 3
    assert plan["write_calls"] == 1 and plan["transfer_bytes"] == 0


def test_plan_text_says_how_units_travel(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    fill(gateway)

    result = cli.invoke(
        app,
        ["inspect", "Source", "--plan", "clone", "--caption", "strip-links", "--by", "media"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    assert "Plan for clone (mode auto, caption strip-links):" in result.output
    assert "sent again by file id" in result.output
    assert "forward (Telegram copies server-side)" in result.output


def test_plan_matches_what_the_real_clone_then_does(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """The done-criterion of 18a: the units the pre-flight puts on each way are the ones the
    runner really sends that way."""
    src = gateway.add_channel("Source")
    gateway.add_message(src.id, "plain text")
    gateway.add_message(src.id, "look t.me/x", media=MediaKind.PHOTO, size=MB)
    gateway.add_message(src.id, "another", media=MediaKind.VIDEO, size=2 * MB)
    gateway.add_message(src.id, "no caption", media=MediaKind.PHOTO, size=MB)
    rt = make_runtime(gateway=gateway)
    flags = ["--caption", "strip-links"]

    inspected = cli.invoke(app, ["inspect", "Source", "--plan", "clone", "--json", *flags], obj=rt)
    ways = _ways(json.loads(inspected.output))
    cloned = cli.invoke(
        app, ["clone", "--src", "Source", "--dst-new", "Dest", "--yes", *flags], obj=rt
    )

    assert cloned.exit_code == 0, cloned.output
    forwarded = sum(len(c.args[2]) for c in gateway.calls if c.method == "copy_messages")
    referenced = sum(1 for c in gateway.calls if c.method == "send_by_reference")
    assert forwarded == ways["copy"]["messages"]
    assert referenced == ways["reference"]["units"]


def test_plan_for_a_protected_source_downloads_and_says_it_needs_the_statement(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = gateway.add_channel("Locked", noforwards=True, is_admin=False)
    gateway.add_message(src.id, "v", media=MediaKind.VIDEO, size=4 * MB)

    result = cli.invoke(
        app,
        ["inspect", "Locked", "--plan", "clone", "--mode", "reupload", "--by", "media"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output  # inspect itself needs no D3 statement
    assert "downloaded and uploaded again" in result.output
    assert "--yes-i-administer-this-channel" in result.output


def test_plan_restore_reads_a_backup_directory(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    out = tmp_path / "backup"
    assert (
        cli.invoke(app, ["backup", "Source", str(out)], obj=make_runtime(gateway=gateway)).exit_code
        == 0
    )

    result = cli.invoke(
        app,
        ["inspect", str(out), "--plan", "restore", "--json"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["plan"]["kind"] == "restore"
    assert _ways(data)["reupload"]["units"] == 3


def test_plan_backup_writes_nothing(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    fill(gateway)

    result = cli.invoke(
        app, ["inspect", "Source", "--plan", "backup", "--json"], obj=make_runtime(gateway=gateway)
    )

    plan = json.loads(result.output)["plan"]
    assert plan["write_calls"] == 0 and plan["days"] == 0
    assert _ways(json.loads(result.output))["download"]["units"] == 3


def test_plan_flag_mistakes_are_usage_errors(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    out = tmp_path / "backup"
    assert cli.invoke(app, ["backup", "Source", str(out)], obj=rt).exit_code == 0

    cases = [
        ["inspect", "Source", "--mode", "copy"],  # a plan flag without --plan
        ["inspect", "Source", "--plan", "nonsense"],
        ["inspect", "Source", "--plan", "restore"],  # a channel is not a backup
        ["inspect", str(out), "--plan", "clone"],  # a backup directory is restored, not cloned
        ["inspect", "Source", "--plan", "backup", "--mode", "copy"],
        ["inspect", "Source", "--plan", "clone", "--mode", "copy", "--caption", "none"],
    ]
    for argv in cases:
        result = cli.invoke(app, argv, obj=rt)
        assert result.exit_code == 2, (argv, result.output)


# ---- selections: --save, --freeze, @last, and using them -------------------------------------


def copied_ids(gateway: FakeGateway) -> list[int]:
    return [i for c in gateway.calls if c.method == "copy_messages" for i in c.args[2]]


def mixed_source(gateway: FakeGateway) -> ChannelInfo:
    src = gateway.add_channel("Source")
    for i in range(6):
        kind = MediaKind.VIDEO if i % 2 == 0 else MediaKind.PHOTO
        gateway.add_message(src.id, f"m{i}", media=kind, size=MB)
    return src


def test_inspect_keeps_the_last_selection_and_prints_what_to_run_next(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = mixed_source(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["inspect", "Source", "--media", "video", "--by", "media"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Continue with exactly this filter:" in result.output
    assert f"tgmirror clone --src {src.id} --media video" in result.output
    assert f"tgmirror backup {src.id} ./backup --media video" in result.output
    last = rt.paths.selections_dir / f"last-{src.id}.yaml"
    assert last.exists() and last.with_suffix(".ids").exists()


def test_clone_selection_last_copies_exactly_what_inspect_reported(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Done-criterion of 18a: inspect a channel, then ``clone --selection @last``."""
    mixed_source(gateway)
    rt = make_runtime(gateway=gateway)
    report = cli.invoke(app, ["inspect", "Source", "--media", "video", "--json"], obj=rt)
    selected = json.loads(report.output)["selected"]["messages"]

    cloned = cli.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Dest", "--selection", "@last", "--yes"],
        obj=rt,
    )

    assert cloned.exit_code == 0, cloned.output
    assert selected == 3
    assert len(copied_ids(gateway)) == selected


def test_a_saved_selection_names_its_source_so_clone_needs_no_src(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    mixed_source(gateway)
    rt = make_runtime(gateway=gateway)
    chosen = tmp_path / "videos.yaml"
    assert (
        cli.invoke(
            app, ["inspect", "Source", "--media", "video", "--save", str(chosen)], obj=rt
        ).exit_code
        == 0
    )

    cloned = cli.invoke(
        app, ["clone", "--dst-new", "Dest", "--selection", str(chosen), "--yes"], obj=rt
    )

    assert cloned.exit_code == 0, cloned.output
    assert len(copied_ids(gateway)) == 3


def test_exact_keeps_to_the_frozen_list_even_when_more_matches_appear(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = mixed_source(gateway)
    rt = make_runtime(gateway=gateway)
    chosen = tmp_path / "frozen.yaml"
    cli.invoke(
        app, ["inspect", "Source", "--media", "video", "--save", str(chosen), "--freeze"], obj=rt
    )
    assert chosen.with_suffix(".ids").read_text(encoding="utf-8").split() == ["1", "3", "5"]
    gateway.add_message(src.id, "new video", media=MediaKind.VIDEO, size=MB)  # matches, but is new

    live = cli.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Live", "--selection", str(chosen), "--yes"],
        obj=rt,
    )
    exact = cli.invoke(
        app,
        [
            "clone",
            "--src",
            "Source",
            "--dst-new",
            "Exact",
            "--selection",
            str(chosen),
            "--exact",
            "--yes",
        ],
        obj=rt,
    )

    assert live.exit_code == 0 and exact.exit_code == 0, live.output + exact.output
    per_call = [c.args[2] for c in gateway.calls if c.method == "copy_messages"]
    assert sorted(i for ids in per_call[: len(per_call) // 2] for i in ids) == [1, 3, 5, 7]
    assert sorted(i for ids in per_call[len(per_call) // 2 :] for i in ids) == [1, 3, 5]


def test_a_filter_that_flags_cannot_say_points_at_the_selection_file(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    mixed_source(gateway)
    rt = make_runtime(gateway=gateway)
    two_rules = tmp_path / "two.yaml"
    two_rules.write_text("include:\n  - media: [video]\n  - media: [photo]\n", encoding="utf-8")

    result = cli.invoke(
        app,
        ["inspect", "Source", "--filter-file", str(two_rules), "--save", str(tmp_path / "s.yaml")],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert "--selection" in result.output and "s.yaml" in result.output


def test_backup_and_restore_take_the_selection_too(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    mixed_source(gateway)
    rt = make_runtime(gateway=gateway)
    cli.invoke(app, ["inspect", "Source", "--media", "photo"], obj=rt)
    out = tmp_path / "bk"

    backed = cli.invoke(app, ["backup", "Source", str(out), "--selection", "@last"], obj=rt)

    assert backed.exit_code == 0, backed.output
    from tgmirror.engine import backupdir

    assert [r.id for r in backupdir.iter_records(out)] == [2, 4, 6]  # photos only
    cli.invoke(app, ["inspect", str(out), "--by", "media"], obj=rt)  # @last is now of the backup
    restored = cli.invoke(
        app, ["restore", str(out), "--dst-new", "--yes", "--selection", "@last"], obj=rt
    )
    assert restored.exit_code == 0, restored.output


def test_selection_mistakes_are_usage_errors(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    mixed_source(gateway)
    other = gateway.add_channel("Other")
    gateway.add_message(other.id, "x")
    rt = make_runtime(gateway=gateway)
    chosen = tmp_path / "s.yaml"
    cli.invoke(app, ["inspect", "Source", "--save", str(chosen)], obj=rt)
    base = ["clone", "--dst-new", "D", "--yes"]
    other_file = tmp_path / "f.yaml"
    other_file.write_text("include: [{media: [video]}]\n", encoding="utf-8")

    cases = [
        [*base, "--src", "Source", "--exact"],  # --exact alone
        # two files, no base
        [*base, "--src", "Source", "--selection", str(chosen), "--filter-file", str(other_file)],
        [*base, "--selection", "@last"],  # which source?
        [*base, "--src", "Other", "--selection", "@last"],  # nothing inspected there yet
        [*base, "--src", "Other", "--selection", str(chosen)],  # made for another source
        [*base, "--src", "Source", "--selection", str(chosen), "--exact"],  # not frozen
        [*base, "--src", "Source", "--selection", str(tmp_path / "missing.yaml")],
    ]
    for argv in cases:
        result = cli.invoke(app, argv, obj=rt)
        assert result.exit_code == 2, (argv, result.exit_code, result.output)
    assert copied_ids(gateway) == []


def test_filter_flags_narrow_the_selection_they_are_given_with(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    mixed_source(gateway)  # m0..m5, the even ones are videos (ids 1, 3, 5)
    rt = make_runtime(gateway=gateway)
    chosen = tmp_path / "s.yaml"
    cli.invoke(app, ["inspect", "Source", "--media", "video", "--save", str(chosen)], obj=rt)

    result = cli.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "D", "--yes"]
        + ["--selection", str(chosen), "--contains", "m2"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert copied_ids(gateway) == [3]  # a video (the selection) that also says m2 (the flag)


def test_a_partial_scan_is_not_kept_as_a_selection(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = mixed_source(gateway)
    gateway.fail_next("iter_messages", FloodWait(100_000))
    rt = make_runtime(gateway=gateway)

    cli.invoke(app, ["inspect", "Source"], obj=rt)

    assert not (rt.paths.selections_dir / f"last-{src.id}.yaml").exists()


def test_freeze_without_save_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    mixed_source(gateway)

    result = cli.invoke(app, ["inspect", "Source", "--freeze"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 2 and "--save" in result.output


def test_the_wizard_offers_the_latest_inspect_result_and_uses_its_filter(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    from tests.fakes import ScriptedPrompter
    from tgmirror.engine import backupdir

    mixed_source(gateway)
    rt = make_runtime(gateway=gateway)
    assert cli.invoke(app, ["inspect", "Source", "--media", "photo"], obj=rt).exit_code == 0
    out = tmp_path / "wizard"
    prompter = ScriptedPrompter(
        select=["Source", "Use the latest inspect result"], text=[str(out)], confirm=[True]
    )

    result = cli.invoke(
        app,
        ["backup"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 0, result.output
    assert [r.id for r in backupdir.iter_records(out)] == [2, 4, 6]  # the photos it reported
    offered = [
        labels for labels in prompter.select_labels if any("latest inspect" in x for x in labels)
    ]
    assert offered, "the filter question should have offered the inspect result"


def test_the_wizard_does_not_offer_it_for_a_source_never_inspected(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    from tests.fakes import ScriptedPrompter

    mixed_source(gateway)
    prompter = ScriptedPrompter(
        select=["Source", "No filter"], text=[str(tmp_path / "w")], confirm=[True]
    )

    cli.invoke(
        app, ["backup"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert not any("latest inspect" in x for labels in prompter.select_labels for x in labels)
