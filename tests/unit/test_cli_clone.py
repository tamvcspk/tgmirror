from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tests.unit.test_cli_run import saved_runs
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.errors import FloodWait
from tgmirror.core.gateway import ChatKind

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]


def creations(gateway: FakeGateway) -> list[tuple[object, ...]]:
    return [c.args[:2] for c in gateway.calls_to("create_channel")]


# ---- flags (no terminal, never prompts) -----------------------------------------------------


def test_create_destination_with_flags(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    src = gateway.add_channel("Source", username="source")

    result = runner.invoke(
        app,
        ["clone", "--src", "@source", "--dst-new", "Source (copy)", "--about", "mirror", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    assert creations(gateway) == [("Source (copy)", "mirror")]
    assert f"({src.id})" in result.output and "just created" in result.output
    assert "Source (copy)" in result.output


def test_existing_destination_creates_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Target")

    result = runner.invoke(
        app, ["clone", "--src", str(src.id), "--dst", "Target"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 0, result.output
    assert creations(gateway) == []
    assert f"({dst.id})" in result.output and "just created" not in result.output


def test_creating_a_channel_without_yes_or_terminal_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")

    result = runner.invoke(
        app, ["clone", "--src", "Source", "--dst-new", "Copy"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 2
    assert "--yes" in result.output
    assert creations(gateway) == []


def test_missing_flags_without_terminal_name_the_flag(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    rt = make_runtime(gateway=gateway)

    no_src = runner.invoke(app, ["clone", "--dst-new", "Copy", "--yes"], obj=rt)
    no_dst = runner.invoke(app, ["clone", "--src", "Source"], obj=rt)

    assert (no_src.exit_code, no_dst.exit_code) == (2, 2)
    assert "--src" in no_src.output and "--dst" in no_dst.output
    assert creations(gateway) == []


def test_dst_and_dst_new_are_mutually_exclusive(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    gateway.add_channel("Target")

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst", "Target", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 2 and creations(gateway) == []


def test_unknown_and_ambiguous_references_exit_2(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Twin")
    gateway.add_channel("Twin")
    rt = make_runtime(gateway=gateway)

    unknown = runner.invoke(app, ["clone", "--src", "@nobody", "--dst-new", "C", "--yes"], obj=rt)
    ambiguous = runner.invoke(app, ["clone", "--src", "Twin", "--dst-new", "C", "--yes"], obj=rt)

    assert unknown.exit_code == 2 and "@nobody" in unknown.output
    assert ambiguous.exit_code == 2 and "several" in ambiguous.output
    assert creations(gateway) == []


def test_restricted_source_without_admin_is_refused_and_nothing_is_created(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Locked", noforwards=True, is_admin=False, can_post=False)

    result = runner.invoke(
        app,
        ["clone", "--src", "Locked", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 4
    assert "Restrict saving content" in result.output
    assert creations(gateway) == []


def test_restricted_source_as_admin_without_reupload_is_refused_before_creating_anything(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Telegram refuses to forward out of a protected source, admin or not: a mode that only
    forwards is refused up front, not after an empty destination was created."""
    gateway.add_channel("Mine", noforwards=True, is_admin=True)

    result = runner.invoke(
        app,
        ["clone", "--src", "Mine", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 4, result.output
    assert "--mode reupload" in result.output
    assert creations(gateway) == []


def test_a_non_admin_is_not_asked_for_the_statement_when_the_mode_cannot_use_it(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """``--mode copy`` never downloads, so typing ``--yes-i-administer-this-channel`` could not
    help: the flow refuses (exit 4, with advice) instead of asking for it."""
    gateway.add_channel("Locked", noforwards=True, is_admin=False)
    gateway.add_channel("Copy")
    prompter = ScriptedPrompter()
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(
        app, ["clone", "--src", "Locked", "--dst", "Copy", "--mode", "copy"], obj=rt
    )

    assert result.exit_code == 4, result.output
    assert "--yes-i-administer-this-channel" in result.output  # the advice, not a question
    assert saved_runs(rt) == []


def test_a_busy_pair_is_refused_before_the_strategy_questions(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """The pair's history is checked right after the destination: a pair that cannot run now
    (here: sitting out a FloodWait) is refused before any mode/caption question is asked."""
    src = gateway.add_channel("Source")
    gateway.add_channel("Copy")
    gateway.add_message(src.id, "m1")
    gateway.fail_next("copy_messages", FloodWait(3600))
    prompter = ScriptedPrompter(select=["Source", "Copy"])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    flagged = ["clone", "--src", "Source", "--dst", "Copy", "--yes"]
    assert runner.invoke(app, flagged, obj=rt).exit_code == 3  # the pair now waits out a flood

    result = runner.invoke(app, ["clone"], obj=rt)

    assert result.exit_code == 3, result.output
    assert [kind for kind, _ in prompter.asked] == ["select", "select"]  # source, destination


def test_typing_the_flag_verbatim_lets_a_non_admin_account_through(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Interactive (menu or wizard): no CLI flag to type, so a dedicated question asks for the
    flag's own name, verbatim — not a Yes/No click. Answering it right does the same as
    ``--yes-i-administer-this-channel``."""
    gateway.add_channel("Locked", noforwards=True, is_admin=False, can_post=False)
    prompter = ScriptedPrompter(text=["--yes-i-administer-this-channel"], confirm=[True])

    result = runner.invoke(
        app,
        ["clone", "--src", "Locked", "--dst-new", "Copy", "--mode", "reupload", "--yes"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 0, result.output
    assert len(creations(gateway)) == 1
    assert [k for k, _ in prompter.asked] == ["text", "confirm"]  # ownership, then "go on?"


def test_typing_anything_else_still_refuses_an_unadministered_source(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Locked", noforwards=True, is_admin=False, can_post=False)
    prompter = ScriptedPrompter(text=["yes please"])  # not the flag, verbatim

    result = runner.invoke(
        app,
        ["clone", "--src", "Locked", "--dst-new", "Copy", "--mode", "reupload", "--yes"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 4
    assert "Restrict saving content" in result.output
    assert creations(gateway) == []


def test_an_admin_account_is_never_asked_to_type_the_flag(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Mine", noforwards=True, is_admin=True)
    prompter = ScriptedPrompter(confirm=[True])  # only the "go on?" question, no text prompt

    result = runner.invoke(
        app,
        ["clone", "--src", "Mine", "--dst-new", "Copy", "--mode", "reupload", "--yes"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 0, result.output
    assert [k for k, _ in prompter.asked] == ["confirm"]


def test_destination_rules_exit_codes(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.add_channel("Source")
    gateway.add_channel("Read only", can_post=False)
    gateway.add_channel("Group", kind=ChatKind.SUPERGROUP)
    rt = make_runtime(gateway=gateway)

    read_only = runner.invoke(app, ["clone", "--src", "Source", "--dst", "Read only"], obj=rt)
    # cross-kind is allowed since "same kind only" was dropped: full flags are consent enough
    group = runner.invoke(app, ["clone", "--src", "Source", "--dst", "Group"], obj=rt)
    same = runner.invoke(app, ["clone", "--src", "Source", "--dst", "Source"], obj=rt)

    assert (read_only.exit_code, group.exit_code, same.exit_code) == (4, 0, 2)


def test_a_new_destination_for_a_group_source_is_a_supergroup(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """A basic group cannot be created through the API, so a new destination for a group source
    is a supergroup instead (docs/01-kien-truc.md, "Loại nguồn")."""
    gateway.add_channel("Chat", kind=ChatKind.SUPERGROUP)

    result = runner.invoke(
        app,
        ["clone", "--src", "Chat", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    assert len(creations(gateway)) == 1


# ---- topic loss: forum source, non-forum destination (phase 8) ------------------------------


def test_copy_mode_with_topic_loss_needs_yes_without_a_terminal(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """``--mode copy`` cannot rewrite anything, so topics are only ever dropped; without a
    terminal that needs an explicit ``--yes`` (mirrors ``--fresh``'s usage-error shape)."""
    gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_channel("Broadcast")
    rt = make_runtime(gateway=gateway)

    refused = runner.invoke(
        app, ["clone", "--src", "Forum", "--dst", "Broadcast", "--mode", "copy"], obj=rt
    )
    assert refused.exit_code == 2, refused.output
    assert saved_runs(rt) == []

    agreed = runner.invoke(
        app,
        ["clone", "--src", "Forum", "--dst", "Broadcast", "--mode", "copy", "--yes"],
        obj=rt,
    )
    assert agreed.exit_code == 0, agreed.output
    (run,) = saved_runs(rt)
    assert run.options.topic_as_hashtag is False  # nothing can carry it under --mode copy


def test_copy_mode_with_topic_loss_asks_on_a_terminal(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_channel("Broadcast")
    prompter = ScriptedPrompter(confirm=[False])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    declined = runner.invoke(
        app, ["clone", "--src", "Forum", "--dst", "Broadcast", "--mode", "copy"], obj=rt
    )

    assert declined.exit_code == 1, declined.output
    assert saved_runs(rt) == []


def test_reupload_mode_defaults_topic_as_hashtag_to_yes_with_only_yes_given(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """A mode that can rewrite text gets the hashtag fallback by default; ``--yes`` alone (no
    terminal to ask) takes that default instead of erroring."""
    gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_channel("Broadcast")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["clone", "--src", "Forum", "--dst", "Broadcast", "--mode", "reupload", "--yes"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    (run,) = saved_runs(rt)
    assert run.options.topic_as_hashtag is True


def test_no_topic_as_hashtag_flag_turns_the_default_off(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_channel("Broadcast")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        [
            "clone",
            "--src",
            "Forum",
            "--dst",
            "Broadcast",
            "--mode",
            "reupload",
            "--no-topic-as-hashtag",
            "--yes",
        ],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    (run,) = saved_runs(rt)
    assert run.options.topic_as_hashtag is False


def test_auto_mode_with_caption_keep_defaults_topic_as_hashtag_to_yes(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """The default strategy (auto, caption keep) keeps topics as hashtags too: those messages are
    sent again rather than forwarded, so nothing needs a caption mode to carry them."""
    gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_channel("Broadcast")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, ["clone", "--src", "Forum", "--dst", "Broadcast", "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    (run,) = saved_runs(rt)
    assert (run.mode, run.options.caption, run.options.topic_as_hashtag) == ("auto", "keep", True)


def test_topics_kept_as_hashtags_without_a_question_are_announced(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Nobody was asked (``--yes``): a notice says the topics become hashtags, instead of the
    "topics will be dropped" warning, which would no longer be true."""
    gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_channel("Broadcast")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, ["clone", "--src", "Forum", "--dst", "Broadcast", "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "kept as hashtags" in result.output
    assert "dropped entirely" not in result.output


def test_topic_as_hashtag_into_a_forum_is_dropped_as_needless(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """A forum destination keeps the topics themselves; the flag would only make ``auto`` send
    every topic message again for nothing."""
    gateway.add_channel("Forum A", kind=ChatKind.FORUM)
    gateway.add_channel("Forum B", kind=ChatKind.FORUM)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["clone", "--src", "Forum A", "--dst", "Forum B", "--topic-as-hashtag", "--yes"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    (run,) = saved_runs(rt)
    assert run.options.topic_as_hashtag is False


def test_topic_as_hashtag_under_copy_mode_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Forum", kind=ChatKind.FORUM)
    gateway.add_channel("Broadcast")

    result = runner.invoke(
        app,
        [
            "clone",
            "--src",
            "Forum",
            "--dst",
            "Broadcast",
            "--mode",
            "copy",
            "--topic-as-hashtag",
            "--yes",
        ],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 2, result.output


def test_forum_to_forum_has_no_topic_loss_warning_or_question(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Forum A", kind=ChatKind.FORUM)
    gateway.add_channel("Forum B", kind=ChatKind.FORUM)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, ["clone", "--src", "Forum A", "--dst", "Forum B", "--yes"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "topic" not in result.output.lower()
    (run,) = saved_runs(rt)
    assert run.options.topic_as_hashtag is False


def test_invalid_title_exits_2(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.add_channel("Source")

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "  ", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 2 and "title" in result.output
    assert creations(gateway) == []


def test_flood_wait_while_creating_exits_3(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.add_channel("Source")
    gateway.fail_next("create_channel", FloodWait(600))

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 3 and "600s" in result.output


def test_no_joined_chats(make_runtime: MakeRuntime) -> None:
    result = runner.invoke(
        app, ["clone", "--src", "x", "--dst-new", "y", "--yes"], obj=make_runtime()
    )

    assert result.exit_code == 1 and "No matching channels" in result.output


# ---- wizard (terminal attached) -------------------------------------------------------------


def test_wizard_creates_the_same_channel_as_the_flags(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    """Parity rule: an interactive session and the flags end in the same create_channel call."""
    gateway.add_channel("Source")
    flags = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Copy", "--about", "about", "--yes"],
        obj=make_runtime(gateway=gateway),
    )
    via_flags = creations(gateway)

    wizard_gateway = FakeGateway()
    wizard_gateway.add_channel("Source")
    prompter = ScriptedPrompter(
        select=["Source", "Automatic", "No filter"], text=["Copy", "about"], confirm=[False, True]
    )
    wizard = runner.invoke(
        app,
        ["clone"],
        obj=make_runtime(
            gateway=wizard_gateway,
            prompter=prompter,
            interactive=True,
            root=tmp_path / "wizard",  # its own database: the flags run already saved this pair
        ),
    )

    assert flags.exit_code == wizard.exit_code == 0, wizard.output
    assert creations(wizard_gateway) == via_flags == [("Copy", "about")]
    assert flags.output == wizard.output


def test_wizard_offers_only_writable_destinations(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Any kind is offered (cross-kind is allowed); only "not writable" and "is the source
    itself" still exclude a chat."""
    gateway.add_channel("Source")
    gateway.add_channel("Good target")
    gateway.add_channel("Read only", can_post=False)
    gateway.add_channel("Group", kind=ChatKind.SUPERGROUP)
    prompter = ScriptedPrompter(select=["Source", "Good target", "Automatic", "No filter"])

    result = runner.invoke(
        app, ["clone"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 0, result.output
    source_choices, destination_choices, _mode, _filter = prompter.select_labels
    assert len(source_choices) == 4  # every joined chat can be a source
    assert [c for c in destination_choices if "Read only" in c or "Source" in c] == []
    assert any("Good target" in c for c in destination_choices)
    assert any("Group" in c for c in destination_choices)  # cross-kind is offered too
    assert creations(gateway) == []


def test_wizard_with_no_eligible_destination_goes_straight_to_creating(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Only one")
    prompter = ScriptedPrompter(
        select=["Only one", "Automatic", "No filter"], text=["Copy", ""], confirm=[False, True]
    )

    result = runner.invoke(
        app, ["clone"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 0, result.output
    assert creations(gateway) == [("Copy", "")]
    assert "No suitable existing destination" in "".join(prompter.said)


def test_wizard_declining_the_confirmation_creates_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    prompter = ScriptedPrompter(
        select=["Source", "Automatic", "No filter"], text=["Copy", ""], confirm=[False, False]
    )

    result = runner.invoke(
        app, ["clone"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 1
    assert creations(gateway) == []


def test_wizard_asks_again_after_an_empty_title(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    prompter = ScriptedPrompter(
        select=["Source", "Automatic", "No filter"],
        text=["", "Copy", "About it"],  # only the refused title is asked again
        confirm=[False, True],
    )

    result = runner.invoke(
        app, ["clone"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 0, result.output
    assert creations(gateway) == [("Copy", "About it")]
    assert "The channel title must not be empty." in prompter.said
    texts_asked = [message for kind, message in prompter.asked if kind == "text"]
    assert len(texts_asked) == 3 and texts_asked[0] == texts_asked[1] != texts_asked[2]


def test_flags_given_on_a_terminal_still_ask_to_confirm_creation(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    prompter = ScriptedPrompter(confirm=[False])

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Copy"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 1 and creations(gateway) == []
    ((kind, question),) = prompter.asked
    assert kind == "confirm" and "'Copy' (a new channel will be created)" in question


def test_wizard_ctrl_c_exits_130(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.add_channel("Source")

    class Interrupting(ScriptedPrompter):
        async def select(self, message, choices):  # type: ignore[no-untyped-def]
            raise KeyboardInterrupt

    result = runner.invoke(
        app, ["clone"], obj=make_runtime(gateway=gateway, prompter=Interrupting(), interactive=True)
    )

    assert result.exit_code == 130 and creations(gateway) == []
