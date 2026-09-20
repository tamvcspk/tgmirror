"""Filters through the CLI: ``new`` flags and wizard, ``--preview``, ``run --refilter``.

Same setup as ``test_cli_run.py``: the real Typer app on ``FakeGateway``, a scripted prompter and a
SQLite file under tmp_path. Parity rule (skill ``cli-wizard``): flags, a YAML file and the wizard
end in the same stored filter.
"""

import json
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import FakeGateway, ScriptedPrompter
from tests.unit.test_cli_run import saved_jobs, texts
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import MediaKind
from tgmirror.store.jobs import JobStatus

runner = CliRunner()
MakeRuntime = Callable[..., Runtime]

NEWS_SINCE_2024 = {
    "date": {"from": "2024-01-01T00:00:00Z"},
    "include": [{"hashtag": ["#news"], "media": ["video"]}],
}
FLAGS = ["--media", "video", "--hashtag", "#News", "--since", "2024-01-01"]


def source_and_target(gateway: FakeGateway) -> tuple[int, int]:
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    return src.id, dst.id


def tagged_source(gateway: FakeGateway, count: int = 6) -> tuple[int, int]:
    """Odd messages (1, 3, 5, ...) carry the hashtag ``#k``."""
    src, dst = source_and_target(gateway)
    for i in range(1, count + 1):
        tag = i % 2 == 1
        gateway.add_message(src, f"m{i} #k" if tag else f"m{i}", hashtags=("#k",) if tag else ())
    return src, dst


def filters_of(rt: Runtime) -> dict[str, object]:
    (job,) = saved_jobs(rt)
    return json.loads(job.filters_json)


# ---- new with flags -------------------------------------------------------------------------


def test_the_filter_flags_are_stored_on_the_job(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_and_target(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app, ["new", "--src", "Source", "--dst", "Copy", "--yes", *FLAGS], obj=rt
    )

    assert result.exit_code == 0, result.output
    assert filters_of(rt) == NEWS_SINCE_2024


def test_no_filter_flags_store_an_empty_filter(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_and_target(gateway)
    rt = make_runtime(gateway=gateway)

    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes"], obj=rt)

    (job,) = saved_jobs(rt)
    assert job.filters_json == "{}" and job.options.pushdown is True


def test_a_yaml_file_gives_the_same_stored_filter_as_the_flags(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    source_and_target(gateway)
    path = tmp_path / "filters.yaml"
    path.write_text(
        'include:\n  - {media: [video], hashtag: ["#news"]}\ndate: {from: 2024-01-01}\n',
        encoding="utf-8",
    )
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--yes", "--filter-file", str(path)],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert filters_of(rt) == NEWS_SINCE_2024


def test_a_yaml_file_together_with_flags_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    source_and_target(gateway)
    path = tmp_path / "filters.yaml"
    path.write_text("include: [{media: [video]}]\n", encoding="utf-8")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        [
            "new",
            "--src",
            "Source",
            "--dst",
            "Copy",
            "--yes",
            "--filter-file",
            str(path),
            "--media",
            "photo",
        ],
        obj=rt,
    )

    assert result.exit_code == 2 and "--filter-file" in result.output
    assert saved_jobs(rt) == []


def test_a_bad_filter_is_refused_before_anything_is_created(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    gateway.add_channel("Source")
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app, ["new", "--src", "Source", "--dst-new", "Copy", "--yes", "--media", "vidoe"], obj=rt
    )

    assert result.exit_code == 2
    assert "Invalid filter" in result.output and "include[0].media[0]" in result.output
    assert gateway.calls_to("create_channel") == [] and gateway.calls_to("list_channels") == []
    assert saved_jobs(rt) == []


def test_a_missing_filter_file_is_a_usage_error(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    source_and_target(gateway)

    result = runner.invoke(
        app,
        [
            "new",
            "--src",
            "Source",
            "--dst",
            "Copy",
            "--yes",
            "--filter-file",
            str(tmp_path / "no.yaml"),
        ],
        obj=make_runtime(gateway=gateway),
    )

    assert result.exit_code == 2 and "cannot read" in result.output


def test_no_pushdown_is_kept_on_the_job(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    source_and_target(gateway)
    rt = make_runtime(gateway=gateway)

    runner.invoke(
        app, ["new", "--src", "Source", "--dst", "Copy", "--yes", "--no-pushdown"], obj=rt
    )

    (job,) = saved_jobs(rt)
    assert job.options.pushdown is False


def test_a_filtered_job_run_from_the_flags_copies_only_what_matches(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = tagged_source(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        [
            "new",
            "--src",
            "Source",
            "--dst",
            "Copy",
            "--yes",
            "--run",
            "--hashtag",
            "#k",
            "--no-pushdown",
        ],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert texts(gateway, dst) == ["m1 #k", "m3 #k", "m5 #k"]
    assert "3 messages were left out by the filter." in result.output
    assert "3 messages copied" in result.output


# ---- preview --------------------------------------------------------------------------------


def test_preview_shows_how_much_matches_and_a_few_examples(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged_source(gateway)
    rt = make_runtime(gateway=gateway)

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--yes", "--hashtag", "#k", "--preview"],
        obj=rt,
    )

    assert result.exit_code == 0, result.output
    assert "Preview: 3 of the first 6 messages" in result.output
    assert "· m1 #k" in result.output and "· m5 #k" in result.output
    assert len(saved_jobs(rt)) == 1  # a preview without a terminal never blocks


def test_preview_is_off_by_default_without_a_terminal(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    tagged_source(gateway)

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--yes", "--hashtag", "#k"],
        obj=make_runtime(gateway=gateway),
    )

    assert "Preview" not in result.output


def test_preview_of_an_empty_range_says_so(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    tagged_source(gateway)

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--yes", "--since", "2030-01-01", "--preview"],
        obj=make_runtime(gateway=gateway),
    )

    assert "no messages in the chosen range" in result.output


# ---- the wizard -----------------------------------------------------------------------------

CRITERIA = "Pick criteria"


def wizard_rt(
    make_runtime: MakeRuntime, gateway: FakeGateway, root: Path, **answers: object
) -> tuple[Runtime, ScriptedPrompter]:
    prompter = ScriptedPrompter(**answers)  # type: ignore[arg-type]
    return make_runtime(gateway=gateway, prompter=prompter, interactive=True, root=root), prompter


def test_the_wizard_stores_the_same_filter_as_the_flags(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    """Parity rule: the terminal path and the flags end in the same stored filter."""
    flags_gw, wizard_gw = FakeGateway(), FakeGateway()
    for gw in (flags_gw, wizard_gw):
        source_and_target(gw)
    flags_rt = make_runtime(gateway=flags_gw, root=tmp_path / "flags")
    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes", *FLAGS], obj=flags_rt)

    rt, prompter = wizard_rt(
        make_runtime,
        wizard_gw,
        tmp_path / "wizard",
        select=["Source", "Copy", CRITERIA],
        checkbox=[["video"]],
        text=["#News", "", "2024-01-01", "", "", ""],  # hashtags, keywords, since, until, sizes
        confirm=[True, False],  # save after the preview, do not run now
    )
    result = runner.invoke(app, ["new"], obj=rt)

    assert result.exit_code == 0, result.output
    assert filters_of(rt) == filters_of(flags_rt) == NEWS_SINCE_2024
    assert ("confirm", "Save this job?") in prompter.asked


def test_choosing_no_filter_in_the_wizard_stores_an_empty_one_and_shows_no_preview(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    source_and_target(gateway)
    rt, prompter = wizard_rt(
        make_runtime,
        gateway,
        tmp_path / "w",
        select=["Source", "Copy", "No filter"],
        confirm=[False],
    )

    result = runner.invoke(app, ["new"], obj=rt)

    assert result.exit_code == 0, result.output
    (job,) = saved_jobs(rt)
    assert job.filters_json == "{}"
    assert [k for k, _ in prompter.asked if k == "checkbox"] == []
    assert "Preview" not in result.output and ("confirm", "Save this job?") not in prompter.asked


def test_the_wizard_loads_a_yaml_file(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    source_and_target(gateway)
    path = tmp_path / "f.yaml"
    path.write_text("include: [{media: [video], hashtag: ['#news']}]\ndate: {from: 2024-01-01}\n")
    rt, _ = wizard_rt(
        make_runtime,
        gateway,
        tmp_path / "w",
        select=["Source", "Copy", "Load from a YAML file"],
        text=[f'"{path}"'],  # pasted with quotes, as "Copy as path" gives it
        confirm=[True, False],
    )

    result = runner.invoke(app, ["new"], obj=rt)

    assert result.exit_code == 0, result.output
    assert filters_of(rt) == NEWS_SINCE_2024


def test_the_wizard_asks_again_after_an_invalid_answer(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    source_and_target(gateway)
    rt, prompter = wizard_rt(
        make_runtime,
        gateway,
        tmp_path / "w",
        select=["Source", "Copy", CRITERIA],
        checkbox=[["video"], ["video"]],
        text=["", "", "last week", "", "", "", "", "", "2024-01-01", "", "", ""],
        confirm=[True, False],
    )

    result = runner.invoke(app, ["new"], obj=rt)

    assert result.exit_code == 0, result.output
    assert any("not a date like 2024-01-01" in line for line in prompter.said)
    assert filters_of(rt) == {
        "date": {"from": "2024-01-01T00:00:00Z"},
        "include": [{"media": ["video"]}],
    }


def test_declining_the_preview_leaves_nothing_behind(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    src = gateway.add_channel("Source")
    gateway.add_message(src.id, "x #k", hashtags=("#k",))
    rt, _ = wizard_rt(
        make_runtime,
        gateway,
        tmp_path / "w",
        select=["Source", CRITERIA],  # no existing destination: it asks for a new one
        checkbox=[[]],
        text=["Copy", "", "#k", "", "", "", "", ""],
        confirm=[False],  # save after the preview: no
    )

    result = runner.invoke(app, ["new"], obj=rt)

    assert result.exit_code == 1 and "Preview: 1 of the first 1" in result.output
    assert gateway.calls_to("create_channel") == [] and saved_jobs(rt) == []


def test_flags_given_on_a_terminal_do_not_start_the_filter_wizard(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    source_and_target(gateway)
    rt, prompter = wizard_rt(make_runtime, gateway, tmp_path / "w", confirm=[False])

    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy"], obj=rt)

    assert [k for k, _ in prompter.asked if k in ("select", "checkbox")] == []


# ---- run --refilter -------------------------------------------------------------------------


def photo_and_video_source(gateway: FakeGateway) -> tuple[int, int]:
    src, dst = source_and_target(gateway)
    for i in range(1, 7):
        gateway.add_message(src, f"m{i}", media=MediaKind.PHOTO if i % 2 else MediaKind.VIDEO)
    return src, dst


def test_refilter_replaces_the_filter_and_backfills(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    _, dst = photo_and_video_source(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(
        app,
        [
            "new",
            "--src",
            "Source",
            "--dst",
            "Copy",
            "--yes",
            "--run",
            "--media",
            "photo",
            "--no-pushdown",
        ],
        obj=rt,
    )
    assert texts(gateway, dst) == ["m1", "m3", "m5"]

    result = runner.invoke(app, ["run", "1", "--refilter", "--exclude-media", "sticker"], obj=rt)

    assert result.exit_code == 0, result.output
    assert "Replaced the filter of job 1" in result.output
    assert texts(gateway, dst) == ["m1", "m3", "m5", "m2", "m4", "m6"]  # nothing twice
    (job,) = saved_jobs(rt)
    assert json.loads(job.filters_json) == {"exclude": [{"media": ["sticker"]}]}
    assert (job.status, job.done, job.skipped_filter, job.cursor_src_id) == (
        JobStatus.DONE,
        6,
        0,
        6,
    )


def test_refilter_needs_a_filter(make_runtime: MakeRuntime, gateway: FakeGateway) -> None:
    photo_and_video_source(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes"], obj=rt)

    result = runner.invoke(app, ["run", "1", "--refilter"], obj=rt)

    assert result.exit_code == 2 and "--refilter needs the new filter" in result.output


def test_filter_flags_without_refilter_are_refused_not_ignored(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    photo_and_video_source(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes"], obj=rt)

    result = runner.invoke(app, ["run", "1", "--media", "photo"], obj=rt)

    assert result.exit_code == 2 and "--refilter" in result.output
    assert filters_of(rt) == {}
    assert gateway.calls_to("copy_messages") == []


# ---- a pair that already has a job ----------------------------------------------------------


def test_a_filter_for_a_pair_that_already_has_a_job_is_refused_up_front_and_says_how_to_change_it(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    """The real-account slip: preview and 'save?' first, 'job exists' last, then `run 1` ran the
    old unfiltered job. Now it stops before previewing, and names --refilter."""
    tagged_source(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes"], obj=rt)

    result = runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--media", "video", "--preview"],
        obj=rt,
    )

    assert result.exit_code == 2
    assert "Preview" not in result.output  # refused before any read
    assert "NOT applied" in result.output and "run 1 --refilter" in result.output
    (job,) = saved_jobs(rt)
    assert job.filters_json == "{}"


def test_without_a_filter_the_existing_job_message_just_says_to_continue_it(
    make_runtime: MakeRuntime, gateway: FakeGateway
) -> None:
    source_and_target(gateway)
    rt = make_runtime(gateway=gateway)
    runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes"], obj=rt)

    result = runner.invoke(app, ["new", "--src", "Source", "--dst", "Copy", "--yes"], obj=rt)

    assert result.exit_code == 2
    assert "run 1" in result.output and "--refilter" not in result.output


def test_the_wizard_stops_before_asking_for_a_filter_when_the_pair_has_a_job(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    source_and_target(gateway)
    runner.invoke(
        app,
        ["new", "--src", "Source", "--dst", "Copy", "--yes"],
        obj=make_runtime(gateway=gateway, root=tmp_path / "w"),
    )
    rt, prompter = wizard_rt(
        make_runtime, gateway, tmp_path / "w", select=["Source", "Copy"], confirm=[]
    )

    result = runner.invoke(app, ["new"], obj=rt)

    assert result.exit_code == 2
    assert [k for k, _ in prompter.asked if k in ("checkbox", "confirm")] == []
    assert len(prompter.select_labels) == 2  # source and destination, not the filter step
