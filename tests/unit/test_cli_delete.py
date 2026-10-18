"""``tgmirror delete`` through the real Typer app: no terminal, no network.

The command's promise (docs/07-inspect-transform.md section 6.3): nothing is deleted without a
confirmation that names the number of messages, and the plan that was shown is the one applied.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.gateway import ChannelInfo
from tgmirror.store.ops import Op, OpStatus
from tgmirror.store.runs import Control

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]
ADS = ["--hashtag", "#ads"]


def fill(gateway: FakeGateway, ads: int = 3, other: int = 2) -> ChannelInfo:
    chat = gateway.add_channel("Chat")
    for i in range(ads):
        gateway.add_message(chat.id, f"ad {i} #ads", hashtags=("#ads",))
    for i in range(other):
        gateway.add_message(chat.id, f"keep {i}")
    return chat


def ids(gateway: FakeGateway, chat: ChannelInfo) -> list[int]:
    return [m.id for m in gateway.messages[chat.id]]


def op_of(rt: Runtime, op_id: int = 1) -> Op | None:
    async def read() -> Op | None:
        async with opened_store(rt) as store:
            return await store.get_op(op_id)

    return asyncio.run(read())


def test_without_a_confirmation_it_only_saves_the_plan(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["delete", "Chat", *ADS], obj=rt)

    assert result.exit_code == 2
    assert "O1" in result.output and "3" in result.output
    assert "--resume O1 --yes --expect 3" in result.output
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]  # nothing deleted
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.PLANNED and op.planned == 3


def test_yes_with_the_right_expect_deletes_exactly_the_plan(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["delete", "Chat", *ADS, "--yes", "--expect", "3"], obj=rt)

    assert result.exit_code == 0, result.output
    assert ids(gateway, chat) == [4, 5]
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.DONE and op.done == 3
    assert "O1" in result.output


def test_yes_without_expect_deletes_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["delete", "Chat", *ADS, "--yes"], obj=rt)

    assert result.exit_code == 2
    assert "--expect" in result.output
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.CANCELLED


def test_a_wrong_expect_deletes_nothing(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["delete", "Chat", *ADS, "--yes", "--expect", "2"], obj=rt)

    assert result.exit_code == 2
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.CANCELLED


def test_an_empty_filter_needs_all(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    refused = cli.invoke(app, ["delete", "Chat", "--yes", "--expect", "5"], obj=rt)

    assert refused.exit_code == 2 and "--all" in refused.output
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]
    assert op_of(rt) is None  # not even a plan

    allowed = cli.invoke(app, ["delete", "Chat", "--all", "--yes", "--expect", "5"], obj=rt)

    assert allowed.exit_code == 0, allowed.output
    assert ids(gateway, chat) == []


def test_a_filter_selecting_nothing_says_so(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway, ads=0)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["delete", "Chat", *ADS, "--yes", "--expect", "0"], obj=rt)

    assert result.exit_code == 0
    assert "Chat" in result.output
    assert op_of(rt) is None


def test_a_chat_the_account_does_not_administer_is_refused(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    gateway.channels[chat.id] = replace(chat, is_admin=False)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["delete", "Chat", *ADS, "--yes", "--expect", "3"], obj=rt)

    assert result.exit_code == 4
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]


def test_resume_applies_a_saved_plan(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    assert cli.invoke(app, ["delete", "Chat", *ADS], obj=rt).exit_code == 2

    unconfirmed = cli.invoke(app, ["delete", "--resume", "O1"], obj=rt)
    assert unconfirmed.exit_code == 2 and ids(gateway, chat) == [1, 2, 3, 4, 5]

    result = cli.invoke(app, ["delete", "--resume", "O1", "--yes", "--expect", "3"], obj=rt)

    assert result.exit_code == 0, result.output
    assert ids(gateway, chat) == [4, 5]
    finished = cli.invoke(app, ["delete", "--resume", "O1", "--yes", "--expect", "3"], obj=rt)
    assert finished.exit_code == 2  # nothing left to do


def test_resume_does_not_take_a_source_or_a_filter(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["delete", "Chat", "--resume", "O1"], obj=rt)

    assert result.exit_code == 2


def test_at_a_terminal_the_count_is_typed_back(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    prompter = ScriptedPrompter(text=["3"])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["delete", "Chat", *ADS], obj=rt)

    assert result.exit_code == 0, result.output
    assert ids(gateway, chat) == [4, 5]
    assert any("3" in message for kind, message in prompter.asked if kind == "text")


def test_typing_another_number_deletes_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    prompter = ScriptedPrompter(text=["yes"])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["delete", "Chat", *ADS], obj=rt)

    assert result.exit_code == 1
    assert ids(gateway, chat) == [1, 2, 3, 4, 5]
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.CANCELLED


def test_history_lists_and_shows_the_operation(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    assert (
        cli.invoke(app, ["delete", "Chat", *ADS, "--yes", "--expect", "3"], obj=rt).exit_code == 0
    )

    listing = cli.invoke(app, ["history"], obj=rt)
    assert listing.exit_code == 0 and "O1" in listing.output

    detail = cli.invoke(app, ["history", "O1"], obj=rt)
    assert detail.exit_code == 0 and "Chat" in detail.output and "3" in detail.output

    as_json = cli.invoke(app, ["history", "O1", "--json"], obj=rt)
    record = json.loads(as_json.output)
    assert record["operation"] == 1 and record["done"] == 3 and record["status"] == "done"


def test_stop_and_pause_reach_a_live_operation(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    assert cli.invoke(app, ["delete", "Chat", *ADS], obj=rt).exit_code == 2

    async def start() -> None:
        async with opened_store(rt) as store:
            await store.start_op(1)

    asyncio.run(start())

    result = cli.invoke(app, ["stop"], obj=rt)

    assert result.exit_code == 0 and "O1" in result.output

    async def control() -> Control:
        async with opened_store(rt) as store:
            return await store.read_op_control(1)

    assert asyncio.run(control()) is Control.STOP
