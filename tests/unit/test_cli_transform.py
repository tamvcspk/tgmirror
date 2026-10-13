"""``--transform`` from the command line (phase 19): clone, run, selection files and the
pre-flight of ``inspect``. Same setup as ``test_cli_reupload``: the real Typer app, ``FakeGateway``.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeGateway
from tests.unit.test_cli_run import MakeRuntime, saved_runs, source_with_messages, texts
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import ChatKind, MediaKind
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


def test_a_caption_flag_goes_on_top_of_the_transform_file_after_its_operations(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    _, dst = captioned(gateway)
    rt = make_runtime(gateway=gateway)
    path = transform_file(
        tmp_path, 'caption:\n  - remove_hashtags: ["#ads"]\n  - append: "via B"\n'
    )

    result = runner.invoke(
        app,
        [*CLONE, "--transform", str(path), "--caption", "append", "--caption-text", "tail"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    # the file's operations first, then the flag's
    assert texts(gateway, dst) == ["news #ads", "clip #keep\n\nvia B\n\ntail"]


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


# ---- topics (phase 19b) -----------------------------------------------------------------------

ROUTING = "topics:\n  hashtag_to_topic:\n    map:\n      '#python': Python\n      '#rust': Rust\n"


def tagged(gateway: FakeGateway) -> tuple[int, int]:
    src, dst = source_with_messages(gateway, 0)
    gateway.add_message(src, "py #python", hashtags=("#python",))  # 1
    gateway.add_message(src, "rs #rust", hashtags=("#rust",))  # 2
    gateway.add_message(src, "other")  # 3
    return src, dst


def test_a_new_destination_for_hashtag_routing_is_created_as_a_forum_whatever_the_source_is(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    tagged(gateway)  # a plain broadcast source
    rt = make_runtime(gateway=gateway)
    path = transform_file(tmp_path, ROUTING)

    result = runner.invoke(
        app, ["clone", "--src", "Source", "--dst-new", "Sorted", "--yes", "--transform", str(path)],
        obj=rt,
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    ((_, _, kind),) = [c.args for c in gateway.calls_to("create_channel")]  # type: ignore[misc]
    assert kind is ChatKind.FORUM
    sorted_id = next(c.id for c in gateway.channels.values() if c.title == "Sorted")
    topics = {t.title: t.id for t in gateway.topics[sorted_id]}
    assert set(topics) == {"Python", "Rust"}
    landed = {m.text: m.topic_id for m in gateway.messages[sorted_id]}
    assert landed == {"py #python": topics["Python"], "rs #rust": topics["Rust"], "other": None}


def test_the_flag_alone_makes_a_topic_per_hashtag_in_a_new_forum(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """``--hashtag-to-topic``: no file to write, every hashtag its own topic."""
    tagged(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Sorted", "--yes", "--hashtag-to-topic"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    sorted_id = next(c.id for c in gateway.channels.values() if c.title == "Sorted")
    topics = {t.title: t.id for t in gateway.topics[sorted_id]}
    assert set(topics) == {"Python", "Rust"}
    landed = {m.text: m.topic_id for m in gateway.messages[sorted_id]}
    assert landed == {"py #python": topics["Python"], "rs #rust": topics["Rust"], "other": None}
    (run,) = saved_runs(rt)
    assert Transform.from_json(run.options.transform_json).topics.auto_all  # run remembers it


def test_the_flag_fills_in_around_the_names_a_file_gives(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    tagged(gateway)
    rt = make_runtime(gateway=gateway)
    path = transform_file(tmp_path, "topics: {hashtag_to_topic: {map: {'#python': Snake}}}\n")

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Sorted", "--yes", "--hashtag-to-topic"]
        + ["--transform", str(path)],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    sorted_id = next(c.id for c in gateway.channels.values() if c.title == "Sorted")
    assert {t.title for t in gateway.topics[sorted_id]} == {"Snake", "Rust"}


def test_an_existing_destination_without_topics_is_refused_before_anything_is_written(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    _, dst = tagged(gateway)  # "Copy" is a broadcast channel
    rt = make_runtime(gateway=gateway)
    path = transform_file(tmp_path, ROUTING)

    result = runner.invoke(app, [*CLONE, "--transform", str(path)], obj=rt)

    assert result.exit_code == 2, result.output
    assert "forum" in result.output
    assert gateway.messages.get(dst, []) == [] and saved_runs(rt) == []
    assert gateway.calls_to("create_topic") == []


def test_run_goes_on_routing_with_the_remembered_rules(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src, _ = tagged(gateway)
    rt = make_runtime(gateway=gateway)
    path = transform_file(tmp_path, ROUTING)
    runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Sorted", "--yes", "--transform", str(path)],
        obj=rt,
    )
    gateway.add_message(src, "py again #python", hashtags=("#python",))  # 4

    again = runner.invoke(app, ["run"], obj=rt)

    assert again.exit_code == 0, again.output
    sorted_id = next(c.id for c in gateway.channels.values() if c.title == "Sorted")
    python = next(t.id for t in gateway.topics[sorted_id] if t.title == "Python")
    assert gateway.messages[sorted_id][-1].topic_id == python
    assert len(gateway.calls_to("create_topic")) == 2  # Python and Rust, once


def test_the_preflight_says_how_many_units_go_to_each_topic(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src, _ = tagged(gateway)
    gateway.add_message(src, "py two #python", hashtags=("#python",))  # 4
    rt = make_runtime(gateway=gateway)
    path = transform_file(tmp_path, ROUTING)

    result = runner.invoke(
        app,
        ["inspect", "Source", "--plan", "clone", "--json", "--transform", str(path)],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    topics = json.loads(result.output)["plan"]["topics"]
    assert topics == [
        {"topic": "Python", "units": 2},
        {"topic": "Rust", "units": 1},
        {"topic": None, "units": 1},  # "other": no rule, so General
    ]


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
