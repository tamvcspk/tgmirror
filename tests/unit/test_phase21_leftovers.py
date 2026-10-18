"""What Phase 21 left for later (docs/06-lo-trinh.md, "Phase 21 — ghi chú"): the flags that tune
hashtag routing, ``inspect --dst-no-topics``, the "destination topic" table (``--by route``, the
Explore screen's column) and the wizard's basic routing question."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tests.unit.test_cli_run import MakeRuntime, saved_runs, source_with_messages
from tests.unit.test_cli_transform import transform_file
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.cli.transform_options import TopicFlags, with_topic_flags
from tgmirror.core.gateway import SrcMessage, Unit
from tgmirror.engine.explore import Explorer, NotFilterable
from tgmirror.engine.transform import Transform, TransformError
from tgmirror.ui.messages import t

runner = CliRunner()


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


def unit(*tags: str, i: int = 1) -> Unit:
    msg = SrcMessage(
        id=i, date=datetime(2024, 1, 1, tzinfo=UTC), text=" ".join(tags), hashtags=tags
    )
    return Unit((msg,))


def routed(flags: TopicFlags, *tags: str, base: Transform | None = None) -> str | None:
    transform = with_topic_flags(base or Transform(), flags)
    return transform.topics.destination(unit(*tags))


# ---- --topic-skip / --topic-regex / --topic-pick / --topic-limit -------------------------------


def test_skip_leaves_a_hashtag_out_of_the_names() -> None:
    flags = TopicFlags(hashtag_to_topic=True, topic_skip=("#YYLive", "#789live"))

    assert routed(flags, "#yylive", "#python") == "Python"
    assert routed(flags, "#789live") is None  # nothing left to name it


def test_pick_last_takes_the_last_hashtag() -> None:
    flags = TopicFlags(hashtag_to_topic=True, topic_pick="last")

    assert routed(flags, "#a", "#b", "#c") == "C"
    assert routed(TopicFlags(hashtag_to_topic=True), "#a", "#b") == "A"  # first by default


def test_regex_keeps_only_the_hashtags_it_finds() -> None:
    flags = TopicFlags(hashtag_to_topic=True, topic_regex="^#(?!yy)", topic_pick="last")

    assert routed(flags, "#python", "#yylive") == "Python"


def test_the_tuning_flags_go_on_top_of_the_hashtag_rule_a_file_has(tmp_path: Path) -> None:
    from tgmirror.engine import transform as tf

    path = transform_file(
        tmp_path,
        "topics:\n  route:\n    - {from: hashtags, to: '{value|tag_title}', skip: ['#ads']}\n",
    )
    base = tf.load(path)

    flags = TopicFlags(topic_skip=("#promo",), topic_pick="last")  # no --hashtag-to-topic needed

    assert routed(flags, "#ads", "#promo", "#news", base=base) == "News"
    assert routed(flags, "#ads", base=base) is None  # the file's skip is kept, not replaced


def test_tuning_needs_hashtag_routing() -> None:
    with pytest.raises(TransformError, match="hashtag routing"):
        with_topic_flags(Transform(), TopicFlags(topic_skip=("#a",)))


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (TopicFlags(hashtag_to_topic=True, topic_regex="("), "--topic-regex"),
        (TopicFlags(hashtag_to_topic=True, topic_pick="middle"), "--topic-pick"),
        (TopicFlags(hashtag_to_topic=True, topic_skip=("not a tag",)), "--topic-skip"),
    ],
)
def test_a_bad_tuning_value_names_its_flag(flags: TopicFlags, message: str) -> None:
    with pytest.raises(TransformError, match=message):
        with_topic_flags(Transform(), flags)


def test_limit_replaces_the_limit_and_needs_a_name_made_from_the_message() -> None:
    limited = with_topic_flags(Transform(), TopicFlags(hashtag_to_topic=True, topic_limit=5))
    assert limited.topics.limit == 5
    assert Transform.from_json(limited.to_json()).topics.limit == 5  # remembered with the pair

    with pytest.raises(TransformError, match="--topic-limit"):
        with_topic_flags(Transform(), TopicFlags(topic_limit=5))


def test_a_clone_with_the_tuning_flags_routes_and_remembers_them(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    src, _ = source_with_messages(gateway, 0)
    gateway.add_message(src, "x #yylive #python", hashtags=("#yylive", "#python"))
    gateway.add_message(src, "y #yylive", hashtags=("#yylive",))
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Sorted", "--yes", "--hashtag-to-topic"]
        + ["--topic-skip", "#yylive", "--topic-limit", "3"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    sorted_id = next(c.id for c in gateway.channels.values() if c.title == "Sorted")
    assert {topic.title for topic in gateway.topics[sorted_id]} == {"Python"}
    (run,) = saved_runs(rt)
    kept = Transform.from_json(run.options.transform_json).topics
    assert kept.limit == 3 and next(r for r in kept.rules if r.is_auto_all).skip == ("#yylive",)


def test_a_tuning_flag_without_routing_is_refused_before_anything_is_created(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_with_messages(gateway, 1)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["clone", "--src", "Source", "--dst-new", "Sorted", "--yes", "--topic-skip", "#a"],
        obj=rt,
    )

    assert result.exit_code == 2
    assert gateway.calls_to("create_channel") == []


# ---- inspect ---------------------------------------------------------------------------------


def tagged(gateway: FakeGateway) -> None:
    src, _ = source_with_messages(gateway, 0)
    gateway.add_message(src, "py #python", hashtags=("#python",))
    gateway.add_message(src, "py2 #python", hashtags=("#python",))
    gateway.add_message(src, "rs #rust", hashtags=("#rust",))
    gateway.add_message(src, "other")


def test_the_plan_for_a_destination_without_topics_counts_hashtags_and_units_sent_again(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged(gateway)
    rt = make_runtime(gateway=gateway)
    base = ["inspect", "Source", "--plan", "clone", "--json", "--hashtag-to-topic"]

    forum = json.loads(runner.invoke(app, base, obj=rt).output)["plan"]
    labelled = json.loads(runner.invoke(app, [*base, "--dst-no-topics"], obj=rt).output)["plan"]

    assert forum["topics_are"] == "topics"
    assert {w["strategy"] for w in forum["ways"]} == {"copy"}  # forwarded into topics
    assert labelled["topics_are"] == "hashtags"
    assert [e["topic"] for e in labelled["topics"]] == ["#python", "#rust", None]
    ways = {w["strategy"]: w["units"] for w in labelled["ways"]}
    assert ways == {"copy": 1, "reupload": 3} or ways == {"copy": 1, "reference": 3}


def test_the_plan_table_says_hashtags_when_the_destination_has_no_topics(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["inspect", "Source", "--plan", "clone", "--hashtag-to-topic", "--dst-no-topics"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert "#python (2)" in result.output and "#rust (1)" in result.output


def test_dst_no_topics_needs_a_plan_and_a_clone_or_restore(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged(gateway)
    rt = make_runtime(gateway=gateway)

    alone = runner.invoke(app, ["inspect", "Source", "--dst-no-topics"], obj=rt)
    backup = runner.invoke(
        app, ["inspect", "Source", "--plan", "backup", "--dst-no-topics"], obj=rt
    )
    copy = runner.invoke(
        app,
        ["inspect", "Source", "--plan", "clone", "--hashtag-to-topic", "--dst-no-topics"]
        + ["--mode", "copy"],
        obj=rt,
    )

    assert alone.exit_code == 2 and backup.exit_code == 2
    assert copy.exit_code == 2  # a forward cannot carry the hashtag


def test_by_route_counts_messages_per_destination_topic(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["inspect", "Source", "--by", "route", "--hashtag-to-topic", "--json"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    table = json.loads(result.output)["tables"]["route"]
    assert {row["key"]: row["messages"] for row in table["rows"]} == {
        "Python": 2,
        "Rust": 1,
        "-": 1,
    }


def test_by_route_without_routing_rules_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(app, ["inspect", "Source", "--by", "route"], obj=rt)

    assert result.exit_code == 2 and "--by route" in result.output


# ---- the Explore screen's model --------------------------------------------------------------


def test_the_explorer_grows_a_destination_topic_column_when_rules_are_loaded() -> None:
    units = [unit("#python", i=1), unit("#python", i=2), unit("#rust", i=3), unit(i=4)]
    explorer = Explorer(units)
    assert "route" not in explorer.tables

    explorer.set_rules(with_topic_flags(Transform(), TopicFlags(hashtag_to_topic=True)).topics)

    assert "route" in explorer.tables
    assert {row.key: row.messages for row in explorer.rows("route")} == {
        "Python": 2,
        "Rust": 1,
        "-": 1,
    }
    explorer.add("include", "hashtag", "#python")  # narrowing recounts the column
    assert {row.key: row.messages for row in explorer.rows("route")} == {"Python": 2}
    with pytest.raises(NotFilterable):
        explorer.add("include", "route", "Python")  # a result of the rules, not a property

    explorer.set_rules(None)  # dropping the rules drops the column; the steps stay
    assert "route" not in explorer.tables and len(explorer.steps) == 1


# ---- the wizard's routing question -----------------------------------------------------------


def test_the_wizard_offers_routing_and_a_new_destination_becomes_a_forum(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged(gateway)
    prompter = ScriptedPrompter(
        select=["Source", "last", "Create", "Automatic", "No filter"],
        text=["#other", "Sorted", ""],  # hashtags that name no topic; the new channel's name, about
        confirm=[True, False, True],  # route by hashtag; customise how to copy; the one question
    )
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["clone"], obj=rt)

    assert result.exit_code == 0, result.output
    assert any(t("clone.route_by_hashtag") == m for k, m in prompter.asked if k == "confirm")
    sorted_id = next(c.id for c in gateway.channels.values() if c.title == "Sorted")
    assert {topic.title for topic in gateway.topics[sorted_id]} == {"Python", "Rust"}
    (run,) = saved_runs(rt)
    rule = next(r for r in Transform.from_json(run.options.transform_json).topics.rules)
    assert rule.is_auto_all and rule.pick == "last" and rule.skip == ("#other",)


def test_the_wizard_asks_the_skip_list_again_after_a_value_that_is_no_hashtag(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged(gateway)
    prompter = ScriptedPrompter(
        select=["Source", "first", "Create", "Automatic", "No filter"],
        text=["not a tag", "", "Sorted", ""],
        confirm=[True, False, True],
    )
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["clone"], obj=rt)

    assert result.exit_code == 0, result.output
    assert len([1 for k, m in prompter.asked if k == "text" and m == t("clone.route_skip")]) == 2
    assert prompter.said  # the refusal was said


def test_the_wizard_stays_out_when_a_flag_or_file_already_routes(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged(gateway)
    prompter = ScriptedPrompter(select=["Source", "Create", "No filter"], text=["Sorted", ""])
    rt = make_runtime(gateway=gateway, prompter=prompter, interactive=True)

    result = runner.invoke(app, ["clone", "--hashtag-to-topic"], obj=rt)

    assert result.exit_code == 0, result.output
    assert t("clone.route_by_hashtag") not in [m for k, m in prompter.asked]
