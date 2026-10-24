"""``tgmirror topics split|merge|delete|rename|close|reopen`` through the real Typer app
(Phase 20b): no terminal, no network. A split and a merge are ``move`` operations: the promise is
``repost``'s (nothing changes without a confirmation that names how many, every original is
deleted only after its copy exists) plus the order of the messages in the topic they land in, and
that a topic is only deleted once it is empty.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.gateway import ChatKind, MediaKind
from tgmirror.engine import backupdir
from tgmirror.store.ops import Op, OpKind, OpStatus

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]
ROUTE_B = "topics:\n  route:\n    - {when: {hashtag: ['#b']}, to: 'B'}\n"
ROUTE_B_INTO_NEWS = "topics:\n  route:\n    - {when: {hashtag: ['#b']}, to: 'News'}\n"


@pytest.fixture
def make_runtime(make_runtime: MakeRuntime) -> MakeRuntime:
    def factory(**kw: Any) -> Runtime:
        rt = make_runtime(**kw)
        rt.paths.config_dir.mkdir(parents=True, exist_ok=True)
        rt.paths.config_file.write_text(
            "[limits]\nmin_delay = 0.01\nmax_delay = 0.02\nread_delay = 0.01\n"
            "long_pause_range = [0, 0]\n",
            encoding="utf-8",
        )
        return rt

    return factory


class Forum:
    """A forum with three topics whose messages are interleaved, as a real one's are."""

    def __init__(self, gateway: FakeGateway, **kw: Any) -> None:
        self.gateway = gateway
        self.chat = gateway.add_channel("Forum", kind=ChatKind.FORUM, **kw)
        self.misc = gateway.add_topic(self.chat.id, "Misc").id  # 2
        self.news = gateway.add_topic(self.chat.id, "News").id  # 3
        self.old = gateway.add_topic(self.chat.id, "Old").id  # 4
        add = gateway.add_message
        add(self.chat.id, "m1", topic_id=self.misc)  # 1
        add(self.chat.id, "b1 #b", topic_id=self.misc, hashtags=("#b",))  # 2
        add(self.chat.id, "n1", topic_id=self.news)  # 3
        add(self.chat.id, "m2", topic_id=self.misc)  # 4
        add(self.chat.id, "b2 #b", topic_id=self.misc, hashtags=("#b",))  # 5
        add(self.chat.id, "o1", topic_id=self.old)  # 6
        add(self.chat.id, "n2", topic_id=self.news)  # 7
        add(self.chat.id, "o2", topic_id=self.old)  # 8

    def texts(self, topic_id: int | None) -> list[str]:
        return [m.text for m in self.gateway.messages[self.chat.id] if m.topic_id == topic_id]

    def titles(self) -> list[str]:
        return [t.title for t in self.gateway.topics.get(self.chat.id, [])]

    def topic_id(self, title: str) -> int:
        return next(t.id for t in self.gateway.topics[self.chat.id] if t.title == title)


def route_file(tmp_path: Path, body: str = ROUTE_B) -> list[str]:
    path = tmp_path / "route.yaml"
    path.write_text(body, encoding="utf-8")
    return ["--transform", str(path)]


def op_of(rt: Runtime, op_id: int = 1) -> Op | None:
    async def read() -> Op | None:
        async with opened_store(rt) as store:
            return await store.get_op(op_id)

    return asyncio.run(read())


# ---- listing still works as it did ------------------------------------------------------------


def test_the_forum_is_listed_by_a_title_or_after_a_double_dash_by_a_marked_id(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)

    by_title = cli.invoke(app, ["topics", "Forum"], obj=rt)
    bare = str(forum.chat.id).lstrip("-")
    by_id = cli.invoke(app, ["topics", "--json", "--", f"-{bare}"], obj=rt)

    assert by_title.exit_code == 0 and "Misc" in by_title.output
    assert by_id.exit_code == 0, by_id.output
    assert [row["title"] for row in json.loads(by_id.output)] == ["General", "Misc", "News", "Old"]


# ---- split --------------------------------------------------------------------------------------


def test_a_split_without_a_confirmation_only_saves_the_plan(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)
    before = list(gateway.messages[forum.chat.id])

    result = cli.invoke(app, ["topics", "split", "Forum", "Misc", *route_file(tmp_path)], obj=rt)

    assert result.exit_code == 2
    assert "'B'" in result.output and "new topic" in result.output
    assert "tgmirror topics move --resume O1 --yes --expect 2" in result.output
    assert gateway.messages[forum.chat.id] == before and forum.titles() == ["Misc", "News", "Old"]
    op = op_of(rt)
    assert op is not None and op.kind is OpKind.MOVE and op.status is OpStatus.PLANNED
    assert op.planned == 2 and op.delete_original


def test_a_split_posts_the_messages_in_their_order_in_a_new_topic_then_deletes_them(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app,
        ["topics", "split", "Forum", "Misc", *route_file(tmp_path), "--yes", "--expect", "2"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert forum.texts(forum.misc) == ["m1", "m2"]
    assert forum.texts(forum.topic_id("B")) == ["b1 #b", "b2 #b"]
    assert forum.texts(forum.news) == ["n1", "n2"]  # no other topic was touched
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.DONE and op.done == 2
    assert all(silent for silent in gateway.silent_sends)  # moving many messages is quiet


def test_a_split_into_a_topic_that_has_messages_puts_them_after_those(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)
    args = ["topics", "split", "Forum", "Misc", *route_file(tmp_path, ROUTE_B_INTO_NEWS)]

    plan = cli.invoke(app, args, obj=rt)
    assert plan.exit_code == 2 and "after those already there" in plan.output

    result = cli.invoke(app, [*args, "--yes", "--expect", "2"], obj=rt)

    assert result.exit_code == 0, result.output
    assert forum.texts(forum.news) == ["n1", "n2", "b1 #b", "b2 #b"]
    assert forum.titles() == ["Misc", "News", "Old"]  # nothing was created


def test_a_reply_between_moved_messages_keeps_answering_the_moved_one(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway)
    gateway.add_message(  # 9: answers b1 (id 2), is itself routed to B
        forum.chat.id, "b3 #b", topic_id=forum.misc, hashtags=("#b",), reply_to_id=2
    )
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app,
        ["topics", "split", "Forum", "Misc", *route_file(tmp_path), "--yes", "--expect", "3"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    moved = [m for m in gateway.messages[forum.chat.id] if m.topic_id == forum.topic_id("B")]
    assert [m.text for m in moved] == ["b1 #b", "b2 #b", "b3 #b"]
    assert moved[2].reply_to_id == moved[0].id  # the copy of b1, not the (deleted) original


def test_a_wrong_expect_moves_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app,
        ["topics", "split", "Forum", "Misc", *route_file(tmp_path), "--yes", "--expect", "5"],
        obj=rt,
    )

    assert result.exit_code == 2
    assert forum.texts(forum.misc) == ["m1", "b1 #b", "m2", "b2 #b"]
    op = op_of(rt)
    assert op is not None and op.status is OpStatus.CANCELLED


def test_a_split_needs_routing_rules_and_changes_no_text(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    Forum(gateway)
    rt = make_runtime(gateway=gateway)
    captions = "caption:\n  - append: 'x'\n" + ROUTE_B

    none = cli.invoke(app, ["topics", "split", "Forum", "Misc"], obj=rt)
    text = cli.invoke(
        app, ["topics", "split", "Forum", "Misc", *route_file(tmp_path, captions)], obj=rt
    )

    assert none.exit_code == 2 and "routing rules" in none.output
    assert text.exit_code == 2 and "caption" in text.output
    assert op_of(rt) is None


def test_the_hashtag_flag_is_a_routing_rule_too(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app,
        ["topics", "split", "Forum", "Misc", "--hashtag-to-topic", "--yes", "--expect", "2"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert forum.texts(forum.misc) == ["m1", "m2"]
    assert "#b" in forum.titles() or "B" in forum.titles()


def test_a_forum_that_restricts_saving_needs_the_statement(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway, noforwards=True)
    rt = make_runtime(gateway=gateway)
    args = ["topics", "split", "Forum", "Misc", *route_file(tmp_path), "--yes", "--expect", "2"]

    refused = cli.invoke(app, args, obj=rt)
    assert refused.exit_code == 2 and "--yes-i-administer-this-channel" in refused.output
    assert forum.texts(forum.misc) == ["m1", "b1 #b", "m2", "b2 #b"]

    allowed = cli.invoke(app, [*args, "--yes-i-administer-this-channel"], obj=rt)
    assert allowed.exit_code == 0, allowed.output


def test_the_originals_can_be_backed_up_first(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)
    directory = tmp_path / "net"

    result = cli.invoke(
        app,
        [
            "topics",
            "split",
            "Forum",
            "Misc",
            *route_file(tmp_path),
            "--yes",
            "--expect",
            "2",
            "--backup-to",
            str(directory),
        ],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert [r.id for r in backupdir.iter_records(directory)] == [2, 5]
    assert forum.texts(forum.topic_id("B")) == ["b1 #b", "b2 #b"]


def test_a_saved_plan_is_applied_by_move_resume_but_only_a_move(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)
    assert (
        cli.invoke(
            app, ["topics", "split", "Forum", "Misc", *route_file(tmp_path)], obj=rt
        ).exit_code
        == 2
    )

    wrong = cli.invoke(app, ["delete", "--resume", "O1", "--yes", "--expect", "2"], obj=rt)
    assert wrong.exit_code == 2 and "tgmirror topics move --resume O1" in wrong.output
    assert forum.texts(forum.misc) == ["m1", "b1 #b", "m2", "b2 #b"]

    result = cli.invoke(app, ["topics", "move", "--resume", "O1", "--yes", "--expect", "2"], obj=rt)

    assert result.exit_code == 0, result.output
    assert forum.texts(forum.topic_id("B")) == ["b1 #b", "b2 #b"]
    detail = cli.invoke(app, ["history", "O1"], obj=rt)
    assert detail.exit_code == 0 and "move messages between topics" in detail.output


def test_the_forum_must_be_administered_and_be_a_forum(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    forum = Forum(gateway)
    plain = gateway.add_channel("Plain")
    rt = make_runtime(gateway=gateway)
    gateway.channels[forum.chat.id] = replace(forum.chat, is_admin=False)

    not_admin = cli.invoke(app, ["topics", "split", "Forum", "Misc", *route_file(tmp_path)], obj=rt)
    not_forum = cli.invoke(app, ["topics", "merge", "Plain", "A", "B"], obj=rt)

    assert not_admin.exit_code == 4
    assert not_forum.exit_code == 2 and plain.title in not_forum.output


# ---- merge --------------------------------------------------------------------------------------


def test_a_merge_appends_each_topic_in_turn_then_deletes_the_emptied_ones(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)
    args = ["topics", "merge", "Forum", "News", "Old", "Misc"]

    plan = cli.invoke(app, args, obj=rt)
    assert plan.exit_code == 2
    assert "'Old' and 'Misc'" not in plan.output and "Old" in plan.output

    result = cli.invoke(app, [*args, "--yes", "--expect", "6"], obj=rt)

    assert result.exit_code == 0, result.output
    assert forum.texts(forum.news) == ["n1", "n2", "o1", "o2", "m1", "b1 #b", "m2", "b2 #b"]
    assert forum.titles() == ["News"]  # Old and Misc are gone, with nothing left in them
    op = op_of(rt, 2)
    assert op is not None and op.topics_dropped == 2 and op.topics_kept == 0
    assert "2 topics deleted" in result.output


def test_a_topic_that_still_holds_a_message_is_not_deleted(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    gateway.add_message(  # a poll cannot be posted again: it stays, and so does its topic
        forum.chat.id, "vote", media=MediaKind.POLL, topic_id=forum.old, poll_options=("a", "b")
    )
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(
        app, ["topics", "merge", "Forum", "News", "Old", "--yes", "--expect", "3"], obj=rt
    )

    assert result.exit_code == 0, result.output
    assert forum.texts(forum.news) == ["n1", "n2", "o1", "o2"]
    assert forum.texts(forum.old) == ["vote"] and "Old" in forum.titles()
    op = op_of(rt)
    assert op is not None and op.topics_kept == 1 and op.topics_dropped == 0
    assert "Kept topic" in result.output


def test_a_merge_into_general_works_but_general_is_never_merged_away(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)

    refused = cli.invoke(app, ["topics", "merge", "Forum", "News", "General"], obj=rt)
    into = cli.invoke(
        app, ["topics", "merge", "Forum", "General", "Old", "--yes", "--expect", "2"], obj=rt
    )

    assert refused.exit_code == 2 and "General" in refused.output
    assert into.exit_code == 0, into.output
    assert forum.texts(None) == ["o1", "o2"] and "Old" not in forum.titles()


def test_a_merge_needs_two_different_topics_that_exist(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    Forum(gateway)
    rt = make_runtime(gateway=gateway)

    one = cli.invoke(app, ["topics", "merge", "Forum", "News"], obj=rt)
    twice = cli.invoke(app, ["topics", "merge", "Forum", "News", "news"], obj=rt)
    unknown = cli.invoke(app, ["topics", "merge", "Forum", "News", "Nowhere"], obj=rt)

    assert one.exit_code == 2 and twice.exit_code == 2 and unknown.exit_code == 2
    assert "Nowhere" in unknown.output
    assert op_of(rt) is None


def test_at_a_terminal_a_merge_asks_for_the_topics_and_the_count(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    prompter = ScriptedPrompter(
        select=["Forum", "News"], checkbox=[["Old (id 4)"]], text=["2"], confirm=[False]
    )
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = cli.invoke(app, ["topics", "merge"], obj=rt)

    assert result.exit_code == 0, result.output
    assert forum.texts(forum.news) == ["n1", "n2", "o1", "o2"]
    assert [kind for kind, _ in prompter.asked] == [
        "select",
        "select",
        "checkbox",
        "text",
        "confirm",
    ]


# ---- delete a topic -----------------------------------------------------------------------------


def test_deleting_a_topic_deletes_its_messages_and_then_the_topic(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)

    plan = cli.invoke(app, ["topics", "delete", "Forum", "Old"], obj=rt)
    assert plan.exit_code == 2 and "will be deleted together with the 2 messages" in plan.output
    assert forum.texts(forum.old) == ["o1", "o2"]

    result = cli.invoke(app, ["topics", "delete", "Forum", "Old", "--yes", "--expect", "2"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Old" not in forum.titles() and forum.texts(forum.old) == []
    assert forum.texts(forum.news) == ["n1", "n2"]
    op = op_of(rt, 2)
    assert op is not None and op.kind is OpKind.DELETE and op.topics_dropped == 1


def test_deleting_an_empty_topic_asks_once_and_general_cannot_be_deleted(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    empty = gateway.add_topic(forum.chat.id, "Empty")
    rt = make_runtime(gateway=gateway)

    needs_yes = cli.invoke(app, ["topics", "delete", "Forum", "Empty"], obj=rt)
    assert needs_yes.exit_code == 2 and "Empty" in forum.titles()
    done = cli.invoke(app, ["topics", "delete", "Forum", str(empty.id), "--yes"], obj=rt)
    general = cli.invoke(app, ["topics", "delete", "Forum", "General", "--yes"], obj=rt)

    assert done.exit_code == 0, done.output
    assert "Empty" not in forum.titles()
    assert general.exit_code == 2 and "General" in general.output


# ---- rename, close, reopen ------------------------------------------------------------


def test_rename_close_and_reopen_change_the_topic_and_no_message(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    rt = make_runtime(gateway=gateway)
    messages = list(gateway.messages[forum.chat.id])

    refused = cli.invoke(app, ["topics", "rename", "Forum", "News", "Headlines"], obj=rt)
    renamed = cli.invoke(app, ["topics", "rename", "Forum", "News", "Headlines", "--yes"], obj=rt)
    closed = cli.invoke(app, ["topics", "close", "Forum", "Headlines", "--yes"], obj=rt)
    closed_state = next(t.closed for t in gateway.topics[forum.chat.id] if t.title == "Headlines")
    reopened = cli.invoke(app, ["topics", "reopen", "Forum", "headlines", "--yes"], obj=rt)

    assert refused.exit_code == 2  # no terminal, no --yes: asked nothing, changed nothing
    assert renamed.exit_code == 0 and closed.exit_code == 0 and reopened.exit_code == 0
    assert forum.titles() == ["Misc", "Headlines", "Old"]
    assert closed_state is True
    assert next(t.closed for t in gateway.topics[forum.chat.id] if t.title == "Headlines") is False
    assert gateway.messages[forum.chat.id] == messages


def test_at_a_terminal_a_rename_asks_yes_or_no(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    forum = Forum(gateway)
    declined = ScriptedPrompter(confirm=[False])
    rt = make_runtime(gateway=gateway, prompter=declined, interactive=True)

    no = cli.invoke(app, ["topics", "rename", "Forum", "News", "Headlines"], obj=rt)
    assert no.exit_code != 0 and forum.titles() == ["Misc", "News", "Old"]

    yes_rt = replace(rt, prompter=ScriptedPrompter(confirm=[True]))
    yes = cli.invoke(app, ["topics", "rename", "Forum", "News", "Headlines"], obj=yes_rt)
    assert yes.exit_code == 0, yes.output
    assert forum.titles() == ["Misc", "Headlines", "Old"]


def test_general_cannot_be_renamed_or_closed(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    Forum(gateway)
    rt = make_runtime(gateway=gateway)

    result = cli.invoke(app, ["topics", "close", "Forum", "General", "--yes"], obj=rt)

    assert result.exit_code == 2 and "General" in result.output
