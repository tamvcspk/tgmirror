"""``tgmirror appdata export|import`` CLI wiring: exit codes, the confirmation before moving
existing data aside, and the printed messages. Store-level behaviour (checksums, stripped
credentials, schema checks) is ``test_appdata.py``'s job.

Plain (non-async) test functions, like ``test_cli_doctor.py``'s: ``CliRunner.invoke`` does its own
``asyncio.run`` internally, so setup below goes through a bare ``asyncio.run`` too.
"""

import asyncio
import zipfile
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway, ScriptedPrompter
from tgmirror.cli.app import app
from tgmirror.cli.runtime import Runtime
from tgmirror.core.paths import Paths
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store.db import Store
from tgmirror.store.runs import RunStatus

MakeRuntime = Callable[..., Runtime]

runner = CliRunner()


def _seed_pair(gateway: FakeGateway, paths: Paths, *, finish: bool = True) -> None:
    async def go() -> None:
        paths.ensure()
        src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
        store = await Store.open(paths.db_path)
        started = await begin_run(store, gateway, src, dst, RunRequest())
        if finish:
            await store.finish(started.run.id, RunStatus.DONE)
        await store.close()

    asyncio.run(go())


def _export(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path, name: str = "src"
) -> Path:
    """A finished pair, exported to ``tmp_path/out.zip``; returns the archive's path."""
    src_paths = Paths.under(tmp_path / name)
    _seed_pair(gateway, src_paths)
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT), root=tmp_path / name)
    archive = tmp_path / "out.zip"
    assert runner.invoke(app, ["appdata", "export", str(archive)], obj=rt).exit_code == 0
    return archive


def test_export_writes_a_zip_and_reports_how_many_files(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    paths = Paths.under(tmp_path)
    _seed_pair(gateway, paths)
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT), root=tmp_path)
    dest = tmp_path / "out.zip"

    result = runner.invoke(app, ["appdata", "export", str(dest)], obj=rt)

    assert result.exit_code == 0, result.output
    assert dest.exists()
    assert "Exported" in result.output
    with zipfile.ZipFile(dest) as zf:
        assert "tgmirror.db" in zf.namelist()


def test_export_refuses_while_a_run_is_live(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    paths = Paths.under(tmp_path)
    _seed_pair(gateway, paths, finish=False)  # still 'running', fresh heartbeat
    rt = make_runtime(gateway=gateway, auth=FakeAuth(logged_in=ACCOUNT), root=tmp_path)

    result = runner.invoke(app, ["appdata", "export", str(tmp_path / "out.zip")], obj=rt)

    assert result.exit_code == 2, result.output
    assert "holding the data" in result.output


def test_import_onto_a_clean_machine_needs_no_confirmation(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    archive = _export(make_runtime, gateway, tmp_path)

    import_rt = make_runtime(root=tmp_path / "dst")
    result = runner.invoke(app, ["appdata", "import", str(archive)], obj=import_rt)

    assert result.exit_code == 0, result.output
    assert "Imported" in result.output
    assert "tgmirror login" in result.output
    assert Paths.under(tmp_path / "dst").db_path.exists()


def test_import_over_existing_data_without_a_terminal_needs_yes(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    archive = _export(make_runtime, gateway, tmp_path)

    dst_paths = Paths.under(tmp_path / "dst")
    dst_paths.ensure()
    dst_paths.db_path.write_bytes(b"already here")
    import_rt = make_runtime(root=tmp_path / "dst")

    result = runner.invoke(app, ["appdata", "import", str(archive)], obj=import_rt)

    assert result.exit_code == 2, result.output
    assert "--yes" in result.output
    assert dst_paths.db_path.read_bytes() == b"already here"  # untouched


def test_import_over_existing_data_with_yes_backs_it_up(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    archive = _export(make_runtime, gateway, tmp_path)

    dst_paths = Paths.under(tmp_path / "dst")
    dst_paths.ensure()
    dst_paths.db_path.write_bytes(b"already here")
    import_rt = make_runtime(root=tmp_path / "dst")

    result = runner.invoke(app, ["appdata", "import", str(archive), "--yes"], obj=import_rt)

    assert result.exit_code == 0, result.output
    assert "moved aside" in result.output
    assert dst_paths.db_path.read_bytes() != b"already here"


def test_import_over_existing_data_interactively_asks_and_honours_a_no(
    make_runtime: MakeRuntime, gateway: FakeGateway, tmp_path: Path
) -> None:
    archive = _export(make_runtime, gateway, tmp_path)

    dst_paths = Paths.under(tmp_path / "dst")
    dst_paths.ensure()
    dst_paths.db_path.write_bytes(b"already here")
    prompter = ScriptedPrompter(confirm=[False])
    import_rt = make_runtime(root=tmp_path / "dst", interactive=True, prompter=prompter)

    result = runner.invoke(app, ["appdata", "import", str(archive)], obj=import_rt)

    assert result.exit_code == 1, result.output
    assert dst_paths.db_path.read_bytes() == b"already here"


def test_import_rejects_a_corrupt_archive(make_runtime: MakeRuntime, tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip file")
    rt = make_runtime(root=tmp_path / "dst")

    result = runner.invoke(app, ["appdata", "import", str(bad)], obj=rt)

    assert result.exit_code == 2, result.output
    assert "Cannot read" in result.output
