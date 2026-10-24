"""``Op`` as plain data: what the engine and the CLI see of the ``ops`` and ``op_items`` tables
(phase 20, docs/07-inspect-transform.md section 6, decision D10).

An operation acts on messages of a chat the account administers, in place: ``delete`` now, ``edit``,
``repost`` and ``undo`` later. It is planned first — the ids it will touch are frozen into
``op_items`` as ``planned`` — and only then applied, so a crash or a Ctrl+C leaves a list that says
exactly what is done, what is not, and (``pending``) what was in flight. Unlike a backup, the
operation's progress *is* in SQLite: it is a write that cannot be undone, and rule 3 of CLAUDE.md
applies (write-ahead ``pending``, one transaction per batch).
"""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

import aiosqlite

from tgmirror.core.gateway import ChannelInfo, ChatKind
from tgmirror.store.runs import Control


class OpKind(StrEnum):
    DELETE = "delete"
    EDIT = "edit"  # phase 20.3: change the text of messages; each item keeps what it was
    UNDO = "undo"  # phase 20.3: put the texts of an edit back (``options_json['undo_of']``)
    REPOST = "repost"  # phase 20.4: post again at the end of the chat, then (optionally) delete


class OpStatus(StrEnum):
    """``planned`` is the one state a run or a backup does not have: the list is frozen and
    waiting for the person's confirmation, nothing has been touched."""

    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"  # held in place by a live process, as a run
    STOPPED = "stopped"
    WAITING_FLOOD = "waiting_flood"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"  # a plan nobody confirmed and the person gave up on


# states of an operation a process may take up again (``tgmirror delete --resume``)
RESUMABLE = (OpStatus.PLANNED, OpStatus.STOPPED, OpStatus.WAITING_FLOOD, OpStatus.FAILED)


class ItemStatus(StrEnum):
    PLANNED = "planned"
    PENDING = "pending"  # written ahead of the Telegram call: its outcome is unknown until read
    REPOSTED = (
        "reposted"  # phase 20.4: the new copy exists (``new_msg_id``), the original not yet gone
    )
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"  # left alone on purpose; ``reason`` says why (gone, changed)


@dataclass(frozen=True, slots=True)
class OpSpec:
    """Everything needed to plan an operation."""

    kind: OpKind
    chat: ChannelInfo
    filters_json: str = "{}"
    options_json: str = "{}"
    account: str = "default"


@dataclass(frozen=True, slots=True)
class PlannedItem:
    """One message an operation will touch. ``unit_id`` is the id of the first message of its unit
    (the message itself, or the head of its album): messages sharing it are never split."""

    msg_id: int
    unit_id: int
    grouped_id: int | None = None
    before_json: str | None = None  # ``edit``/``undo``: ``{"before": ..., "after": ...}`` markup


@dataclass(frozen=True, slots=True)
class OpItem:
    msg_id: int
    unit_id: int
    grouped_id: int | None
    status: ItemStatus
    reason: str | None = None
    before_json: str | None = None
    new_msg_id: int | None = None  # ``repost``: the id of the copy at the end of the chat

    @property
    def texts(self) -> tuple[str, str]:
        """``(before, after)`` of an ``edit``/``undo`` item: what the message read when the plan was
        made, and what it is to read afterwards."""
        data = json.loads(self.before_json or "{}")
        return str(data.get("before", "")), str(data.get("after", ""))


@dataclass(frozen=True, slots=True)
class Op:
    id: int
    account: str
    kind: OpKind
    chat_id: int
    chat_title: str
    chat_kind: ChatKind
    filters_json: str
    options_json: str
    status: OpStatus
    control: Control
    resume_at: datetime | None
    fail_reason: str | None
    stats: dict[str, int]
    created_at: datetime
    ended_at: datetime | None
    updated_at: datetime  # doubles as the heartbeat of a running operation

    @property
    def planned(self) -> int:
        """How many messages the plan froze."""
        return self.stats.get("planned", 0)

    @property
    def done(self) -> int:
        return self.stats.get("done", 0)

    @property
    def skipped(self) -> int:
        """Planned messages left alone because they changed since (the filter no longer matches)."""
        return self.stats.get("skipped", 0)

    @property
    def gone(self) -> int:
        """Planned messages that were no longer there when their turn came."""
        return self.stats.get("gone", 0)

    @property
    def unsupported(self) -> int:
        """``repost``: messages (a poll, a game, ...) that cannot be posted again and were left."""
        return self.stats.get("unsupported", 0)

    @property
    def same_file(self) -> int:
        """``repost``: new messages that still carry their original's file id."""
        return self.stats.get("same_file", 0)

    @property
    def failed(self) -> int:
        return self.stats.get("failed", 0)

    @property
    def handled(self) -> int:
        return self.done + self.skipped + self.gone + self.failed + self.unsupported

    @property
    def delete_original(self) -> bool:
        """A ``repost`` that also deletes what it posted again."""
        return json.loads(self.options_json).get(DELETE_ORIGINAL) is True

    @property
    def silent(self) -> bool:
        """A ``repost`` that does not notify the chat's followers (the default)."""
        return json.loads(self.options_json).get(SILENT, True) is not False

    @property
    def undo_of(self) -> int | None:
        """The ``edit`` an ``undo`` puts back (``None`` for any other operation)."""
        found = json.loads(self.options_json).get(UNDO_OF)
        return found if isinstance(found, int) else None

    @property
    def transform_json(self) -> str | None:
        """The transform an ``edit`` was planned with, as the user's own file read."""
        found = json.loads(self.options_json).get(TRANSFORM)
        return found if isinstance(found, str) else None

    @property
    def backup_dir(self) -> str | None:
        """The directory the plan is backed up into before it is applied (phase 20.2): set when
        the person asked for the safety net, whether or not that backup finished yet."""
        found = json.loads(self.options_json).get(BACKUP_DIR)
        return found if isinstance(found, str) else None

    @property
    def backup_done(self) -> bool:
        """The safety-net backup of this plan finished: applying it needs no second one."""
        return json.loads(self.options_json).get(BACKUP_DONE) is True


BACKUP_DIR = "backup_dir"  # keys of ``ops.options_json`` (phase 20.2)
BACKUP_DONE = "backup_done"
UNDO_OF = "undo_of"  # phase 20.3
DELETE_ORIGINAL = "delete_original"  # phase 20.4
SILENT = "silent"
TRANSFORM = "transform"

OP_SELECT = (
    "SELECT id, account, kind, chat_id, chat_title, chat_kind, filters_json, options_json, status, "
    "control, resume_at, fail_reason, stats_json, created_at, ended_at, updated_at FROM ops"
)


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def op_from_row(row: Any) -> Op:
    return Op(
        id=row["id"],
        account=row["account"],
        kind=OpKind(row["kind"]),
        chat_id=row["chat_id"],
        chat_title=row["chat_title"] or "",
        chat_kind=ChatKind(row["chat_kind"]),
        filters_json=row["filters_json"],
        options_json=row["options_json"],
        status=OpStatus(row["status"]),
        control=Control(row["control"]),
        resume_at=_time(row["resume_at"]),
        fail_reason=row["fail_reason"],
        stats=json.loads(row["stats_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        ended_at=_time(row["ended_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _item(row: Any) -> OpItem:
    return OpItem(
        msg_id=row["msg_id"],
        unit_id=row["unit_id"],
        grouped_id=row["grouped_id"],
        status=ItemStatus(row["status"]),
        reason=row["reason"],
        before_json=row["before_json"],
        new_msg_id=row["new_msg_id"],
    )


def group_units(items: list[OpItem]) -> list[list[OpItem]]:
    """Items (ordered by unit, then message) as the units they belong to."""
    units: list[list[OpItem]] = []
    for item in items:
        if units and units[-1][0].unit_id == item.unit_id:
            units[-1].append(item)
        else:
            units.append([item])
    return units


async def insert_items(
    db: aiosqlite.Connection, op_id: int, items: list[PlannedItem], chunk: int = 5000
) -> None:
    for start in range(0, len(items), chunk):
        await db.executemany(
            "INSERT INTO op_items(op_id, msg_id, unit_id, grouped_id, status, before_json) "
            "VALUES(?, ?, ?, ?, 'planned', ?)",
            [
                (op_id, i.msg_id, i.unit_id, i.grouped_id, i.before_json)
                for i in items[start : start + chunk]
            ],
        )


async def next_units(
    db: aiosqlite.Connection, op_id: int, status: ItemStatus, limit: int
) -> list[list[OpItem]]:
    """The first ``limit`` units that still have an item in ``status``, oldest first, each with
    *all* its items in that status (an album comes out whole)."""
    cur = await db.execute(
        "SELECT op_id, msg_id, unit_id, grouped_id, status, reason, before_json, new_msg_id "
        "FROM op_items WHERE op_id = ? AND status = ? AND unit_id IN ("
        "  SELECT DISTINCT unit_id FROM op_items WHERE op_id = ? AND status = ? "
        "  ORDER BY unit_id LIMIT ?) "
        "ORDER BY unit_id, msg_id",
        (op_id, status, op_id, status, limit),
    )
    return group_units([_item(r) for r in await cur.fetchall()])


async def items_in(db: aiosqlite.Connection, op_id: int, status: ItemStatus) -> list[OpItem]:
    cur = await db.execute(
        "SELECT op_id, msg_id, unit_id, grouped_id, status, reason, before_json, new_msg_id "
        "FROM op_items WHERE op_id = ? AND status = ? ORDER BY unit_id, msg_id",
        (op_id, status),
    )
    return [_item(r) for r in await cur.fetchall()]


async def counts(db: aiosqlite.Connection, op_id: int) -> dict[ItemStatus, int]:
    cur = await db.execute(
        "SELECT status, COUNT(*) FROM op_items WHERE op_id = ? GROUP BY status", (op_id,)
    )
    return {ItemStatus(row[0]): int(row[1]) for row in await cur.fetchall()}
