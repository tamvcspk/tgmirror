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
``last_id`` and continuing after it; a crash mid-line is handled by ``iter_records``/``last_id``
tolerating exactly one broken line — the last one, never one in the middle (``CorruptMessagesFile``,
N3, Phase 15b) — so the caller re-fetches whatever that line would have been. Only ``\n`` ends a
line (N2, Phase 15b): a text field may legally contain U+2028/U+2029/U+0085, which
``str.splitlines`` also treats as line breaks but Telegram/JSON does not, so using it here would
cut such a message's own JSON in half.
"""

import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from tgmirror.core.errors import TgMirrorError
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


class CorruptMessagesFile(TgMirrorError):
    """A line other than the last one in ``messages.jsonl`` is not valid JSON. Unlike a killed-
    mid-write last line (tolerated, dropped, refetched), this is not something a resume can safely
    ignore, so backing up or restoring from this directory refuses until the user fixes or removes
    the offending line."""

    def __init__(self, path: Path, line_number: int) -> None:
        super().__init__(f"{path}: line {line_number} is not valid JSON")
        self.path = path
        self.line_number = line_number


def _split_lines(text: str) -> list[str]:
    """``text.split("\\n")``, minus the one empty element every file ending in ``\\n`` produces —
    never ``str.splitlines()``, which also breaks on U+2028/U+2029/U+0085 (see module docstring)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


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
    it was never acknowledged as done, so the unit it belongs to is fetched again. Any other line
    that is not valid JSON raises ``CorruptMessagesFile`` instead — that can only mean disk/file
    damage, not a normal crash, and continuing past it would silently lose everything after it.
    """
    path = messages_path(directory)
    if not path.exists():
        return []
    lines = _split_lines(path.read_text(encoding="utf-8"))
    records: list[ExportedMessage] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                break  # only ever the last line may be an unfinished write
            raise CorruptMessagesFile(path, i + 1) from None
        records.append(record_from_dict(data))
    return records


def last_id(directory: Path) -> int:
    """The highest message id already backed up (0: nothing yet), what a re-run resumes after.

    Scans lines without building ``ExportedMessage``s (``iter_records`` builds every one, which a
    large backup need not pay for just to find where to resume) — same last-line tolerance and
    mid-file ``CorruptMessagesFile`` as ``iter_records``.
    """
    path = messages_path(directory)
    if not path.exists():
        return 0
    lines = _split_lines(path.read_text(encoding="utf-8"))
    found = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            found = json.loads(line)["id"]
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                break
            raise CorruptMessagesFile(path, i + 1) from None
    return found


def repair_trailing_line(directory: Path) -> None:
    """Cut off a final line left incomplete by a kill mid-``write`` — the one case
    ``iter_records``/``last_id`` tolerate — so the *next* ``append_records`` starts a fresh line
    instead of writing onto the broken one and burying it, unfixable, in the middle of the file
    forever (N3, Phase 15b). Call once when a backup resumes into a directory that already has
    ``messages.jsonl``, before anything reads ``last_id`` from it. A no-op when the last line is
    already complete JSON or the file is empty/missing; a corrupt line elsewhere is left alone —
    ``iter_records``/``last_id`` still raise ``CorruptMessagesFile`` for that, on purpose.
    """
    path = messages_path(directory)
    if not path.exists():
        return
    lines = _split_lines(path.read_text(encoding="utf-8"))
    if not lines or not lines[-1].strip():
        return
    try:
        json.loads(lines[-1])
    except json.JSONDecodeError:
        pass
    else:
        return  # last line is already complete: nothing to repair
    fixed = "\n".join(lines[:-1])
    if fixed:
        fixed += "\n"
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(fixed, encoding="utf-8")
    tmp.replace(path)
