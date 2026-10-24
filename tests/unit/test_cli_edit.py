"""``tgmirror edit`` and ``tgmirror undo`` through the real Typer app (Phase 20.3): no terminal, no
network. The promise is ``delete``'s: nothing is changed without a confirmation that names how
many, and the plan that was shown is the one applied; plus ``undo`` gives the texts back.
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.gateway import ChannelInfo
from tgmirror.store.ops import Op, OpKind, OpStatus

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]
ADS = ["--hashtag", "#ads"]
CONFIRMED = ["--yes", "--expect", "3"]
STRIP = "version: 1\ncaption:\n  - remove_hashtags: ['#ads']\n  - text_too: true\n"


def transform(tmp_path: Path, text: str = STRIP) -> list[str]:
    path = tmp_path / "t.yaml"
    path.write_text(text, encoding="utf-8")
    return ["--transform", str(path)]


def fill(gateway: FakeGateway, ads: int = 3, other: int = 2) -> ChannelInfo:
    chat = gateway.add_channel("Chat")
    for i in range(ads):
        gateway.add_message(chat.id, f"ad {i} #ads", hashtags=("#ads",))
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
EDITED = ["ad 0", "ad 1", "ad 2", "keep 0", "keep 1"]


def test_without_a_confirmation_it_only_saves_the_plan_and_shows_the_diff(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["edit", "Chat", *ADS, *transform(tmp_path)], obj=rt)

    assert result.exit_code == 2
    assert "O1" in result.output and "ad 0 #ads" in result.output and "ad 0" in result.output
    assert "--resume O1 --yes --expect 3" in result.output
    assert texts(gateway, chat) == ORIGINAL  # nothing edited
    op = op_of(rt)
    assert op is not None and op.kind is OpKind.EDIT and op.status is OpStatus.PLANNED


def test_yes_with_the_right_expect_edits_exactly_the_plan(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["edit", "Chat", *ADS, *CONFIRMED, *transform(tmp_path)], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, chat) == EDITED
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.DONE and op.done == 3
    assert "tgmirror undo O1" in result.output  # how to get them back


def test_a_wrong_expect_edits_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app, ["edit", "Chat", *ADS, "--yes", "--expect", "2", *transform(tmp_path)], obj=rt
    )

    assert result.exit_code == 2
    assert texts(gateway, chat) == ORIGINAL
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.CANCELLED


def test_it_needs_a_transform_with_caption_operations(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)

    none = cli.invoke(app, ["edit", "Chat", *ADS, *CONFIRMED], obj=rt)
    empty = cli.invoke(
        app, ["edit", "Chat", *ADS, *CONFIRMED, *transform(tmp_path, "version: 1\n")], obj=rt
    )

    assert none.exit_code == 2 and empty.exit_code == 2
    assert "--transform" in none.output
    assert op_of(rt) is None


def test_a_transform_that_is_about_copying_is_refused(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    routing = (
        "version: 1\ncaption:\n  - remove_hashtags: all\n"
        "topics:\n  topic_map:\n    A: B\nreply: keep\n"
    )

    result = cli.invoke(
        app, ["edit", "Chat", *ADS, *CONFIRMED, *transform(tmp_path, routing)], obj=rt
    )

    assert result.exit_code == 2
    assert "topics" in result.output and "reply: keep" in result.output
    assert texts(gateway, chat) == ORIGINAL and op_of(rt) is None


def test_an_empty_filter_needs_all(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)

    refused = cli.invoke(
        app, ["edit", "Chat", "--yes", "--expect", "3", *transform(tmp_path)], obj=rt
    )
    allowed = cli.invoke(
        app, ["edit", "Chat", "--all", "--yes", "--expect", "3", *transform(tmp_path)], obj=rt
    )

    assert refused.exit_code == 2 and "--all" in refused.output
    assert allowed.exit_code == 0, allowed.output


def test_a_transform_that_changes_nothing_says_so(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    other = "version: 1\ncaption:\n  - remove_hashtags: ['#zzz']\n  - text_too: true\n"

    result = cli.invoke(
        app, ["edit", "Chat", *ADS, *CONFIRMED, *transform(tmp_path, other)], obj=rt
    )

    assert result.exit_code == 0
    assert "Chat" in result.output and op_of(rt) is None


def test_at_a_terminal_the_count_is_typed_back_and_there_is_no_backup_question(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    prompter = ScriptedPrompter(text=["3"])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["edit", "Chat", *ADS, *transform(tmp_path)], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, chat) == EDITED
    assert [kind for kind, _ in prompter.asked] == ["text"]  # the originals are in the plan


def test_resume_applies_a_saved_plan_but_only_of_its_own_kind(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    assert cli.invoke(app, ["edit", "Chat", *ADS, *transform(tmp_path)], obj=rt).exit_code == 2

    wrong = cli.invoke(app, ["delete", "--resume", "O1", *CONFIRMED], obj=rt)
    assert wrong.exit_code == 2 and "tgmirror edit --resume O1" in wrong.output
    assert texts(gateway, chat) == ORIGINAL

    result = cli.invoke(app, ["edit", "--resume", "O1", *CONFIRMED], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, chat) == EDITED


def test_undo_plans_confirms_and_puts_the_originals_back(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    assert (
        cli.invoke(app, ["edit", "Chat", *ADS, *CONFIRMED, *transform(tmp_path)], obj=rt).exit_code
        == 0
    )

    saved = cli.invoke(app, ["undo", "O1"], obj=rt)  # no terminal, no --yes: only the plan
    assert saved.exit_code == 2 and "--resume O2 --yes --expect 3" in saved.output
    assert texts(gateway, chat) == EDITED

    result = cli.invoke(app, ["undo", "--resume", "O2", *CONFIRMED], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, chat) == ORIGINAL
    again = cli.invoke(app, ["undo", "O1", *CONFIRMED], obj=rt)
    assert again.exit_code == 2 and "O2" in again.output  # already undone


def test_undo_without_a_number_takes_the_latest_edit(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    rt = make_runtime(gateway=gateway)
    nothing = cli.invoke(app, ["undo", *CONFIRMED], obj=rt)
    assert nothing.exit_code == 2

    cli.invoke(app, ["edit", "Chat", *ADS, *CONFIRMED, *transform(tmp_path)], obj=rt)
    result = cli.invoke(app, ["undo", *CONFIRMED], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, chat) == ORIGINAL


def test_undo_of_something_that_is_not_an_edit_is_refused(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    assert cli.invoke(app, ["delete", "Chat", *ADS, *CONFIRMED], obj=rt).exit_code == 0

    result = cli.invoke(app, ["undo", "O1", *CONFIRMED], obj=rt)

    assert result.exit_code == 2 and "not an edit" in result.output


def test_history_and_status_know_edits_and_undos(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    fill(gateway)
    rt = make_runtime(gateway=gateway)
    cli.invoke(app, ["edit", "Chat", *ADS, *CONFIRMED, *transform(tmp_path)], obj=rt)
    cli.invoke(app, ["undo", "O1", *CONFIRMED], obj=rt)

    edit = cli.invoke(app, ["history", "O1"], obj=rt)
    undo = cli.invoke(app, ["history", "O2"], obj=rt)
    status = json.loads(cli.invoke(app, ["status", "--json"], obj=rt).output)

    assert edit.exit_code == 0 and "edit" in edit.output.lower()
    assert undo.exit_code == 0 and "O1" in undo.output  # says what it undoes
    assert status["kind"] == "undo" and status["operation"] == 2 and status["done"] == 3


def test_history_names_the_messages_telegram_would_not_let_us_edit(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    chat = fill(gateway)
    gateway.uneditable.add((chat.id, 2))
    rt = make_runtime(gateway=gateway)
    cli.invoke(app, ["edit", "Chat", *ADS, *CONFIRMED, *transform(tmp_path)], obj=rt)

    detail = cli.invoke(app, ["history", "O1"], obj=rt)

    assert "id 2: MESSAGE_AUTHOR_REQUIRED" in detail.output


# ---- --mirror (phase 20.5): the copies of a pair -------------------------------------------

LINKS = "version: 1\nlinks: rewrite\n"


def cloned_pair(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> tuple[Runtime, ChannelInfo, ChannelInfo]:
    """A source with links among its messages, cloned verbatim into a new chat: the copies still
    point at the source (what a clone leaves when it does not rewrite links)."""
    src = gateway.add_channel("Source", username="srcchan")
    gateway.add_message(src.id, "first #keep", hashtags=("#keep",))
    gateway.add_message(src.id, "see t.me/srcchan/1 and t.me/srcchan/4")
    gateway.add_message(src.id, "elsewhere t.me/other/1 and gone t.me/srcchan/9")
    gateway.add_message(src.id, "last")
    rt = make_runtime(gateway=gateway)
    cloned = cli.invoke(app, ["clone", "--src", "Source", "--dst-new", "Copy", "--yes"], obj=rt)
    assert cloned.exit_code == 0, cloned.output
    dst = next(c for c in gateway.channels.values() if c.title == "Copy")
    return rt, src, dst


def test_mirror_points_the_links_of_the_copies_at_the_copies(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    rt, _, dst = cloned_pair(make_runtime, gateway)
    bare = str(dst.id).removeprefix("-100").removeprefix("-")
    gateway.add_message(dst.id, "not from the pair: t.me/srcchan/1")  # somebody else's post
    before = texts(gateway, dst)

    result = cli.invoke(
        app,
        ["edit", "--mirror", "R1", *transform(tmp_path, LINKS), "--yes", "--expect", "1"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    after = texts(gateway, dst)
    assert after[1] == f"see https://t.me/c/{bare}/1 and https://t.me/c/{bare}/4"
    # a link into another chat is not ours, nor one to a message the pair never copied
    assert after[2] == "elsewhere t.me/other/1 and gone t.me/srcchan/9"
    assert after[4] == before[4]  # the message the pair did not post is left alone
    assert "Source" in result.output and "R1" in result.output  # the plan says whose copies

    undone = cli.invoke(app, ["undo", "--yes", "--expect", "1"], obj=rt)
    assert undone.exit_code == 0, undone.output
    assert texts(gateway, dst) == before


def test_mirror_edits_only_the_copies_with_a_caption_operation_too(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    rt, _, dst = cloned_pair(make_runtime, gateway)
    gateway.add_message(dst.id, "mine #keep", hashtags=("#keep",))  # not a copy of the pair
    strip = "version: 1\ncaption:\n  - remove_hashtags: ['#keep']\n  - text_too: true\n"

    result = cli.invoke(
        app,
        ["edit", "--mirror", "1", *transform(tmp_path, strip), "--yes", "--expect", "1"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst)[0] == "first"
    assert texts(gateway, dst)[-1] == "mine #keep"


def test_links_rewrite_needs_a_pair(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    rt, _, dst = cloned_pair(make_runtime, gateway)

    result = cli.invoke(
        app,
        ["edit", "Copy", "--all", *transform(tmp_path, LINKS), "--yes", "--expect", "1"],
        obj=rt,
    )

    assert result.exit_code == 2 and "--mirror" in result.output


def test_mirror_does_not_go_with_a_source_or_an_unknown_run(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    rt, _, _ = cloned_pair(make_runtime, gateway)

    both = cli.invoke(app, ["edit", "Copy", "--mirror", "R1", *transform(tmp_path, LINKS)], obj=rt)
    unknown = cli.invoke(app, ["edit", "--mirror", "R99", *transform(tmp_path, LINKS)], obj=rt)

    assert both.exit_code == 2 and unknown.exit_code == 2
