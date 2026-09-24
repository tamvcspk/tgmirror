"""``tgmirror config get``/``set``: no Telegram connection, reads/writes ``config.toml`` only."""

import json
from collections.abc import Callable

from typer.testing import CliRunner

from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime

MakeRuntime = Callable[..., Runtime]

runner = CliRunner()


def test_get_shows_paths_and_every_limit(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    result = runner.invoke(app, ["config", "get"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "batch_size = 20" in result.output
    assert str(rt.paths.config_file) in result.output


def test_bare_config_is_the_same_as_get(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    bare = runner.invoke(app, ["config"], obj=rt)
    explicit = runner.invoke(app, ["config", "get"], obj=rt)

    assert bare.exit_code == 0
    assert bare.output == explicit.output


def test_get_one_key(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    result = runner.invoke(app, ["config", "get", "batch_size"], obj=rt)

    assert result.exit_code == 0
    assert result.output.strip() == "20"


def test_get_json(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    result = runner.invoke(app, ["config", "get", "--json"], obj=rt)

    assert result.exit_code == 0
    record = json.loads(result.output)
    assert record["limits"]["batch_size"] == 20
    assert record["limits"]["long_pause_range"] == [30.0, 90.0]
    assert record["paths"]["config_file"] == str(rt.paths.config_file)


def test_get_unknown_key_is_a_usage_error(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    result = runner.invoke(app, ["config", "get", "not_a_key"], obj=rt)

    assert result.exit_code == 2


def test_set_then_get_round_trips(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    result = runner.invoke(app, ["config", "set", "batch_size", "30"], obj=rt)
    assert result.exit_code == 0, result.output
    assert "30" in result.output

    again = runner.invoke(app, ["config", "get", "batch_size"], obj=rt)
    assert again.output.strip() == "30"


def test_set_invalid_value_is_a_usage_error_and_writes_nothing(make_runtime: MakeRuntime) -> None:
    rt = make_runtime()

    result = runner.invoke(app, ["config", "set", "batch_size", "not-a-number"], obj=rt)

    assert result.exit_code == 2
    assert not rt.paths.config_file.exists()
