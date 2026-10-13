"""``--transform`` from the command line (phase 19): clone, run, selection files and the
pre-flight of ``inspect``. Same setup as ``test_cli_reupload``: the real Typer app, ``FakeGateway``.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeGateway
from tests.unit.test_cli_run import MakeRuntime, saved_runs, source_with_messages, texts
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import MediaKind
from tgmirror.engine.transform import Transform

runner = CliRunner()
ACK = "--yes-i-administer-this-channel"
CLONE = ["clone", "--src", "Source", "--dst", "Copy", "--yes"]
CLEAR = "version: 1\nfilter: {}\ntransform:\n  caption:\n    - clear: true\n"


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


def transform_file(tmp_path: Path, body: str = "") -> Path:
    path = tmp_path / "t.yaml"
    path.write_text(
        body
        or 'caption:\n  - remove_hashtags: ["#ads"]\n  - append: "via B"\n  - text_too: true\n',
        encoding="utf-8",
    )
    return path


def captioned(gateway: FakeGateway) -> tuple[int, int]:
    src, dst = source_with_messages(gateway, 0)
    gateway.add_message(src, "news #ads")  # 1: plain text
    gateway.add_message(src, "clip #ads #keep", media=MediaKind.VIDEO)  # 2
    return src, dst


def test_a_transform_file_rewrites_what_it_selects_and_is_remembered_with_the_pair(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    _, dst = captioned(gateway)
    rt = make_runtime(gateway=gateway)
    path = transform_file(tmp_path)

    result = runner.invoke(app, [*CLONE, "--transform", str(path)], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["news\n\nvia B", "clip #keep\n\nvia B"]
    (run,) = saved_runs(rt)
    assert run.mode == "auto"  # a transform alone does not ask for a mode
    assert Transform.from_json(run.options.transform_json).text_too


def test_run_goes_on_with_the_same_transform(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src, dst = captioned(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, [*CLONE, "--transform", str(transform_file(tmp_path))], obj=rt)
    gateway.add_message(src, "later #ads #ok", media=MediaKind.PHOTO)  # 3

    again = runner.invoke(app, ["run"], obj=rt)

    assert again.exit_code == 0, again.output
    assert texts(gateway, dst)[-1] == "later #ok\n\nvia B"


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        (["--caption", "none"], "--transform"),
        (["--mode", "copy"], "not copy"),
    ],
)
def test_a_transform_that_contradicts_other_options_is_refused_before_anything_is_created(
    make_runtime: MakeRuntime,
    gateway: FakeGateway,
    tmp_path: Path,
    extra: list[str],
    needle: str,
) -> None:
    captioned(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "New", "--yes"]
        + ["--transform", str(transform_file(tmp_path)), *extra],
        obj=rt,
    )

    assert result.exit_code == 2 and needle in result.output, result.output
    assert gateway.calls_to("create_channel") == [] and saved_runs(rt) == []


def test_a_bad_transform_file_is_a_usage_error_naming_the_part(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    captioned(gateway)
    rt = make_runtime(gateway=gateway)
    bad = transform_file(tmp_path, "caption:\n  - strip_links: sometimes\n")

    result = runner.invoke(app, [*CLONE, "--transform", str(bad)], obj=rt)

    assert result.exit_code == 2
    assert "caption[0].strip_links" in result.output and saved_runs(rt) == []


def test_a_transform_on_a_protected_source_needs_the_statement(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src, dst = source_with_messages(gateway, 0, noforwards=True, is_admin=True)
    gateway.add_message(src, "look #ads", media=MediaKind.PHOTO)
    rt = make_runtime(gateway=gateway)
    path = transform_file(tmp_path, "caption:\n  - remove_hashtags: all\n")

    refused = runner.invoke(app, [*CLONE, "--transform", str(path)], obj=rt)
    allowed = runner.invoke(app, [*CLONE, "--transform", str(path), ACK], obj=rt)

    assert refused.exit_code == 2 and ACK in refused.output
    assert allowed.exit_code == 0, allowed.output
    assert texts(gateway, dst) == ["look"]


# ---- selection files --------------------------------------------------------------------------


def test_a_selection_file_can_carry_the_transform(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    _, dst = captioned(gateway)
    rt = make_runtime(gateway=gateway)
    path = tmp_path / "s.yaml"
    path.write_text(
        "version: 1\nfilter: {}\ntransform:\n  caption:\n"
        '    - replace: {regex: "#ads", with: "AD"}\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, [*CLONE, "--selection", str(path)], obj=rt)

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["news #ads", "clip AD #keep"]  # text_too was not asked for


def test_the_transform_flag_and_a_selections_transform_are_not_merged(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    captioned(gateway)
    rt = make_runtime(gateway=gateway)
    path = tmp_path / "s.yaml"
    path.write_text(CLEAR, encoding="utf-8")

    result = runner.invoke(
        app,
        [*CLONE, "--selection", str(path), "--transform", str(transform_file(tmp_path))],
        obj=rt,
    )

    assert result.exit_code == 2 and "--transform" in result.output
    assert saved_runs(rt) == []


def test_backup_refuses_a_selection_that_carries_a_transform(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    captioned(gateway)
    rt = make_runtime(gateway=gateway)
    path = tmp_path / "s.yaml"
    path.write_text(CLEAR, encoding="utf-8")

    result = runner.invoke(
        app,
        ["backup", "Source", str(tmp_path / "out"), "--selection", str(path), "--yes"],
        obj=rt,
    )

    assert result.exit_code == 2 and "transform" in result.output


# ---- inspect ----------------------------------------------------------------------------------


def test_the_preflight_counts_a_transform_like_the_real_run_routes_it(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    captioned(gateway)
    rt = make_runtime(gateway=gateway)
    path = transform_file(tmp_path)

    plain = runner.invoke(app, ["inspect", "Source", "--plan", "clone", "--json"], obj=rt)
    planned = runner.invoke(
        app,
        ["inspect", "Source", "--plan", "clone", "--json", "--transform", str(path)],
        obj=rt,
    )

    assert plain.exit_code == 0 and planned.exit_code == 0, planned.output
    assert '"strategy": "copy"' in plain.output and '"strategy": "reference"' not in plain.output
    assert '"strategy": "reference"' in planned.output  # the clip is sent again by its file id
    assert '"strategy": "reupload"' in planned.output  # the text goes the long way (text_too)


def test_transform_without_plan_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    captioned(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app, ["inspect", "Source", "--transform", str(transform_file(tmp_path))], obj=rt
    )

    assert result.exit_code == 2
