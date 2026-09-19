from typer.testing import CliRunner

from tgmirror import __version__
from tgmirror.cli.app import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"tgmirror {__version__}"


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])

    assert "Clone a Telegram channel" in result.output


def test_utf8_output_is_forced_on_a_legacy_code_page(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import io
    import sys

    from tgmirror.cli.app import _use_utf8_output

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))

    _use_utf8_output()
    print("Chưa đăng nhập")
    sys.stdout.flush()

    assert raw.getvalue().decode("utf-8").strip() == "Chưa đăng nhập"
