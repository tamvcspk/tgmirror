"""``tgmirror repost`` through the real Typer app (Phase 20.4): no terminal, no network. The promise
is ``delete``'s (nothing happens without a confirmation that names how many) plus the repost's own:
the new messages are posted before any original goes, a chat that restricts saving content needs
decision D3's statement, and the originals are backed up first when they are to be deleted.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.gateway import ChannelInfo, MediaKind
from tgmirror.engine import backupdir
from tgmirror.store.ops import Op, OpKind, OpStatus

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]
ADS = ["--hashtag", "#ads"]
CONFIRMED = ["--yes", "--expect", "3"]


def fill(gateway: FakeGateway, ads: int = 3, other: int = 2) -> ChannelInfo:
    chat = gateway.add_channel("Chat")
    for i in range(ads):
        gateway.add_message(
            chat.id,
            f"ad {i} #ads",
            hashtags=("#ads",),
            media=MediaKind.PHOTO,
            size=5,
            file_key=f"d{100 + i}",
        )
    for i in range(other):
        gateway.add_message(chat.id, f"keep {i}")
    return chat


def texts(gateway: FakeGateway, chat: ChannelInfo) -> list[str]:
    return [m.text for m in gateway.messages[chat.id]]


def op_of(rt: Runtime, op_id: int = 1) -> Op | None:
    async def read() -> Op | None:
        async with opened_store(rt) as store:
            return await store.get_op(op_id)

    return asyncio.run(read())


ORIGINAL = ["ad 0 #ads", "ad 1 #ads", "ad 2 #ads", "keep 0", "keep 1"]
COPIES = ["ad 0 #ads", "ad 1 #ads", "ad 2 #ads"]


def test_without_a_confirmation_it_only_saves_the_plan_and_says_what_it_costs(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["repost", "Chat", *ADS], obj=rt)

    assert result.exit_code == 2
    assert "O1" in result.output and "--resume O1 --yes --expect 3" in result.output
    assert "UPLOADED AGAIN" in result.output or "TẢI LÊN LẠI" in result.output
    assert texts(gateway, chat) == ORIGINAL
    op = op_of(rt)
    assert op is not None and op.kind is OpKind.REPOST and op.status is OpStatus.PLANNED
    assert not op.delete_original and op.silent


def test_it_posts_silent_copies_at_the_end_and_keeps_the_originals(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["repost", "Chat", *ADS, *CONFIRMED], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, chat) == ORIGINAL + COPIES
    assert gateway.silent_sends == [True, True, True]
    file_keys = [m.file_key for m in gateway.messages[chat.id][:3]]
    assert [m.file_key for m in gateway.messages[chat.id][5:]] != file_keys  # new file ids
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.DONE and op.done == 3


def test_notify_posts_with_a_notification(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["repost", "Chat", *ADS, *CONFIRMED, "--notify"], obj=rt)

    assert result.exit_code == 0, result.output
    assert gateway.silent_sends == [False, False, False]


def test_a_wrong_expect_posts_nothing(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["repost", "Chat", *ADS, "--yes", "--expect", "2"], obj=rt)

    assert result.exit_code == 2
    assert texts(gateway, chat) == ORIGINAL
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.CANCELLED


def test_delete_original_backs_up_posts_and_only_then_deletes(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    directory = tmp_path / "net"

    result = cli.invoke(
        app,
        [
            "repost",
            "Chat",
            *ADS,
            *CONFIRMED,
            "--delete-original",
            "--backup-to",
            str(directory),
        ],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert texts(gateway, chat) == ["keep 0", "keep 1", *COPIES]
    assert [r.id for r in backupdir.iter_records(directory)] == [1, 2, 3]
    op = op_of(rt)
    assert op is not None and op.delete_original and op.backup_done
    calls = [c.method for c in gateway.calls]
    assert (
        calls.index("export_unit") < calls.index("send_prepared") < calls.index("delete_messages")
    )


def test_keeping_the_originals_asks_no_question_about_a_backup(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)
    prompter = ScriptedPrompter(text=["3"])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["repost", "Chat", *ADS], obj=rt)

    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in prompter.asked] == ["text"]  # only the count


def test_deleting_the_originals_asks_about_a_backup_at_a_terminal(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    prompter = ScriptedPrompter(text=["3"])  # the count; the two confirmations default to Yes
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["repost", "Chat", *ADS, "--delete-original"], obj=rt)

    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in prompter.asked] == ["text", "confirm", "confirm"]
    assert (rt.paths.data_dir / "safety" / "O1").exists()
    assert texts(gateway, chat) == ["keep 0", "keep 1", *COPIES]


def test_a_chat_that_restricts_saving_content_needs_the_statement(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    chat = fill(gateway)
    gateway.channels[chat.id] = replace(chat, noforwards=True)
    rt = make_runtime(gateway=gateway)

    refused = cli.invoke(app, ["repost", "Chat", *ADS, *CONFIRMED], obj=rt)

    assert refused.exit_code == 2 and "--yes-i-administer-this-channel" in refused.output
    assert texts(gateway, chat) == ORIGINAL and gateway.calls_to("prepare") == []

    allowed = cli.invoke(
        app, ["repost", "--resume", "O1", *CONFIRMED, "--yes-i-administer-this-channel"], obj=rt
    )

    assert allowed.exit_code == 0, allowed.output
    assert texts(gateway, chat) == ORIGINAL + COPIES


def test_resume_takes_only_a_repost_and_no_new_choice_of_originals(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    assert cli.invoke(app, ["repost", "Chat", *ADS], obj=rt).exit_code == 2

    wrong = cli.invoke(app, ["delete", "--resume", "O1", *CONFIRMED], obj=rt)
    changed = cli.invoke(app, ["repost", "--resume", "O1", "--delete-original", *CONFIRMED], obj=rt)

    assert wrong.exit_code == 2 and "tgmirror repost --resume O1" in wrong.output
    assert changed.exit_code == 2


def test_history_and_status_know_a_repost(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    cli.invoke(app, ["repost", "Chat", *ADS, *CONFIRMED], obj=rt)

    detail = cli.invoke(app, ["history", "O1"], obj=rt)
    status = json.loads(cli.invoke(app, ["status", "--json"], obj=rt).output)

    assert detail.exit_code == 0 and "repost" in detail.output.lower()
    assert status["kind"] == "repost" and status["done"] == 3
