"""``tgmirror appdata export``/``import`` (docs/06-lo-trinh.md, Phase 10): snapshot + restore of
``tgmirror.db`` and a credential-stripped ``config.toml``, never a secret. Store-level behaviour
only; ``test_cli_appdata.py`` covers the CLI wiring (confirmation, exit codes, messages).
"""

import json
import zipfile
from pathlib import Path

import pytest

from tests.fakes import FakeGateway
from tgmirror.core.errors import (
    AppDataChecksumMismatch,
    AppDataFormatError,
    AppDataSchemaNewer,
    ExportBusy,
)
from tgmirror.core.paths import Paths
from tgmirror.engine.runs import RunRequest, begin_run
from tgmirror.store import appdata
from tgmirror.store.db import Store, default_migrations
from tgmirror.store.runs import RunStatus


async def _seed_pair(store: Store, gateway: FakeGateway, *, finish: bool) -> int:
    src, dst = gateway.add_channel("Source"), gateway.add_channel("Copy")
    started = await begin_run(store, gateway, src, dst, RunRequest())
    if finish:
        await store.finish(started.run.id, RunStatus.DONE)
    return started.run.id


def _write_config(paths: Paths, text: str) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(text, encoding="utf-8")


async def test_export_then_import_roundtrip(tmp_path: Path) -> None:
    src_paths = Paths.under(tmp_path / "src")
    src_paths.ensure()
    _write_config(src_paths, 'api_id = 1\napi_hash = "abc"\n\n[limits]\nbatch_size = 77\n')
    gateway = FakeGateway()
    store = await Store.open(src_paths.db_path)
    await _seed_pair(store, gateway, finish=True)

    dest = tmp_path / "export.zip"
    manifest = await appdata.export_appdata(store, src_paths, dest)
    await store.close()

    assert set(manifest.files) == {"tgmirror.db", "config.toml"}

    dst_paths = Paths.under(tmp_path / "dst")
    loaded = appdata.verify_archive(dest)
    backed_up = appdata.apply_import(dst_paths, dest, loaded)

    assert backed_up == ()
    assert dst_paths.db_path.exists()
    config_text = dst_paths.config_file.read_text(encoding="utf-8")
    assert "batch_size = 77" in config_text
    assert "api_id" not in config_text
    assert "api_hash" not in config_text

    imported = await Store.open(dst_paths.db_path)
    runs = await imported.list_runs()
    assert len(runs) == 1
    assert runs[0].status == RunStatus.DONE
    await imported.close()


async def test_export_strips_credentials_from_the_packaged_config(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.ensure()
    _write_config(paths, 'api_id = 12345\napi_hash = "0123456789abcdef"\n')
    store = await Store.open(paths.db_path)

    dest = tmp_path / "export.zip"
    await appdata.export_appdata(store, paths, dest)
    await store.close()

    with zipfile.ZipFile(dest) as zf:
        text = zf.read("config.toml").decode("utf-8")
    assert "api_id" not in text
    assert "0123456789abcdef" not in text


async def test_export_has_no_config_entry_when_there_is_no_config_file(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.ensure()
    store = await Store.open(paths.db_path)

    manifest = await appdata.export_appdata(store, paths, tmp_path / "export.zip")
    await store.close()

    assert set(manifest.files) == {"tgmirror.db"}


async def test_export_refuses_while_a_run_is_live(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.ensure()
    gateway = FakeGateway()
    store = await Store.open(paths.db_path)
    await _seed_pair(store, gateway, finish=False)  # still 'running', fresh heartbeat

    with pytest.raises(ExportBusy):
        await appdata.export_appdata(store, paths, tmp_path / "export.zip")
    await store.close()


def test_verify_archive_rejects_a_zip_without_a_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("tgmirror.db", b"not a real db")

    with pytest.raises(AppDataFormatError):
        appdata.verify_archive(bad)


async def test_verify_archive_rejects_a_tampered_file(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.ensure()
    store = await Store.open(paths.db_path)
    good = tmp_path / "good.zip"
    await appdata.export_appdata(store, paths, good)
    await store.close()

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            content = src.read(name)
            dst.writestr(name, content + b"\x00" if name == "tgmirror.db" else content)

    with pytest.raises(AppDataChecksumMismatch):
        appdata.verify_archive(tampered)


async def test_verify_archive_rejects_a_newer_schema(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.ensure()
    store = await Store.open(paths.db_path)
    good = tmp_path / "good.zip"
    await appdata.export_appdata(store, paths, good)
    await store.close()

    newer = tmp_path / "newer.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(newer, "w") as dst:
        for name in src.namelist():
            content = src.read(name)
            if name == "manifest.json":
                data = json.loads(content)
                data["db_user_version"] = len(default_migrations()) + 1
                content = json.dumps(data).encode("utf-8")
            dst.writestr(name, content)

    with pytest.raises(AppDataSchemaNewer):
        appdata.verify_archive(newer)


async def test_apply_import_backs_up_existing_data_instead_of_deleting_it(tmp_path: Path) -> None:
    src_paths = Paths.under(tmp_path / "src")
    src_paths.ensure()
    store = await Store.open(src_paths.db_path)
    dest = tmp_path / "export.zip"
    await appdata.export_appdata(store, src_paths, dest)
    await store.close()

    dst_paths = Paths.under(tmp_path / "dst")
    dst_paths.ensure()
    dst_paths.db_path.write_bytes(b"old database")
    _write_config(dst_paths, "batch_size_marker = 'old config'\n")

    manifest = appdata.verify_archive(dest)
    backed_up = appdata.apply_import(dst_paths, dest, manifest)

    assert len(backed_up) == 2
    assert dst_paths.db_path.read_bytes() != b"old database"  # overwritten by the import
    for path in backed_up:
        assert path.exists()
    old_db_backup = next(p for p in backed_up if (p / "tgmirror.db").exists())
    assert (old_db_backup / "tgmirror.db").read_bytes() == b"old database"
    old_config_backup = next(p for p in backed_up if p.name.startswith("config.toml.bak-"))
    assert "old config" in old_config_backup.read_text(encoding="utf-8")


def test_existing_data_is_false_on_a_clean_machine(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)

    assert appdata.existing_data(paths) is False


def test_existing_data_is_true_when_the_db_or_config_already_exists(tmp_path: Path) -> None:
    paths = Paths.under(tmp_path)
    paths.ensure()
    paths.db_path.write_bytes(b"x")

    assert appdata.existing_data(paths) is True
