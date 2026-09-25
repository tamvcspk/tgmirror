import json
from collections.abc import Callable

from typer.testing import CliRunner

from tests.fakes import FakeGateway
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import ChatKind

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]


def test_table_lists_general_and_every_added_topic(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_topic(forum.id, "Announcements")
    gateway.add_topic(forum.id, "Off-topic", closed=True)

    result = runner.invoke(app, ["topics", "Forum"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 0, result.output
    assert "General" in result.output
    assert "Announcements" in result.output and "Off-topic" in result.output
    assert "3 topics" in result.output


def test_json_output_is_machine_readable(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    forum = gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_topic(forum.id, "Announcements")

    result = runner.invoke(app, ["topics", "Forum", "--json"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["title"] for r in rows] == ["General", "Announcements"]
    assert rows[0]["id"] == 1 and set(rows[0]) == {"id", "title", "closed"}


def test_a_non_forum_source_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Channel")

    result = runner.invoke(app, ["topics", "Channel"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 2
    assert "not a forum" in result.output.lower()


def test_unknown_source_exits_2(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    result = runner.invoke(app, ["topics", "Nope"], obj=make_runtime(gateway=gateway))

    assert result.exit_code == 2
