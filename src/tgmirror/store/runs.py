"""Runs and mirrors as plain data: what the engine and the CLI see of the ``runs`` and ``mirrors``
tables.

A **run** is one execution of ``tgmirror clone`` / ``tgmirror run``: the log the user can look at
(``tgmirror history``). A **mirror** is the checkpoint of a source/destination pair (cursor,
remembered filter, ``msg_map``): it is what makes the next run a delta and a crash recoverable, and
the user never sees it. Raw SQL stays inside ``store/`` (skill ``checkpoint-state``); everything
outside works with these dataclasses through the intent-level methods of ``Store``.
"""

import json
import sqlite3
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from typing import Any

from tgmirror.core.gateway import ChannelInfo, ChatKind


class RunStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"  # held in place by a live process: it keeps the terminal and the heartbeat
    STOPPED = "stopped"
    WAITING_FLOOD = "waiting_flood"
    DONE = "done"
    FAILED = "failed"


class Control(StrEnum):
    """Written by ``tgmirror pause|stop|run`` (another process); the runner polls it."""

    NONE = "none"
    PAUSE = "pause"
    STOP = "stop"


class MsgStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class FilterChange(StrEnum):
    """What ``start_run`` did with the filter, so the CLI can say it."""

    NEW = "new"  # first run of the pair
    SAME = "same"  # remembered (or given again, unchanged): delta from the cursor
    CHANGED = "changed"  # another filter: the source is read again from the start


@dataclass(frozen=True, slots=True)
class RunOptions:
    """The free-form ``options_json`` column. Unknown keys from a newer version are ignored."""

    batch_size: int = 20
    # Highest message id of the destination when the pair was first cloned. Reconcile reads the
    # destination after ``max(done dst id, dst_base_id)``, so it never scans an old destination.
    dst_base_id: int = 0
    # False reads the whole source instead of letting Telegram narrow it (docs/03-filters.md):
    # the escape hatch for checking that pushdown loses nothing on a real account.
    pushdown: bool = True
    # Strategy B (docs/01-kien-truc.md): what to do with captions and with what cannot be copied.
    caption: str = "keep"  # a ``CaptionMode`` value
    caption_text: str = ""  # what ``--caption append`` adds
    reset_polls: bool = False
    ignore_unsupported: bool = False
    placeholder: bool = False
    # Phase 8: the source is a forum but the destination cannot hold topics, and the run's mode
    # can rewrite text — append a hashtag for the topic instead of dropping it silently.
    topic_as_hashtag: bool = False
    # The user said they may copy a source that restricts saving content (decision D3); ``run`` and
    # ``retry`` carry it on, so they do not ask again.
    protected_ack: bool = False
    # The four below belong to one run, never to the pair (see ``for_pair``).
    # Highest id of the source when the run began (0 = unknown): the total ``status`` measures
    # progress and ETA against. Messages posted meanwhile are not in it.
    src_last_id: int = 0
    # How many messages the run has to look at, counted by Telegram when the run began (``0`` =
    # not known): what progress is measured against. An upper bound: service messages count, and
    # what a client-side filter drops is not subtracted; ``skipped_filter`` closes the gap.
    total_items: int = 0
    # The source restricts saving content, as ``begin_run`` read it when the run began (only read
    # for a run that may download): nothing is then sent by file id, whatever the user said (D3).
    src_protected: bool = False
    # Set on a ``tgmirror retry``: the run whose ``failed`` messages this run sends again.
    retry_of: int | None = None
    # Phase 11b: the backup directory this run reads from instead of a live source. A run-only
    # property (like ``retry_of``), not carried into ``for_pair`` — after a restore the same pair
    # can go back to being driven by a live ``clone``/``run``.
    from_backup: str | None = None

    def for_pair(self, dst_base_id: int) -> "RunOptions":
        """What the mirror remembers: the run-only keys dropped, ``dst_base_id`` as given."""
        return RunOptions(
            batch_size=self.batch_size,
            dst_base_id=dst_base_id,
            pushdown=self.pushdown,
            caption=self.caption,
            caption_text=self.caption_text,
            reset_polls=self.reset_polls,
            ignore_unsupported=self.ignore_unsupported,
            placeholder=self.placeholder,
            protected_ack=self.protected_ack,
            topic_as_hashtag=self.topic_as_hashtag,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "RunOptions":
        data: dict[str, Any] = json.loads(text)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Everything needed to start a run. Both the flags and the wizard end up building one."""

    src: ChannelInfo
    dst: ChannelInfo
    mode: str = "auto"
    options: RunOptions = field(default_factory=RunOptions)
    # Canonical ``FilterSpec.to_json()``. ``None`` keeps the filter the pair already has (a pair
    # seen for the first time clones everything); a string that differs from it restarts the read.
    filters_json: str | None = None
    account: str = "default"


@dataclass(frozen=True, slots=True)
class Mirror:
    id: int
    account: str
    src_id: int
    src_title: str
    src_kind: ChatKind
    dst_id: int
    dst_title: str
    dst_kind: ChatKind
    mode: str
    filters_json: str
    options: RunOptions
    cursor_src_id: int  # largest source id handled (done, failed or left out by the filter)
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Run:
    """One run, joined with its mirror. ``cursor_src_id`` and ``stats`` are this run's own."""

    id: int
    mirror_id: int
    account: str
    src_id: int
    src_title: str
    src_kind: ChatKind
    dst_id: int
    dst_title: str
    dst_kind: ChatKind
    mode: str
    filters_json: str
    options: RunOptions
    status: RunStatus
    control: Control
    cursor_from: int  # where the source was read from
    cursor_src_id: int  # how far this run got
    resume_at: datetime | None
    fail_reason: str | None
    stats: dict[str, int]
    started_at: datetime
    ended_at: datetime | None
    updated_at: datetime  # doubles as the heartbeat of a running run

    @property
    def done(self) -> int:
        return self.stats.get("done", 0)

    @property
    def failed(self) -> int:
        return self.stats.get("failed", 0)

    @property
    def skipped_filter(self) -> int:
        return self.stats.get("skipped_filter", 0)

    @property
    def skipped_unsupported(self) -> int:
        return self.stats.get("skipped_unsupported", 0)

    @property
    def gone(self) -> int:
        """Failed messages a retry found deleted at the source (they cannot be copied any more)."""
        return self.stats.get("gone", 0)

    @property
    def already_done(self) -> int:
        """Messages the run passed because the pair already had them (after a filter change)."""
        return self.stats.get("already_done", 0)

    @property
    def handled(self) -> int:
        """Every message this run has dealt with, whichever way: what progress counts."""
        return (
            self.done
            + self.failed
            + self.skipped_filter
            + self.skipped_unsupported
            + self.gone
            + self.already_done
        )


@dataclass(frozen=True, slots=True)
class StartedRun:
    run: Run
    filters: FilterChange
    # A fresh start: how many copied messages the pair forgot (``None``: not a fresh start).
    forgot: int | None = None


@dataclass(frozen=True, slots=True)
class FailedMessage:
    src_msg_id: int
    reason: str


@dataclass(frozen=True, slots=True)
class FloodEvent:
    ts: datetime
    kind: str
    seconds: int | None
    method: str | None


RUN_SELECT = (
    "SELECT r.id, r.mirror_id, m.account, m.src_id, m.src_title, m.src_kind, m.dst_id, "
    "m.dst_title, m.dst_kind, r.mode, r.filters_json, r.options_json, r.status, r.control, "
    "r.cursor_from, r.cursor_to, r.resume_at, r.fail_reason, r.stats_json, r.started_at, "
    "r.ended_at, r.updated_at FROM runs r JOIN mirrors m ON m.id = r.mirror_id"
)


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def run_from_row(row: sqlite3.Row) -> Run:
    """A row of ``RUN_SELECT``."""
    return Run(
        id=row["id"],
        mirror_id=row["mirror_id"],
        account=row["account"],
        src_id=row["src_id"],
        src_title=row["src_title"] or "",
        src_kind=ChatKind(row["src_kind"]),
        dst_id=row["dst_id"],
        dst_title=row["dst_title"] or "",
        dst_kind=ChatKind(row["dst_kind"]),
        mode=row["mode"],
        filters_json=row["filters_json"],
        options=RunOptions.from_json(row["options_json"]),
        status=RunStatus(row["status"]),
        control=Control(row["control"]),
        cursor_from=row["cursor_from"],
        cursor_src_id=row["cursor_to"],
        resume_at=_time(row["resume_at"]),
        fail_reason=row["fail_reason"],
        stats=json.loads(row["stats_json"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        ended_at=_time(row["ended_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def mirror_from_row(row: sqlite3.Row) -> Mirror:
    return Mirror(
        id=row["id"],
        account=row["account"],
        src_id=row["src_id"],
        src_title=row["src_title"] or "",
        src_kind=ChatKind(row["src_kind"]),
        dst_id=row["dst_id"],
        dst_title=row["dst_title"] or "",
        dst_kind=ChatKind(row["dst_kind"]),
        mode=row["mode"],
        filters_json=row["filters_json"],
        options=RunOptions.from_json(row["options_json"]),
        cursor_src_id=row["cursor_src_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
