"""Export/import of tgmirror's own state (docs/06-lo-trinh.md, Phase 10) — never secrets.

The zip holds ``tgmirror.db`` (a ``VACUUM INTO`` snapshot, consistent even with WAL open) and
``config.toml`` with its ``api_id``/``api_hash`` lines stripped, plus a ``manifest.json`` (format
version, tgmirror version, the database's ``PRAGMA user_version``, a timestamp, and each file's
SHA-256). Left out on purpose: ``sessions/``, any credential (keyring or config.toml) and ``tmp/``.
No table in ``store/schema.sql`` holds an absolute path of the machine it was written on, so a
snapshot from one OS opens correctly on another.
"""

import hashlib
import json
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from tgmirror import __version__
from tgmirror.core.config import config_text_without_credentials
from tgmirror.core.errors import (
    AppDataChecksumMismatch,
    AppDataFormatError,
    AppDataSchemaNewer,
    ExportBusy,
)
from tgmirror.core.paths import Paths
from tgmirror.store.db import Store, default_migrations

FORMAT_VERSION = 1
DB_ENTRY = "tgmirror.db"
CONFIG_ENTRY = "config.toml"
MANIFEST_ENTRY = "manifest.json"


@dataclass(frozen=True, slots=True)
class Manifest:
    format_version: int
    tgmirror_version: str
    db_user_version: int
    created_at: str
    files: dict[str, str]  # zip entry -> sha256 hex digest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def export_appdata(store: Store, paths: Paths, dest: Path) -> Manifest:
    """Write a zip at ``dest``. Refuses (``ExportBusy``) while a run holds the data: a snapshot
    taken mid-run and then run on another machine would send everything a second time."""
    if await store.active_run() is not None:
        raise ExportBusy()

    with tempfile.TemporaryDirectory(prefix="tgmirror-export-") as tmp:
        db_snapshot = Path(tmp) / DB_ENTRY
        await store.export_db(db_snapshot)

        files: dict[str, str] = {DB_ENTRY: _sha256_file(db_snapshot)}
        config_text = config_text_without_credentials(paths)

        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_snapshot, DB_ENTRY)
            if config_text is not None:
                zf.writestr(CONFIG_ENTRY, config_text)
                files[CONFIG_ENTRY] = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
            manifest = Manifest(
                format_version=FORMAT_VERSION,
                tgmirror_version=__version__,
                db_user_version=await store.schema_version(),
                created_at=datetime.now(UTC).isoformat(),
                files=files,
            )
            zf.writestr(MANIFEST_ENTRY, json.dumps(asdict(manifest), indent=2, sort_keys=True))
    return manifest


def existing_data(paths: Paths) -> bool:
    """Whether this machine already has tgmirror state that ``apply_import`` would move aside."""
    return paths.db_path.exists() or paths.config_file.exists()


def verify_archive(src: Path) -> Manifest:
    """Open ``src``, load its manifest and check every file's checksum. Raises before anything on
    disk is touched, so a bad archive never gets a chance to move real data aside."""
    try:
        zf = zipfile.ZipFile(src)
    except (OSError, zipfile.BadZipFile) as exc:
        raise AppDataFormatError(f"cannot open {src}: {exc}") from exc
    with zf:
        try:
            raw = zf.read(MANIFEST_ENTRY)
        except KeyError:
            raise AppDataFormatError(f"{src} has no {MANIFEST_ENTRY}") from None
        try:
            data = json.loads(raw)
            manifest = Manifest(
                format_version=int(data["format_version"]),
                tgmirror_version=str(data["tgmirror_version"]),
                db_user_version=int(data["db_user_version"]),
                created_at=str(data["created_at"]),
                files={str(k): str(v) for k, v in dict(data["files"]).items()},
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise AppDataFormatError(f"malformed {MANIFEST_ENTRY} in {src}: {exc}") from exc

        if manifest.format_version > FORMAT_VERSION:
            raise AppDataFormatError(
                f"{src} is archive format {manifest.format_version}, this tgmirror knows up to "
                f"{FORMAT_VERSION}"
            )
        known_schema = len(default_migrations())
        if manifest.db_user_version > known_schema:
            raise AppDataSchemaNewer(manifest.db_user_version, known_schema)
        if DB_ENTRY not in manifest.files:
            raise AppDataFormatError(f"{src} has no {DB_ENTRY} in its manifest")

        for entry, expected in manifest.files.items():
            try:
                content = zf.read(entry)
            except KeyError:
                raise AppDataFormatError(
                    f"{src} is missing {entry} named in its manifest"
                ) from None
            if hashlib.sha256(content).hexdigest() != expected:
                raise AppDataChecksumMismatch(entry)
    return manifest


def apply_import(paths: Paths, src: Path, manifest: Manifest) -> tuple[Path, ...]:
    """Extract ``src`` (already checked by ``verify_archive``) into ``paths``. Any data this
    machine already has (``tgmirror.db`` and/or ``config.toml``) is moved aside, never deleted;
    returns what was moved (empty when there was nothing)."""
    paths.ensure()
    backed_up: list[Path] = []
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")

    if paths.db_path.exists():
        target = paths.data_dir.with_name(f"{paths.data_dir.name}.bak-{ts}")
        shutil.move(str(paths.data_dir), str(target))
        backed_up.append(target)
        paths.ensure()
    if paths.config_file.exists():
        target = paths.config_file.with_name(f"{paths.config_file.name}.bak-{ts}")
        shutil.move(str(paths.config_file), str(target))
        backed_up.append(target)

    with zipfile.ZipFile(src) as zf:
        paths.db_path.write_bytes(zf.read(DB_ENTRY))
        if CONFIG_ENTRY in manifest.files:
            paths.config_dir.mkdir(parents=True, exist_ok=True)
            paths.config_file.write_bytes(zf.read(CONFIG_ENTRY))
    return tuple(backed_up)
