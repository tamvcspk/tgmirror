from collections.abc import Callable

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.errors import FloodWait
from tgmirror.core.gateway import ChatKind

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]


def creations(gateway: FakeGateway) -> list[tuple[object, ...]]:
    return [c.args for c in gateway.calls_to("create_channel")]


# ---- flags (no terminal, never prompts) -----------------------------------------------------


def test_create_destination_with_flags(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    src = gateway.add_channel("Source", username="source")

    result = runner.invoke(
        app,
        ["new", "--src", "@source", "--dst-new", "Source (copy)", "--about", "mirror", "--yes"],
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
        app, ["new", "--src", str(src.id), "--dst", "Target"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 0, result.output
    assert creations(gateway) == []
    assert f"({dst.id})" in result.output and "just created" not in result.output


def test_creating_a_channel_without_yes_or_terminal_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")

    result = runner.invoke(
        app, ["new", "--src", "Source", "--dst-new", "Copy"], obj=make_runtime(gateway=gateway)
    )

    assert result.exit_code == 2
    assert "--yes" in result.output
    assert creations(gateway) == []


def test_missing_flags_without_terminal_name_the_flag(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    rt = make_runtime(gateway=gateway)

    no_src = runner.invoke(app, ["new", "--dst-new", "Copy", "--yes"], obj=rt)
    no_dst = runner.invoke(app, ["new", "--src", "Source"], obj=rt)

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
        ["new", "--src", "Source", "--dst", "Target", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 2 and creations(gateway) == []


def test_unknown_and_ambiguous_references_exit_2(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Twin")
    gateway.add_channel("Twin")
    rt = make_runtime(gateway=gateway)

    unknown = runner.invoke(app, ["new", "--src", "@nobody", "--dst-new", "C", "--yes"], obj=rt)
    ambiguous = runner.invoke(app, ["new", "--src", "Twin", "--dst-new", "C", "--yes"], obj=rt)

    assert unknown.exit_code == 2 and "@nobody" in unknown.output
    assert ambiguous.exit_code == 2 and "several" in ambiguous.output
    assert creations(gateway) == []


def test_restricted_source_without_admin_is_refused_and_nothing_is_created(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Locked", noforwards=True, is_admin=False, can_post=False)

    result = runner.invoke(
        app,
        ["new", "--src", "Locked", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 4
    assert "Restrict saving content" in result.output
    assert creations(gateway) == []


def test_restricted_source_as_admin_warns_but_goes_on(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Mine", noforwards=True, is_admin=True)

    result = runner.invoke(
        app,
        ["new", "--src", "Mine", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 0, result.output
    assert "Restrict saving content" in result.output  # the warning
    assert len(creations(gateway)) == 1


def test_destination_rules_exit_codes(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.add_channel("Source")
    gateway.add_channel("Read only", can_post=False)
    gateway.add_channel("Group", kind=ChatKind.SUPERGROUP)
    rt = make_runtime(gateway=gateway)

    read_only = runner.invoke(app, ["new", "--src", "Source", "--dst", "Read only"], obj=rt)
    group = runner.invoke(app, ["new", "--src", "Source", "--dst", "Group"], obj=rt)
    same = runner.invoke(app, ["new", "--src", "Source", "--dst", "Source"], obj=rt)

    assert (read_only.exit_code, group.exit_code, same.exit_code) == (4, 2, 2)


def test_new_destination_for_a_group_source_is_not_available_yet(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Chat", kind=ChatKind.SUPERGROUP)

    result = runner.invoke(
        app,
        ["new", "--src", "Chat", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 2 and "phase 8" in result.output
    assert creations(gateway) == []


def test_invalid_title_exits_2(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.add_channel("Source")

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst-new", "  ", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 2 and "title" in result.output
    assert creations(gateway) == []


def test_flood_wait_while_creating_exits_3(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.add_channel("Source")
    gateway.fail_next("create_channel", FloodWait(600))

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst-new", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 3 and "600s" in result.output


def test_no_joined_chats(make_runtime: MakeRuntime) -> None:
    result = runner.invoke(
        app, ["new", "--src", "x", "--dst-new", "y", "--yes"], obj=make_runtime()
    )

    assert result.exit_code == 1 and "No matching channels" in result.output


# ---- wizard (terminal attached) -------------------------------------------------------------


def test_wizard_creates_the_same_channel_as_the_flags(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Parity rule: an interactive session and the flags end in the same create_channel call."""
    gateway.add_channel("Source")
    flags = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst-new", "Copy", "--about", "about", "--yes"],
        obj=make_runtime(gateway=gateway),
    )
    via_flags = creations(gateway)

    wizard_gateway = FakeGateway()
    wizard_gateway.add_channel("Source")
    prompter = ScriptedPrompter(
        select=["Source", "Create a new channel"], text=["Copy", "about"], confirm=[True]
    )
    wizard = runner.invoke(
        app, ["new"], obj=make_runtime(gateway=wizard_gateway, prompter=prompter, interactive=True)
    )

    assert flags.exit_code == wizard.exit_code == 0, wizard.output
    assert creations(wizard_gateway) == via_flags == [("Copy", "about")]
    assert flags.output == wizard.output


def test_wizard_offers_only_eligible_destinations(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    gateway.add_channel("Good target")
    gateway.add_channel("Read only", can_post=False)
    gateway.add_channel("Group", kind=ChatKind.SUPERGROUP)
    prompter = ScriptedPrompter(select=["Source", "Good target"])

    result = runner.invoke(
        app, ["new"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 0, result.output
    source_choices, destination_choices = prompter.select_labels
    assert len(source_choices) == 4  # every joined chat can be a source
    assert [
        c for c in destination_choices if "Read only" in c or "Group" in c or "Source" in c
    ] == []
    assert any("Good target" in c for c in destination_choices)
    assert creations(gateway) == []


def test_wizard_with_no_eligible_destination_goes_straight_to_creating(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Only one")
    prompter = ScriptedPrompter(select=["Only one"], text=["Copy", ""], confirm=[True])

    result = runner.invoke(
        app, ["new"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 0, result.output
    assert creations(gateway) == [("Copy", "")]
    assert "No suitable existing destination" in "".join(prompter.said)


def test_wizard_declining_the_confirmation_creates_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    prompter = ScriptedPrompter(select=["Source"], text=["Copy", ""], confirm=[False])

    result = runner.invoke(
        app, ["new"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 1
    assert creations(gateway) == []


def test_wizard_asks_again_after_an_empty_title(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    prompter = ScriptedPrompter(
        select=["Source"],
        text=["", "", "Copy", ""],
        confirm=[True],  # title+about twice
    )

    result = runner.invoke(
        app, ["new"], obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    )

    assert result.exit_code == 0, result.output
    assert creations(gateway) == [("Copy", "")]
    assert "The channel title must not be empty." in prompter.said


def test_flags_given_on_a_terminal_still_ask_to_confirm_creation(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    prompter = ScriptedPrompter(confirm=[False])

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst-new", "Copy"],
        obj=make_runtime(gateway=gateway, prompter=prompter, interactive=True),
    )

    assert result.exit_code == 1 and creations(gateway) == []
    assert prompter.asked == [("confirm", "Create channel «Copy»?")]


def test_wizard_ctrl_c_exits_130(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    gateway.add_channel("Source")

    class Interrupting(ScriptedPrompter):
        async def select(self, message, choices):  # type: ignore[no-untyped-def]
            raise KeyboardInterrupt

    result = runner.invoke(
        app, ["new"], obj=make_runtime(gateway=gateway, prompter=Interrupting(), interactive=True)
    )

    assert result.exit_code == 130 and creations(gateway) == []
