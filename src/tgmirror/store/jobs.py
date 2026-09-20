"""Job rows as plain data: what the engine and the CLI see of the ``jobs`` table.

Raw SQL stays inside ``store/`` (skill ``checkpoint-state``); everything outside works with these
dataclasses through the intent-level methods of ``Store`` (``store/db.py``).
"""

import json
import sqlite3
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from typing import Any

from tgmirror.core.gateway import ChannelInfo, ChatKind


class JobStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    WAITING_FLOOD = "waiting_flood"
    DONE = "done"
    FAILED = "failed"


class Control(StrEnum):
    """Written by ``tgmirror pause|stop`` (another process); the runner polls it between batches."""

    NONE = "none"
    PAUSE = "pause"
    STOP = "stop"


class MsgStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class JobOptions:
    """The free-form ``options_json`` column. Unknown keys from a newer version are ignored."""

    batch_size: int = 20
    # Highest message id of the destination when the job was created. Reconcile reads the
    # destination after ``max(done dst id, dst_base_id)``, so it never scans an old destination.
    dst_base_id: int = 0
    # False reads the whole source instead of letting Telegram narrow it (docs/03-filters.md):
    # the escape hatch for checking that pushdown loses nothing on a real account.
    pushdown: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "JobOptions":
        data: dict[str, Any] = json.loads(text)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True, slots=True)
class JobSpec:
    """Everything needed to create a job. Both the flags and the wizard end up building one."""

    name: str
    src: ChannelInfo
    dst: ChannelInfo
    mode: str = "auto"
    options: JobOptions = field(default_factory=JobOptions)
    filters_json: str = "{}"  # canonical ``FilterSpec.to_json()``; ``{}`` clones everything
    account: str = "default"


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    name: str
    account: str
    src_id: int
    src_title: str
    src_kind: ChatKind
    dst_id: int
    dst_title: str
    mode: str
    filters_json: str
    options: JobOptions
    status: JobStatus
    control: Control
    cursor_src_id: int
    resume_at: datetime | None
    fail_reason: str | None
    stats: dict[str, int]
    created_at: datetime
    updated_at: datetime

    @property
    def done(self) -> int:
        return self.stats.get("done", 0)

    @property
    def failed(self) -> int:
        return self.stats.get("failed", 0)

    @property
    def skipped_filter(self) -> int:
        return self.stats.get("skipped_filter", 0)


def job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        name=row["name"],
        account=row["account"],
        src_id=row["src_id"],
        src_title=row["src_title"] or "",
        src_kind=ChatKind(row["src_kind"]),
        dst_id=row["dst_id"],
        dst_title=row["dst_title"] or "",
        mode=row["mode"],
        filters_json=row["filters_json"],
        options=JobOptions.from_json(row["options_json"]),
        status=JobStatus(row["status"]),
        control=Control(row["control"]),
        cursor_src_id=row["cursor_src_id"],
        resume_at=datetime.fromisoformat(row["resume_at"]) if row["resume_at"] else None,
        fail_reason=row["fail_reason"],
        stats=json.loads(row["stats_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
