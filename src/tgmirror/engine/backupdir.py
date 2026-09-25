"""The backup directory format (docs/06-lo-trinh.md, Phase 11): self-describing, readable without
tgmirror.

::

    <dir>/
      backup.json      # format, tgmirror version, source, filter, topics, the D3 statement if any
      messages.jsonl    # one line per message, ascending by id
      media/<id>.<ext>  # downloaded files, named by message id like the real gateway names them

Pure I/O and (de)serialisation: no Telegram/Telethon import (hard rule 8), so it is exercised
without a gateway at all. ``engine/backup.py`` is the writer that calls into this.

Progress is the directory itself (docs/06-lo-trinh.md, "Backup nhớ tiến độ bằng chính thư mục"):
writing a file or appending a line twice does no harm, so there is no write-ahead/reconcile here
(skill ``checkpoint-state`` does not apply to this module). Resuming a backup means reading
``last_id`` and continuing after it; a crash mid-line is handled by ``iter_records`` simply
stopping at the last complete line, so the caller re-fetches whatever that line would have been.
"""

import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from tgmirror.core.gateway import ChatKind, ExportedMedia, ExportedMessage, MediaKind

FORMAT_VERSION = 1
MANIFEST_NAME = "backup.json"
MESSAGES_NAME = "messages.jsonl"
MEDIA_DIR = "media"


def manifest_path(directory: Path) -> Path:
    return directory / MANIFEST_NAME


def messages_path(directory: Path) -> Path:
    return directory / MESSAGES_NAME


def media_dir(directory: Path) -> Path:
    return directory / MEDIA_DIR


@dataclass(frozen=True, slots=True)
class BackupTopic:
    id: int
    title: str


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """``backup.json``: everything about the source itself, written once at the start of a backup
    and refreshed (``updated_at``, ``topics``) as the run goes."""

    format_version: int
    tgmirror_version: str
    src_id: int
    src_title: str
    src_kind: ChatKind
    src_about: str
    src_noforwards: bool
    protected_ack: bool  # decision D3's statement was needed and given
    filters_json: str
    topics: tuple[BackupTopic, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def to_json(self) -> str:
        data = asdict(self)
        data["src_kind"] = self.src_kind.value
        return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> Self:
        data = json.loads(text)
        data["src_kind"] = ChatKind(data["src_kind"])
        data["topics"] = tuple(BackupTopic(**t) for t in data.get("topics", ()))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def write_manifest(directory: Path, manifest: BackupManifest) -> None:
    """Write ``backup.json`` atomically: a resume rewrites it every time (topics, ``updated_at``),
    and a crash mid-write must never leave a corrupt manifest a later resume cannot read."""
    directory.mkdir(parents=True, exist_ok=True)
    final = manifest_path(directory)
    tmp = final.with_suffix(".json.tmp")
    tmp.write_text(manifest.to_json(), encoding="utf-8")
    tmp.replace(final)


def read_manifest(directory: Path) -> BackupManifest | None:
    path = manifest_path(directory)
    if not path.exists():
        return None
    return BackupManifest.from_json(path.read_text(encoding="utf-8"))


# ---- messages.jsonl ---------------------------------------------------------------------------


def _media_to_dict(media: ExportedMedia | None) -> dict[str, Any] | None:
    if media is None:
        return None
    data = asdict(media)
    data["kind"] = media.kind.value
    return {k: v for k, v in data.items() if v not in (None, (), False) or k == "kind"}


def _media_from_dict(data: dict[str, Any] | None) -> ExportedMedia | None:
    if data is None:
        return None
    data = dict(data)
    data["kind"] = MediaKind(data["kind"])
    if "poll_options" in data:  # JSON has no tuple: back to what ExportedMedia declares
        data["poll_options"] = tuple(data["poll_options"])
    known = {f.name for f in fields(ExportedMedia)}
    return ExportedMedia(**{k: v for k, v in data.items() if k in known})


def record_to_dict(message: ExportedMessage) -> dict[str, Any]:
    """One ``messages.jsonl`` row, with only the fields that are not their default left in."""
    data: dict[str, Any] = {
        "id": message.id,
        "date": message.date.astimezone(UTC).isoformat(),
        "text_html": message.text_html,
    }
    if message.grouped_id is not None:
        data["grouped_id"] = message.grouped_id
    if message.topic_id is not None:
        data["topic_id"] = message.topic_id
    if message.from_user_id is not None:
        data["from_user_id"] = message.from_user_id
    if message.views is not None:
        data["views"] = message.views
    if (media := _media_to_dict(message.media)) is not None:
        data["media"] = media
    return data


def record_from_dict(data: dict[str, Any]) -> ExportedMessage:
    return ExportedMessage(
        id=data["id"],
        date=datetime.fromisoformat(data["date"]),
        grouped_id=data.get("grouped_id"),
        topic_id=data.get("topic_id"),
        from_user_id=data.get("from_user_id"),
        text_html=data.get("text_html", ""),
        views=data.get("views"),
        media=_media_from_dict(data.get("media")),
    )


def append_records(directory: Path, messages: list[ExportedMessage]) -> None:
    """Append ``messages`` (one unit, kept together) as complete lines, each ending in ``\\n``.

    ``open(..., "a")`` and one ``write`` per line: a crash can only ever leave the *last* line of
    the file incomplete (or, between two calls, entirely absent), never one in the middle — which
    is exactly what ``iter_records`` tolerates.
    """
    directory.mkdir(parents=True, exist_ok=True)
    with messages_path(directory).open("a", encoding="utf-8") as fh:
        for message in messages:
            fh.write(json.dumps(record_to_dict(message), ensure_ascii=False))
            fh.write("\n")


def iter_records(directory: Path) -> list[ExportedMessage]:
    """Every complete line of ``messages.jsonl``, ascending by id (empty: no file yet).

    A last line that is not valid JSON (the writer was killed mid-``write``) is silently dropped:
    it was never acknowledged as done, so the unit it belongs to is fetched again.
    """
    path = messages_path(directory)
    if not path.exists():
        return []
    records: list[ExportedMessage] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            break  # only ever the last line: stop, do not trust anything after a corrupt one
        records.append(record_from_dict(data))
    return records


def last_id(directory: Path) -> int:
    """The highest message id already backed up (0: nothing yet), what a re-run resumes after.

    Scans the file without building ``ExportedMessage``s (``iter_records`` builds every one, which
    a large backup need not pay for just to find where to resume).
    """
    path = messages_path(directory)
    if not path.exists():
        return 0
    found = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                found = json.loads(line)["id"]
            except json.JSONDecodeError:
                break  # only ever the last line
    return found
