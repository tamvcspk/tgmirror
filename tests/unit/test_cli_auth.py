from collections.abc import Callable

import keyring
import keyring.backends.fail
from typer.testing import CliRunner

from tests.fakes import ACCOUNT, FakeAuth, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.config import load_config
from tgmirror.core.errors import FloodWait
from tgmirror.core.secrets import read_keyring

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]


def test_login_interactive_normalises_phone_and_hides_secrets(make_runtime: MakeRuntime) -> None:
    auth = FakeAuth(code="24680")
    prompter = ScriptedPrompter(text=["+84 (901) 234-567"], secret=["24680"])
    rt = make_runtime(auth=auth, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Logged in: Test User (@tester)" in result.output
    assert auth.seen_secrets[0] == "+84901234567"  # what Telegram received
    assert "24680" not in result.output and "84901234567" not in result.output
    assert [k for k, _ in prompter.asked] == ["text", "secret"]  # the code is asked hidden


def test_login_with_two_step_password(make_runtime: MakeRuntime) -> None:
    auth = FakeAuth(password="hunter2")
    prompter = ScriptedPrompter(text=["+15551234567"], secret=["12345", "hunter2"])
    rt = make_runtime(auth=auth, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "hunter2" not in result.output
    assert auth.current is not None


def test_login_wrong_code_three_times_exits_1(make_runtime: MakeRuntime) -> None:
    prompter = ScriptedPrompter(text=["+15551234567"], secret=["1", "2", "3"])
    rt = make_runtime(auth=FakeAuth(), prompter=prompter, interactive=True)

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_login_prefills_phone_from_flag(make_runtime: MakeRuntime) -> None:
    auth = FakeAuth()
    prompter = ScriptedPrompter(secret=["12345"])  # the text prompt falls back to its default
    rt = make_runtime(auth=auth, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["login", "--phone", "+15551234567"], obj=rt)

    assert result.exit_code == 0, result.output
    assert auth.seen_secrets[0] == "+15551234567"


def test_login_without_terminal_exits_2_before_asking_telegram_for_a_code(
    make_runtime: MakeRuntime,
) -> None:
    auth = FakeAuth()
    rt = make_runtime(auth=auth, interactive=False)

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 2
    assert "interactive terminal" in result.output
    assert "request_code" not in auth.calls


def test_login_when_already_logged_in_needs_no_terminal(make_runtime: MakeRuntime) -> None:
    rt = make_runtime(interactive=False)  # logged in by default

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 0
    assert "Already logged in" in result.output


def test_login_asks_for_and_saves_missing_credentials(make_runtime: MakeRuntime) -> None:
    prompter = ScriptedPrompter(
        text=["abc", "777", "+15551234567"],  # first api_id is rejected, then accepted
        secret=["deadbeefdeadbeefdeadbeefdeadbeef", "12345"],
    )
    rt = make_runtime(auth=FakeAuth(), prompter=prompter, interactive=True, env={})

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 0, result.output
    config = load_config(rt.paths, env={})
    assert config.api_id == 777
    assert config.api_hash is not None
    assert config.api_hash.get_secret_value() == "deadbeefdeadbeefdeadbeefdeadbeef"
    assert "deadbeef" not in result.output  # hard rule 6


def test_login_saves_into_the_keyring_and_leaves_config_toml_clean(
    make_runtime: MakeRuntime,
) -> None:
    """``fake_keyring`` (conftest) is a usable backend, so this is the everyday path on a machine
    that has a real one (docs/06-lo-trinh.md, Phase 9)."""
    prompter = ScriptedPrompter(
        text=["777", "+15551234567"], secret=["deadbeefdeadbeefdeadbeefdeadbeef", "12345"]
    )
    rt = make_runtime(auth=FakeAuth(), prompter=prompter, interactive=True, env={})

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "keyring" in result.output.lower()
    assert not rt.paths.config_file.exists() or "api_hash" not in rt.paths.config_file.read_text()
    assert read_keyring() == (777, "deadbeefdeadbeefdeadbeefdeadbeef")
    config = load_config(rt.paths, env={})  # resolves through the keyring
    assert config.api_id == 777


def test_login_moves_existing_config_toml_credentials_to_the_keyring(
    make_runtime: MakeRuntime,
) -> None:
    """Running ``login`` again (e.g. because a session expired) is the trigger docs/06-lo-trinh.md
    describes for moving a plaintext credential over once a keyring becomes available — not
    something that happens silently in the background."""
    prompter = ScriptedPrompter(text=["+15551234567"], secret=["12345"])
    rt = make_runtime(auth=FakeAuth(), prompter=prompter, interactive=True, env={})
    rt.paths.config_dir.mkdir(parents=True)
    rt.paths.config_file.write_text(
        '# a note\napi_id = 555\napi_hash = "old-hash-value"\n\n[limits]\nbatch_size = 5\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 0, result.output
    text = rt.paths.config_file.read_text(encoding="utf-8")
    assert "api_id" not in text and "api_hash" not in text
    assert "# a note" in text and "batch_size = 5" in text  # everything else survives
    assert read_keyring() == (555, "old-hash-value")


def test_login_falls_back_to_config_toml_without_a_usable_keyring(
    make_runtime: MakeRuntime,
) -> None:
    keyring.set_keyring(keyring.backends.fail.Keyring())
    prompter = ScriptedPrompter(
        text=["777", "+15551234567"], secret=["deadbeefdeadbeefdeadbeefdeadbeef", "12345"]
    )
    rt = make_runtime(auth=FakeAuth(), prompter=prompter, interactive=True, env={})

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 0, result.output
    assert str(rt.paths.config_file) in result.output
    config = load_config(rt.paths, env={})
    assert config.api_id == 777
    assert config.api_hash is not None
    assert config.api_hash.get_secret_value() == "deadbeefdeadbeefdeadbeefdeadbeef"


def test_login_without_credentials_and_terminal_explains_the_env_vars(
    make_runtime: MakeRuntime,
) -> None:
    rt = make_runtime(interactive=False, env={})

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 2
    assert "TGMIRROR_API_ID" in result.output and "TGMIRROR_API_HASH" in result.output


def test_login_ctrl_c_exits_130(make_runtime: MakeRuntime) -> None:
    class Interrupting(ScriptedPrompter):
        async def text(self, message: str, default: str = "") -> str:
            raise KeyboardInterrupt

    rt = make_runtime(auth=FakeAuth(), prompter=Interrupting(), interactive=True)

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 130


def test_whoami_shows_name_username_and_id_but_not_the_phone(make_runtime: MakeRuntime) -> None:
    result = runner.invoke(app, ["whoami"], obj=make_runtime())

    assert result.exit_code == 0
    assert "Test User (@tester) (id 42)" in result.output


def test_whoami_when_logged_out_points_to_login(make_runtime: MakeRuntime) -> None:
    rt = make_runtime(auth=FakeAuth(logged_in=None))

    result = runner.invoke(app, ["whoami"], obj=rt)

    assert result.exit_code == 1
    assert "tgmirror login" in result.output


def test_logout_ends_the_session(make_runtime: MakeRuntime) -> None:
    auth = FakeAuth(logged_in=ACCOUNT)
    rt = make_runtime(auth=auth)

    result = runner.invoke(app, ["logout"], obj=rt)

    assert result.exit_code == 0
    assert "Logged out Test User" in result.output
    assert auth.current is None


def test_logout_when_not_logged_in_is_not_an_error(make_runtime: MakeRuntime) -> None:
    auth = FakeAuth(logged_in=None)

    result = runner.invoke(app, ["logout"], obj=make_runtime(auth=auth))

    assert result.exit_code == 0
    assert "log_out" not in auth.calls


def test_flood_wait_during_login_exits_3(make_runtime: MakeRuntime) -> None:
    auth = FakeAuth()
    auth.fail_next("request_code", FloodWait(120))
    prompter = ScriptedPrompter(text=["+15551234567"])
    rt = make_runtime(auth=auth, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["login"], obj=rt)

    assert result.exit_code == 3
    assert "120s" in result.output
