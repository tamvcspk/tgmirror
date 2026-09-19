import json
from collections.abc import Callable

from typer.testing import CliRunner

from tests.fakes import FakeAuth, FakeGateway
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.errors import FloodWait
from tgmirror.core.gateway import ChatKind

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]


def joined(gateway: FakeGateway) -> None:
    gateway.add_channel("Daily News", username="daily_news", participants=12345, noforwards=True)
    gateway.add_channel("Cooking [pro]", is_admin=False, can_post=False)  # brackets: not markup
    gateway.add_channel("Chat Room", kind=ChatKind.SUPERGROUP, participants=87)


def test_table_lists_every_chat_with_its_kind(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    joined(gateway)

    result = runner.invoke(app, ["channels"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 0, result.output
    for expected in ("Daily News", "@daily_news", "12,345", "Cooking [pro]", "Chat Room"):
        assert expected in result.output
    assert "supergroup" in result.output and "channel" in result.output
    assert "3 channels/groups" in result.output


def test_json_output_is_machine_readable(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    joined(gateway)

    result = runner.invoke(app, ["channels", "--json"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["title"] for r in rows] == ["Daily News", "Cooking [pro]", "Chat Room"]
    assert rows[0]["kind"] == "broadcast" and rows[0]["noforwards"] is True
    assert rows[2]["kind"] == "supergroup"
    assert set(rows[0]) == {
        "id",
        "title",
        "kind",
        "username",
        "participants",
        "noforwards",
        "is_admin",
        "can_post",
    }


def test_search_matches_title_or_username_ignoring_case(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    joined(gateway)
    rt = make_runtime(gateway=gateway)

    by_title = runner.invoke(app, ["channels", "--json", "-s", "COOKING"], obj=rt)
    by_username = runner.invoke(app, ["channels", "--json", "--search", "daily_"], obj=rt)

    assert [r["title"] for r in json.loads(by_title.output)] == ["Cooking [pro]"]
    assert [r["title"] for r in json.loads(by_username.output)] == ["Daily News"]


def test_writable_keeps_only_chats_where_you_are_an_admin_who_can_post(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    joined(gateway)

    result = runner.invoke(
        app, ["channels", "--json", "--writable"], obj=make_runtime(gateway=gateway)
    )

    assert [r["title"] for r in json.loads(result.output)] == ["Daily News", "Chat Room"]


def test_no_match_says_so(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    joined(gateway)

    result = runner.invoke(app, ["channels", "--search", "zzz"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 0
    assert "No matching channels" in result.output


def test_logged_out_points_to_login(make_runtime: MakeRuntime) -> None:
    rt = make_runtime(auth=FakeAuth(logged_in=None))

    result = runner.invoke(app, ["channels"], obj=rt)

    assert result.exit_code == 1
    assert "tgmirror login" in result.output


def test_flood_wait_exits_3_with_a_sentence(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.fail_next("list_channels", FloodWait(45))

    result = runner.invoke(app, ["channels"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 3
    assert "45s" in result.output and "Traceback" not in result.output


def test_debug_flag_shows_the_traceback(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.fail_next("list_channels", FloodWait(45))

    result = runner.invoke(app, ["--debug", "channels"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 1  # click's exit for an unhandled exception
    assert isinstance(result.exception, FloodWait)
