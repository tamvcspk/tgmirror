"""Naming a channel on the command line: the marked-id-as-option mistake (cli/refs.py)."""

from collections.abc import Callable

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeGateway
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import ChatKind

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]


@pytest.mark.parametrize("command", ["inspect", "topics", "backup"])
def test_a_marked_id_as_an_argument_gets_a_hint_not_no_such_option(
    make_runtime: MakeRuntime, command: str
) -> None:
    result = cli.invoke(app, [command, "-1002428805566"], obj=make_runtime())

    assert result.exit_code == 2
    assert "No such option" not in result.output
    assert "1002428805566" in result.output and "--" in result.output


def test_a_real_typo_in_an_option_is_still_reported_as_one(make_runtime: MakeRuntime) -> None:
    result = cli.invoke(app, ["inspect", "--nonsense"], obj=make_runtime())

    assert result.exit_code == 2 and "No such option" in result.output


def test_the_id_works_without_the_minus_and_after_double_dash(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_message(forum.id, "a")
    rt = make_runtime(gateway=gateway)
    full = str(forum.id)  # -1001000000001

    bare = cli.invoke(app, ["inspect", full.removeprefix("-"), "--by", "media"], obj=rt)
    dashed = cli.invoke(app, ["inspect", "--by", "media", "--", full], obj=rt)

    assert bare.exit_code == 0, bare.output
    assert dashed.exit_code == 0, dashed.output
    assert "Read 1 messages" in bare.output and "Read 1 messages" in dashed.output
