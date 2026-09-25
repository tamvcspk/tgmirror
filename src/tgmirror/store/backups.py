"""``Backup`` as plain data: what the engine and the CLI see of the ``backups`` table (phase 11,
docs/06-lo-trinh.md).

A backup has no destination and no ``msg_map``: unlike a run, its progress lives in the backup
directory itself (``engine/backupdir.py``), not in SQLite (skill ``checkpoint-state`` does not
apply to *resuming* a backup). This table only exists so ``tgmirror history`` can show it, so a
live backup can be paused/stopped from another terminal, and so ``flood_log`` has something to
point at (``engine/flood.py::FloodOwner``) — the same shape as ``runs``' log half, without a
mirror underneath it.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tgmirror.core.gateway import ChannelInfo, ChatKind
from tgmirror.store.runs import Control, RunStatus


@dataclass(frozen=True, slots=True)
class BackupSpec:
    """Everything needed to start a backup."""

    src: ChannelInfo
    dir: str  # the backup directory, as given on the command line (not resolved/normalised here)
    filters_json: str = "{}"
    protected_ack: bool = False  # decision D3's statement, carried into ``backup.json``
    account: str = "default"


@dataclass(frozen=True, slots=True)
class Backup:
    id: int
    account: str
    src_id: int
    src_title: str
    src_kind: ChatKind
    dir: str
    filters_json: str
    status: RunStatus
    control: Control
    cursor_to: int  # highest source id handled so far (mirrors ``backupdir.last_id``)
    resume_at: datetime | None
    fail_reason: str | None
    stats: dict[str, int]
    started_at: datetime
    ended_at: datetime | None
    updated_at: datetime  # doubles as the heartbeat of a running backup

    @property
    def done(self) -> int:
        return self.stats.get("done", 0)

    @property
    def skipped_filter(self) -> int:
        return self.stats.get("skipped_filter", 0)

    @property
    def gone(self) -> int:
        """Messages the source no longer had when a unit was about to be exported."""
        return self.stats.get("gone", 0)

    @property
    def handled(self) -> int:
        return self.done + self.skipped_filter + self.gone


BACKUP_SELECT = (
    "SELECT id, account, src_id, src_title, src_kind, dir, filters_json, status, control, "
    "cursor_to, resume_at, fail_reason, stats_json, started_at, ended_at, updated_at FROM backups"
)


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def backup_from_row(row: Any) -> Backup:
    return Backup(
        id=row["id"],
        account=row["account"],
        src_id=row["src_id"],
        src_title=row["src_title"] or "",
        src_kind=ChatKind(row["src_kind"]),
        dir=row["dir"],
        filters_json=row["filters_json"],
        status=RunStatus(row["status"]),
        control=Control(row["control"]),
        cursor_to=row["cursor_to"],
        resume_at=_time(row["resume_at"]),
        fail_reason=row["fail_reason"],
        stats=json.loads(row["stats_json"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=_time(row["ended_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
