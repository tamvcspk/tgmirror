"""``tgmirror sync-changes`` through the real Typer app (Phase 17): no terminal, no network. The
promises are those of ``edit`` and ``delete``: nothing changes without a confirmation that names how
many, the plan that was shown is the one applied, every copy is read again right before it is
touched; plus the source is the authority only while it can be read, and a copy whose source
turns up again is never deleted.
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
from tgmirror.core.gateway import ChannelInfo, MediaKind
from tgmirror.engine import backupdir
from tgmirror.engine.sync import plan_sync
from tgmirror.store.ops import Op, OpKind, OpStatus

cli = CliRunner()
MakeRuntime = Callable[..., Runtime]
SYNC = ["sync-changes"]
ORIGINAL = ["one", "two", "three", "four"]


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


def texts(gateway: FakeGateway, chat: ChannelInfo) -> list[str]:
    return [m.text for m in gateway.messages[chat.id]]


def ids(gateway: FakeGateway, chat: ChannelInfo) -> list[int]:
    return [m.id for m in gateway.messages[chat.id]]


def op_of(rt: Runtime, op_id: int) -> Op | None:
    async def read() -> Op | None:
        async with opened_store(rt) as store:
            return await store.get_op(op_id)

    return asyncio.run(read())


def edit_source(gateway: FakeGateway, chat: ChannelInfo, msg_id: int, text: str) -> None:
    asyncio.run(gateway.edit_message(chat.id, msg_id, text))


def pair(
    make_runtime: MakeRuntime,
    gateway: FakeGateway,
    messages: list[str] | None = None,
    extra: list[str] | None = None,
) -> tuple[Runtime, ChannelInfo, ChannelInfo]:
    """A source cloned into a new chat ``Copy``: its messages are copied, nothing else happened."""
    src = gateway.add_channel("Source", username="srcchan")
    for text in messages or ORIGINAL:
        gateway.add_message(src.id, text)
    rt = make_runtime(gateway=gateway)
    cloned = cli.invoke(
        app, ["clone", "--src", "Source", "--dst-new", "Copy", "--yes", *(extra or [])], obj=rt
    )
    assert cloned.exit_code == 0, cloned.output
    dst = next(c for c in gateway.channels.values() if c.title == "Copy")
    return rt, src, dst


def test_an_edit_at_the_source_is_carried_to_the_copy(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 2, "two, edited")

    result = cli.invoke(app, [*SYNC, "--yes", "--expect", "1"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["one", "two, edited", "three", "four"]
    op = op_of(rt, 1)
    assert op is not None and op.kind is OpKind.EDIT and op.status is OpStatus.DONE
    assert op.done == 1 and op.sync_src == src.id and op.chat_id == dst.id
    assert "tgmirror undo O1" in result.output  # an edit can be put back


def test_a_deleted_source_message_takes_its_copy_with_it(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    gateway.delete_message(src.id, 3)

    result = cli.invoke(app, [*SYNC, "--yes", "--expect", "1"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["one", "two", "four"]
    assert texts(gateway, src) == ["one", "two", "four"]
    op = op_of(rt, 1)
    assert op is not None and op.kind is OpKind.DELETE and op.status is OpStatus.DONE
    assert op.done == 1 and op.sync_src == src.id


def test_without_a_confirmation_both_plans_are_only_saved(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 2, "two, edited")
    gateway.delete_message(src.id, 4)

    result = cli.invoke(app, SYNC, obj=rt)

    assert result.exit_code == 2
    assert "two, edited" in result.output and "O1" in result.output and "O2" in result.output
    assert "tgmirror edit --resume O1 --yes --expect 1" in result.output
    assert "tgmirror delete --resume O2 --yes --expect 1" in result.output
    assert texts(gateway, dst) == ORIGINAL  # nothing touched
    assert [op_of(rt, n).status for n in (1, 2)] == [OpStatus.PLANNED] * 2  # type: ignore[union-attr]

    # the saved plans are ordinary operations: each resumes on its own
    assert (
        cli.invoke(app, ["edit", "--resume", "O1", "--yes", "--expect", "1"], obj=rt).exit_code == 0
    )
    assert (
        cli.invoke(app, ["delete", "--resume", "O2", "--yes", "--expect", "1"], obj=rt).exit_code
        == 0
    )
    assert texts(gateway, dst) == ["one", "two, edited", "three"]


def test_the_expected_number_is_the_total_of_edits_and_deletes(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 1, "one, edited")
    gateway.delete_message(src.id, 4)

    wrong = cli.invoke(app, [*SYNC, "--yes", "--expect", "1"], obj=rt)
    missing = cli.invoke(app, [*SYNC, "--yes"], obj=rt)

    assert wrong.exit_code == 2 and missing.exit_code == 2
    assert texts(gateway, dst) == ORIGINAL
    cancelled = [op_of(rt, n) for n in (1, 2, 3, 4)]
    assert all(op is not None and op.status is OpStatus.CANCELLED for op in cancelled)

    right = cli.invoke(app, [*SYNC, "--yes", "--expect", "2"], obj=rt)

    assert right.exit_code == 0, right.output
    assert texts(gateway, dst) == ["one, edited", "two", "three"]


def test_at_a_terminal_the_total_is_typed_back(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    base, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 1, "one, edited")
    gateway.delete_message(src.id, 4)
    prompter = ScriptedPrompter(text=["2"], confirm=[False])  # the count, then no backup
    rt = replace(base, prompter=prompter, interactive=True)

    result = cli.invoke(app, SYNC, obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["one, edited", "two", "three"]
    assert [kind for kind, _ in prompter.asked][:2] == ["text", "confirm"]


def test_a_wrong_number_typed_back_drops_both_plans(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    base, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 1, "one, edited")
    gateway.delete_message(src.id, 4)
    rt = replace(base, prompter=ScriptedPrompter(text=["1"]), interactive=True)

    result = cli.invoke(app, SYNC, obj=rt)

    assert result.exit_code != 0
    assert texts(gateway, dst) == ORIGINAL
    assert [op_of(rt, n).status for n in (1, 2)] == [OpStatus.CANCELLED] * 2  # type: ignore[union-attr]


def test_each_kind_can_be_left_out(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 1, "one, edited")
    gateway.delete_message(src.id, 4)

    only_edits = cli.invoke(app, [*SYNC, "--no-deletes", "--yes", "--expect", "1"], obj=rt)
    assert only_edits.exit_code == 0, only_edits.output
    assert texts(gateway, dst) == ["one, edited", "two", "three", "four"]

    only_deletes = cli.invoke(app, [*SYNC, "--no-edits", "--yes", "--expect", "1"], obj=rt)
    assert only_deletes.exit_code == 0, only_deletes.output
    assert texts(gateway, dst) == ["one, edited", "two", "three"]

    nothing_asked = cli.invoke(app, [*SYNC, "--no-edits", "--no-deletes"], obj=rt)
    assert nothing_asked.exit_code == 2


def test_a_plan_that_deletes_most_of_the_copies_warns_about_it(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    for msg_id in (2, 3, 4):
        gateway.delete_message(src.id, msg_id)

    result = cli.invoke(app, SYNC, obj=rt)

    assert result.exit_code == 2
    assert "75%" in result.output and "can no longer see" in result.output

    quiet = cli.invoke(app, [*SYNC, "--no-deletes"], obj=rt)  # edits only: nothing to warn about
    assert "75%" not in quiet.output


def test_a_second_run_finds_nothing_left_to_do(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 2, "two, edited")
    gateway.delete_message(src.id, 4)
    assert cli.invoke(app, [*SYNC, "--yes", "--expect", "2"], obj=rt).exit_code == 0

    again = cli.invoke(app, SYNC, obj=rt)

    assert again.exit_code == 0, again.output
    assert "Nothing to sync" in again.output
    assert op_of(rt, 3) is None  # no new plan, nothing to confirm


def test_a_copy_whose_source_is_back_by_apply_time_is_not_deleted(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """The plan's word that a source message is gone is only an empty answer: it is read again
    right before the copy goes, and a copy whose source is found stays."""
    rt, src, dst = pair(make_runtime, gateway)
    removed = gateway.messages[src.id][2]
    gateway.delete_message(src.id, removed.id)
    assert cli.invoke(app, SYNC, obj=rt).exit_code == 2  # the plan is saved
    gateway.messages[src.id].insert(2, removed)  # ... and the source turns out to have it

    result = cli.invoke(app, ["delete", "--resume", "O1", "--yes", "--expect", "1"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ORIGINAL
    op = op_of(rt, 1)
    assert op is not None and op.done == 0 and op.skipped == 1


def test_a_copy_somebody_edited_since_the_plan_is_left_alone(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 2, "two, edited")
    assert cli.invoke(app, SYNC, obj=rt).exit_code == 2
    asyncio.run(gateway.edit_message(dst.id, 2, "by hand"))

    result = cli.invoke(app, ["edit", "--resume", "O1", "--yes", "--expect", "1"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst)[1] == "by hand"
    op = op_of(rt, 1)
    assert op is not None and op.done == 0 and op.skipped == 1


def test_a_copy_edited_by_hand_whose_source_never_changed_is_not_looked_at(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    asyncio.run(gateway.edit_message(dst.id, 2, "by hand"))  # nobody edited the source

    result = cli.invoke(app, SYNC, obj=rt)

    assert result.exit_code == 0 and "Nothing to sync" in result.output
    assert texts(gateway, dst)[1] == "by hand"


def test_the_pairs_own_transform_is_applied_to_the_new_source_text(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    path = tmp_path / "t.yaml"
    path.write_text(
        'caption:\n  - remove_hashtags: ["#ads"]\n  - append: "via B"\n  - text_too: true\n',
        encoding="utf-8",
    )
    rt, src, dst = pair(
        make_runtime, gateway, ["news #ads", "plain"], extra=["--transform", str(path)]
    )
    assert texts(gateway, dst) == ["news\n\nvia B", "plain\n\nvia B"]
    edit_source(gateway, src, 1, "news again #ads")

    result = cli.invoke(app, [*SYNC, "--yes", "--expect", "1"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["news again\n\nvia B", "plain\n\nvia B"]


def test_an_album_loses_only_the_copies_whose_sources_went(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src = gateway.add_channel("Source", username="srcchan")
    album = gateway.add_album(src.id, [MediaKind.PHOTO] * 3, "caption")
    gateway.add_message(src.id, "after")
    rt = make_runtime(gateway=gateway)
    assert (
        cli.invoke(
            app, ["clone", "--src", "Source", "--dst-new", "Copy", "--yes"], obj=rt
        ).exit_code
        == 0
    )
    dst = next(c for c in gateway.channels.values() if c.title == "Copy")
    before = ids(gateway, dst)
    assert len(before) == 4
    gateway.delete_message(src.id, album[1].id)
    gateway.delete_message(src.id, album[2].id)

    result = cli.invoke(app, [*SYNC, "--yes", "--expect", "2"], obj=rt)

    assert result.exit_code == 0, result.output
    assert ids(gateway, dst) == [before[0], before[3]]


def test_a_source_that_cannot_be_read_deletes_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Not seeing the source is not the same as it being deleted: nothing is planned."""
    rt, src, dst = pair(make_runtime, gateway)
    del gateway.channels[src.id]

    result = cli.invoke(app, [*SYNC, "--yes", "--expect", "4"], obj=rt)

    assert result.exit_code != 0
    assert texts(gateway, dst) == ORIGINAL
    assert op_of(rt, 1) is None


def test_the_destination_must_be_administered(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    gateway.delete_message(src.id, 4)
    gateway.channels[dst.id] = replace(dst, is_admin=False)

    result = cli.invoke(app, [*SYNC, "--yes", "--expect", "1"], obj=rt)

    assert result.exit_code == 4
    assert texts(gateway, dst) == ORIGINAL


def test_copies_to_be_deleted_can_be_backed_up_first(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    doomed = ids(gateway, dst)[3]
    gateway.delete_message(src.id, 4)
    directory = tmp_path / "net"

    result = cli.invoke(
        app, [*SYNC, "--yes", "--expect", "1", "--backup-to", str(directory)], obj=rt
    )

    assert result.exit_code == 0, result.output
    assert [r.id for r in backupdir.iter_records(directory)] == [doomed]
    assert texts(gateway, dst) == ["one", "two", "three"]


def test_a_formatting_that_cannot_be_written_back_is_never_edited(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    gateway.lossy.add((dst.id, ids(gateway, dst)[1]))
    edit_source(gateway, src, 2, "two, edited")

    result = cli.invoke(app, SYNC, obj=rt)

    assert result.exit_code == 0 and "Nothing to sync" in result.output
    assert texts(gateway, dst) == ORIGINAL


def test_history_says_which_pair_an_operation_was_synced_for(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    gateway.delete_message(src.id, 4)
    assert cli.invoke(app, [*SYNC, "--yes", "--expect", "1"], obj=rt).exit_code == 0

    detail = cli.invoke(app, ["history", "O1"], obj=rt)

    assert detail.exit_code == 0 and "Synced" in detail.output and "R1" in detail.output
    as_json = json.loads(cli.invoke(app, ["history", "O1", "--json"], obj=rt).output)
    assert as_json["planned"] == 1


def test_a_pair_that_adds_a_topic_hashtag_has_no_edits_synced(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Their copies carry text no transform here can work out again: only deletes are looked at."""
    rt, src, dst = pair(make_runtime, gateway)
    edit_source(gateway, src, 2, "two, edited")
    gateway.delete_message(src.id, 4)

    async def plan() -> tuple[bool, bool]:
        async with opened_store(rt) as store:
            run = await store.latest_run()
            assert run is not None
            hashtagged = replace(run, options=replace(run.options, topic_as_hashtag=True))
            found = await plan_sync(store, gateway, hashtagged, rt.config().limits)
            return found.edit is None, found.delete is not None

    assert asyncio.run(plan()) == (True, True)


def test_the_pair_can_be_named_by_its_chats_instead_of_a_run(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    gateway.delete_message(src.id, 4)

    named = cli.invoke(
        app, [*SYNC, "--src", "Source", "--dst", "Copy", "--yes", "--expect", "1"], obj=rt
    )
    assert named.exit_code == 0, named.output
    assert texts(gateway, dst) == ["one", "two", "three"]

    other = gateway.add_channel("Elsewhere")
    unknown = cli.invoke(app, [*SYNC, "--src", "Source", "--dst", other.title], obj=rt)
    both = cli.invoke(app, [*SYNC, "R1", "--src", "Source", "--dst", "Copy"], obj=rt)
    half = cli.invoke(app, [*SYNC, "--src", "Source"], obj=rt)

    assert unknown.exit_code == 2 and "Elsewhere" in unknown.output
    assert both.exit_code == 2 and half.exit_code == 2


def test_only_the_copies_in_an_id_range_of_the_source_are_looked_at(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    rt, src, dst = pair(make_runtime, gateway)
    gateway.delete_message(src.id, 1)
    gateway.delete_message(src.id, 4)
    edit_source(gateway, src, 3, "three, edited")

    result = cli.invoke(app, [*SYNC, "--min-id", "2", "--max-id", "3"], obj=rt)

    assert result.exit_code == 2
    assert "ids 2 to 3" in result.output and "three, edited" in result.output
    assert "O2" not in result.output  # no delete plan: ids 1 and 4 are out of range

    done = cli.invoke(app, [*SYNC, "--min-id", "4", "--yes", "--expect", "1"], obj=rt)
    assert done.exit_code == 0, done.output
    assert texts(gateway, dst) == ["one", "two", "three"]  # 4 went, 1 was not in range
