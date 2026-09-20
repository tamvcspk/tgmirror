"""Strategy B from the command line: ``--mode reupload``, the captions, what cannot be copied, and
decision D3 (a source that restricts saving content is copied only on the user's own say-so).

Same setup as ``test_cli_run``: the real Typer app, ``FakeGateway``, a scripted prompter.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tests.unit.test_cli_run import MakeRuntime, saved_runs, source_with_messages, texts
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import MediaKind
from tgmirror.store.runs import RunStatus
from tgmirror.ui.messages import t

runner = CliRunner()
ACK = "--yes-i-administer-this-channel"
CLONE = ["clone", "--src", "Source", "--dst", "Copy", "--yes"]


@pytest.fixture
def make_runtime(make_runtime: MakeRuntime) -> MakeRuntime:
    """The usual runtime, with a tiny delay between sends: every re-uploaded unit is a batch of
    its own, so the default 2 s would make each of these runs sleep for real."""

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


def protected_source(gateway: FakeGateway, count: int = 3) -> tuple[int, int]:
    return source_with_messages(gateway, count, noforwards=True, is_admin=True)


# ---- a plain reupload -------------------------------------------------------------------------


def test_mode_reupload_copies_by_download_and_upload(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"]
    assert gateway.calls_to("copy_messages") == [] and len(gateway.calls_to("send_prepared")) == 3
    (run,) = saved_runs(rt)
    assert (run.mode, run.status) == ("reupload", RunStatus.DONE)


def test_a_reupload_leaves_no_files_in_the_scratch_folder(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, _ = source_with_messages(gateway, 0)
    gateway.add_album(src, [MediaKind.PHOTO] * 3, caption="album")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt)

    assert result.exit_code == 0, result.output
    assert not [p for p in rt.paths.tmp_dir.rglob("*") if p.is_file()]


def test_captions_are_rewritten_in_auto_mode_and_only_captioned_media_is_sent_by_file_id(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 1)
    gateway.add_message(src, "look", media=MediaKind.PHOTO)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--caption", "append", "--caption-text", "via X"], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "look\n\nvia X"]
    assert [c.args[1] for c in gateway.calls_to("send_by_reference")] == [[2]]
    assert gateway.calls_to("prepare") == []  # nothing was downloaded
    (run,) = saved_runs(rt)
    assert (run.mode, run.options.caption, run.options.caption_text) == ("auto", "append", "via X")


# ---- options that contradict each other ---------------------------------------------------


def test_contradicting_options_are_refused_before_anything_is_created(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    rt = make_runtime(gateway=gateway)

    for extra, needle in [
        (["--caption", "append"], "--caption-text"),
        (["--caption", "shout"], "keep, strip-links"),
        (["--mode", "copy", "--caption", "none"], "not copy"),
        (["--reset-polls"], "--mode reupload"),
        (["--caption-text", "x"], "--caption append"),
    ]:
        result = runner.invoke(
            app, ["clone", "--src", "Source", "--dst-new", "Copy", "--yes", *extra], obj=rt
        )
        assert result.exit_code == 2 and needle in result.output, (extra, result.output)

    assert gateway.calls_to("create_channel") == [] and saved_runs(rt) == []


# ---- what cannot be copied --------------------------------------------------------------------


def test_a_game_stops_the_run_with_a_way_forward_and_keeps_the_progress(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 2)
    gateway.add_message(src, "", media=MediaKind.GAME, title="Chess")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt)

    assert result.exit_code == 2
    assert "Message 3 is a game" in result.output and "--ignore-unsupported" in result.output
    assert texts(gateway, dst) == ["m1", "m2"] and saved_runs(rt)[0].status is RunStatus.FAILED


def test_ignore_unsupported_goes_on_and_the_end_says_how_many_were_left_out(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 1)
    gateway.add_message(src, "", media=MediaKind.GAME, title="Chess")
    gateway.add_message(src, "", media=MediaKind.POLL, title="Q?")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload", "--ignore-unsupported"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Left out message 2 (unsupported:game)" in result.output
    assert "Left out message 3 (unsupported:poll)" in result.output
    assert "2 messages that cannot be copied were left out" in result.output
    assert texts(gateway, dst) == ["m1"]
    assert saved_runs(rt)[0].skipped_unsupported == 2


def test_placeholder_and_reset_polls(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    src, dst = source_with_messages(gateway, 0)
    gateway.add_message(src, "", media=MediaKind.INVOICE, title="Shop")
    gateway.add_message(src, "", media=MediaKind.POLL, title="Q?")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app, [*CLONE, "--mode", "reupload", "--placeholder", "--reset-polls"], obj=rt
    )

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["[Hóa đơn: Shop — không thể sao chép]", ""]  # the poll is real
    assert [m.media for m in gateway.messages[dst]][1] is MediaKind.POLL


# ---- decision D3 ------------------------------------------------------------------------------


def test_a_protected_source_is_not_copied_by_reupload_without_the_users_own_word(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    protected_source(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt)  # --yes is not enough

    assert result.exit_code == 2 and ACK in result.output
    assert gateway.calls_to("prepare") == [] and saved_runs(rt) == []


def test_the_flag_is_the_users_word_and_reupload_then_copies_a_protected_source(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = protected_source(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload", ACK], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "m2", "m3"] and gateway.calls_to("copy_messages") == []
    assert "Restrict saving content" not in result.output  # the warning is for those who can't
    assert "WARNING" in result.output and "full responsibility" in result.output


def test_interactively_the_user_is_asked_and_yes_does_not_answer_for_them(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = protected_source(gateway)
    prompter = ScriptedPrompter(confirm=[True])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt)  # with --yes

    assert result.exit_code == 0, result.output
    (question,) = [m for kind, m in prompter.asked if kind == "confirm"]
    assert "Restrict saving content" in question and "'Source'" in question
    assert texts(gateway, dst) == ["m1", "m2", "m3"]


def test_declining_the_question_copies_and_creates_nothing(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    protected_source(gateway)
    rt = make_runtime(gateway=gateway, prompter=ScriptedPrompter(confirm=[False]), interactive=True)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt)

    assert result.exit_code == 1
    assert gateway.calls_to("prepare") == [] and saved_runs(rt) == []


def test_a_source_this_account_does_not_administer_is_refused_without_the_flag(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, noforwards=True, is_admin=False)
    rt = make_runtime(gateway=gateway)

    plain = runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt)
    with_prompt = runner.invoke(  # the prompt is for admins: it does not stand in for the flag
        app,
        [*CLONE, "--mode", "reupload"],
        obj=make_runtime(
            gateway=gateway,
            prompter=ScriptedPrompter(confirm=[True]),
            interactive=True,
            root=Path(rt.paths.data_dir) / "elsewhere",
        ),
    )

    for refused in (plain, with_prompt):
        assert refused.exit_code == 4 and ACK in refused.output
    assert gateway.calls_to("prepare") == [] and saved_runs(rt) == []


def test_the_flag_makes_the_user_responsible_and_copies_from_an_account_that_is_not_admin(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """Decision D3 as changed: the user owns the channel through another account."""
    _, dst = source_with_messages(gateway, noforwards=True, is_admin=False)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload", ACK], obj=rt)

    assert result.exit_code == 0, result.output
    assert "NOT an admin" in result.output and "full responsibility" in result.output
    assert result.output.count("full responsibility") == 1  # once, not once per step
    assert texts(gateway, dst) == ["m1", "m2", "m3"] and gateway.calls_to("copy_messages") == []
    (run,) = saved_runs(rt)
    assert run.options.protected_ack and run.mode == "reupload"


def test_copy_and_auto_still_get_the_warning_and_the_refusal_on_a_protected_source(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    protected_source(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, [*CLONE, "--mode", "auto"], obj=rt)

    assert result.exit_code == 4 and "--mode reupload" in result.output


# ---- the wizard says the same as the flags -----------------------------------------------------


def wizard(gateway: FakeGateway, make_runtime: MakeRuntime, root: Path, **script: object) -> object:
    prompter = ScriptedPrompter(**script)  # type: ignore[arg-type]
    return make_runtime(gateway=gateway, prompter=prompter, interactive=True, root=root), prompter


def test_the_wizard_step_and_the_flags_end_in_the_same_run(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    flags_gw, wizard_gw = FakeGateway(), FakeGateway()
    for gw in (flags_gw, wizard_gw):
        source_with_messages(gw, 1)
    flags_rt = make_runtime(gateway=flags_gw, root=tmp_path / "flags")
    runner.invoke(
        app,
        [
            *CLONE,
            "--mode",
            "reupload",
            "--caption",
            "append",
            "--caption-text",
            "via X",
            "--reset-polls",
        ],
        obj=flags_rt,
    )

    prompter = ScriptedPrompter(
        select=["Source", "Copy", "Download and send again", "Add some text", "No filter"],
        text=["via X"],
        checkbox=[[t("options.flag_reset_polls")]],
        confirm=[True, True],  # customise how to copy; then the one question
    )
    wizard_rt = make_runtime(
        gateway=wizard_gw, prompter=prompter, interactive=True, root=tmp_path / "wizard"
    )
    result = runner.invoke(app, ["clone"], obj=wizard_rt)

    assert result.exit_code == 0, result.output
    (flags_run,), (wizard_run,) = saved_runs(flags_rt), saved_runs(wizard_rt)
    assert wizard_run.mode == flags_run.mode == "reupload"
    assert wizard_run.options == flags_run.options
    assert wizard_run.options.reset_polls and wizard_run.options.caption == "append"


def test_declining_to_customise_is_a_plain_forward_with_the_captions_kept(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 1)
    prompter = ScriptedPrompter(
        select=["Source", "Copy", "Automatic", "No filter"], confirm=[False, True]
    )
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["clone"], obj=rt)

    assert result.exit_code == 0, result.output
    (run,) = saved_runs(rt)
    assert (run.mode, run.options.caption) == ("auto", "keep")
    assert len(gateway.calls_to("copy_messages")) == 1


def test_a_protected_source_skips_the_how_to_copy_question_and_goes_to_the_rest(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    protected_source(gateway, 1)
    prompter = ScriptedPrompter(
        select=["Source", "Copy", "Keep them", "No filter"],
        checkbox=[[]],
        confirm=[True, True],  # the D3 question, then the one before copying
    )
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["clone"], obj=rt)

    assert result.exit_code == 0, result.output
    asked = [m for kind, m in prompter.asked if kind == "confirm"]
    assert not any("Customise" in m for m in asked) and "Restrict saving content" in asked[0]
    assert saved_runs(rt)[0].mode == "reupload"


# ---- later runs go on the same way -------------------------------------------------------------


def test_run_and_retry_keep_the_strategy_options_of_the_run_they_continue(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 2)
    rt = make_runtime(gateway=gateway)
    first = runner.invoke(
        app,
        [*CLONE, "--mode", "reupload", "--caption", "none", "--ignore-unsupported"],
        obj=rt,
    )
    assert first.exit_code == 0, first.output
    gateway.add_message(src, "new", media=MediaKind.PHOTO)  # caption to be removed

    again = runner.invoke(app, ["run"], obj=rt)

    assert again.exit_code == 0, again.output
    latest = saved_runs(rt)[-1]
    assert (latest.mode, latest.options.caption, latest.options.ignore_unsupported) == (
        "reupload",
        "none",
        True,
    )
    assert texts(gateway, dst) == ["m1", "m2", ""]


# ---- D3 again on later runs --------------------------------------------------------------------


def test_run_carries_the_confirmation_on_so_it_does_not_ask_again(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = protected_source(gateway, 1)
    rt = make_runtime(gateway=gateway)
    assert runner.invoke(app, [*CLONE, "--mode", "reupload", ACK], obj=rt).exit_code == 0
    gateway.add_message(src, "new")

    result = runner.invoke(app, ["run"], obj=rt)  # no flag, no terminal

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1", "new"]
    assert saved_runs(rt)[-1].options.protected_ack


def test_run_refuses_a_source_that_turned_protected_after_the_clone(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 1)
    rt = make_runtime(gateway=gateway)
    assert runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt).exit_code == 0
    gateway.channels[src] = replace(gateway.channels[src], noforwards=True)  # the owner did it
    gateway.add_message(src, "new")

    result = runner.invoke(app, ["run"], obj=rt)

    assert result.exit_code == 2 and ACK in result.output
    assert texts(gateway, dst) == ["m1"]  # nothing of the protected source was downloaded


def test_run_refuses_a_source_that_turned_protected_when_the_account_is_not_admin(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = source_with_messages(gateway, 1)
    rt = make_runtime(gateway=gateway)
    assert runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt).exit_code == 0
    gateway.channels[src] = replace(gateway.channels[src], noforwards=True, is_admin=False)
    gateway.add_message(src, "new")

    result = runner.invoke(app, ["run"], obj=rt)  # the user never made the statement

    assert result.exit_code == 4 and ACK in result.output and texts(gateway, dst) == ["m1"]


def test_the_statement_made_at_clone_time_stands_for_later_runs_of_the_pair(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = protected_source(gateway, 1)
    rt = make_runtime(gateway=gateway)
    assert runner.invoke(app, [*CLONE, "--mode", "reupload", ACK], obj=rt).exit_code == 0
    gateway.channels[src] = replace(gateway.channels[src], is_admin=False)  # the account lost it
    gateway.add_message(src, "new")

    result = runner.invoke(app, ["run"], obj=rt)

    assert result.exit_code == 0, result.output  # the user's responsibility, stated once per pair
    assert texts(gateway, dst) == ["m1", "new"]
    assert "full responsibility" in result.output  # ...and every run says it again


def test_a_caption_change_on_a_protected_source_needs_the_confirmation_too(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, dst = protected_source(gateway, 0)
    gateway.add_message(src, "look", media=MediaKind.PHOTO)
    rt = make_runtime(gateway=gateway)

    refused = runner.invoke(app, [*CLONE, "--caption", "none"], obj=rt)
    allowed = runner.invoke(app, [*CLONE, "--caption", "none", ACK], obj=rt)

    assert refused.exit_code == 2 and ACK in refused.output
    assert allowed.exit_code == 0, allowed.output
    assert texts(gateway, dst) == [""]


# ---- the mode is a step of the wizard ------------------------------------------------------------


def wizard_run(
    make_runtime: MakeRuntime, gateway: FakeGateway, **script: Any
) -> tuple[ScriptedPrompter, Any, Runtime]:
    prompter = ScriptedPrompter(**script)
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)
    return prompter, runner.invoke(app, ["clone"], obj=rt), rt


def test_the_wizard_always_asks_for_the_mode_and_offers_all_three(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 1)

    prompter, result, _ = wizard_run(
        make_runtime,
        gateway,
        select=["Source", "Copy", "Automatic", "No filter"],
        confirm=[False, True],
    )

    assert result.exit_code == 0, result.output
    mode_labels = next(labels for labels in prompter.select_labels if len(labels) == 3)
    assert [label.split(":")[0] for label in mode_labels] == [
        "Automatic",
        "Server-side forward only (copy)",
        "Download and send again (slow; needed to change captions or when saving is restricted)",
    ]


def test_choosing_copy_in_the_wizard_is_the_flag_and_asks_nothing_more(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 1)

    prompter, result, rt = wizard_run(
        make_runtime,
        gateway,
        select=["Source", "Copy", "Server-side forward only", "No filter"],
        confirm=[True],  # only the question before copying: copy has no details to ask about
    )

    assert result.exit_code == 0, result.output
    assert [kind for kind, _ in prompter.asked].count("confirm") == 1
    (run,) = saved_runs(rt)
    assert (run.mode, run.options.caption) == ("copy", "keep")


def test_reupload_without_details_uses_the_defaults_and_asks_about_them_once(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = source_with_messages(gateway, 1)

    prompter, result, rt = wizard_run(
        make_runtime,
        gateway,
        select=["Source", "Copy", "Download and send again", "No filter"],
        confirm=[False, True],  # details: no; then the question before copying
    )

    assert result.exit_code == 0, result.output
    asked = [m for kind, m in prompter.asked if kind == "confirm"]
    assert "what cannot be copied" in asked[0]
    (run,) = saved_runs(rt)
    assert (run.mode, run.options.caption, run.options.reset_polls) == ("reupload", "keep", False)
    assert texts(gateway, dst) == ["m1"] and len(gateway.calls_to("send_prepared")) == 1


def test_the_auto_details_question_is_about_captions_only(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 1)

    prompter, result, _ = wizard_run(
        make_runtime,
        gateway,
        select=["Source", "Copy", "Automatic", "No filter"],
        confirm=[False, True],
    )

    assert result.exit_code == 0, result.output
    assert "captions" in [m for kind, m in prompter.asked if kind == "confirm"][0]
    assert not [1 for kind, _ in prompter.asked if kind == "checkbox"]


def test_the_warning_is_printed_by_every_run_that_relies_on_the_statement_and_only_those(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 1)  # not protected: nothing to answer for
    rt = make_runtime(gateway=gateway)

    plain = runner.invoke(app, [*CLONE, "--mode", "reupload", ACK], obj=rt)

    assert plain.exit_code == 0, plain.output
    assert "responsibility" not in plain.output


def test_answering_the_prompt_leads_to_the_same_warning(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    protected_source(gateway, 1)
    rt = make_runtime(gateway=gateway, prompter=ScriptedPrompter(confirm=[True]), interactive=True)

    result = runner.invoke(app, [*CLONE, "--mode", "reupload"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "full responsibility" in result.output
