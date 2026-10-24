"""The screen an operation on a chat's own messages drives while it works (Phase 20.2,
docs/06-lo-trinh.md; ``delete``, ``edit``, ``repost``, the topic moves and ``sync-changes``) —
mirrors ``backup_screen.py::BackupScreen``.

One task, two stretches: when the person asked for the safety net, the plan's messages are backed up
first (``BackupWriter`` with the ids the plan froze, drawn by ``BackupTuiReporter``), and only if
that finished is the plan taken up and deleted (``OpRunner``, drawn by ``OpTuiReporter`` below).
``p``/``r``/``q`` act on whichever stretch is running; ``q`` during the backup leaves the plan
saved and deletes nothing. The control loop (``stop_on_interrupt``, reporting once, exit 130) is
``ui/menu/task_screen.py::TaskScreen``.
"""

import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.core.config import Limits
from tgmirror.core.gateway import TelegramGateway
from tgmirror.engine.backup import BackupWriter
from tgmirror.engine.ops import OpBackup, OpRunner, begin_op, settle_op_backup
from tgmirror.engine.status import op_estimate
from tgmirror.engine.transfer import Transfer
from tgmirror.store.db import Store, utc_now
from tgmirror.store.ops import Op, OpStatus
from tgmirror.ui.lines import (
    backup_result_lines,
    backup_start_line,
    delete_backup_done_line,
    delete_backup_stopped_line,
    delete_result_lines,
    delete_start_line,
)
from tgmirror.ui.menu.backup_screen import BackupTuiReporter
from tgmirror.ui.menu.task_screen import TaskScreen
from tgmirror.ui.messages import t
from tgmirror.ui.progress import OpLineReporter, bar, floods_tail, progress_line

RECENT_LINES = 5  # as ``BackupTuiReporter``: the last few notice lines


class OpTuiReporter:
    """Renders a delete in flight, driven by the same two ``engine.ops.Reporter`` calls as
    ``OpLineReporter``; like ``BackupTuiReporter`` it owns no ``Live``, the screen pulls
    ``render()`` each redraw."""

    def __init__(
        self,
        op: Op,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._op = op
        self._clock = clock
        self._now = now
        self._recent: list[str] = []
        self._lines = OpLineReporter(self._buffer, clock=clock)
        self._floods = 0
        self._last_flood: float | None = None
        self._paused = False

    def _buffer(self, line: str) -> None:
        self._recent.append(line)
        del self._recent[:-RECENT_LINES]

    def notice(self, code: str, **params: object) -> None:
        if code == "paused":
            self._paused = True
        elif code == "resumed":
            self._paused = False
        if code in ("flood_waiting", "throttled"):
            self._floods += 1
            self._last_flood = self._clock()
        self._lines.notice(code, **params)

    def progress(self, op: Op) -> None:
        self._op = op

    def transfer(self, transfer: Transfer) -> None:
        """A repost or a move downloads and uploads: the same \"big file\" lines a clone prints."""
        self._lines.transfer(transfer)

    def render(self) -> RenderableType:
        op = self._op
        est = op_estimate(op, now=self._now(), live=not self._paused)
        progress = progress_line(op.handled, op.planned, est.fraction, est.speed, est.eta)
        counts = t(
            f"{op.kind}.progress",
            id=op.id,
            done=op.done,
            planned=op.planned,
            skipped=op.skipped,
            gone=op.gone,
        )
        counts += floods_tail(
            self._floods, timedelta(seconds=max(self._clock() - (self._last_flood or 0.0), 0.0))
        )
        lines = [
            Text.assemble(bar(est.fraction), "  ", progress),
            Text(counts),
            *(Text(line, style="dim") for line in self._recent),
        ]
        if self._paused:
            lines.append(Text(t("delete.paused")))
        return Group(*lines)


class OpScreen(TaskScreen[Op]):
    def __init__(
        self,
        store: Store,
        gateway: TelegramGateway,
        op: Op,
        *,
        limits: Limits,
        backup: OpBackup | None = None,
        ack: bool = False,
        tmp_dir: Path | None = None,
        then: Sequence[Op] = (),
        backup_of: Op | None = None,
    ) -> None:
        super().__init__()
        self._backup_of = backup_of  # the operation the backup is for, when not the first one
        self._ack = ack  # decision D3's statement, for an operation that downloads
        self._tmp_dir = tmp_dir
        self._then = list(then)  # operations to carry out one after another once this one is done
        self.op_id = op.id  # MenuApp reads this so its "running elsewhere" badge skips it
        self._store = store
        self._gateway = gateway
        self._op = op
        self._limits = limits
        self._backup = backup
        if backup is None:
            self._reporter: BackupTuiReporter | OpTuiReporter = OpTuiReporter(op)
            self._lines = [delete_start_line(op)]
        else:
            self._reporter = BackupTuiReporter(backup.backup)
            self._lines = [
                backup_start_line(backup.backup.id, op.chat_title, str(backup.directory))
            ]

    async def _run(self) -> Op:
        op = self._op
        if self._backup is not None and not await self._back_up(
            self._backup_of or op, self._backup
        ):
            final = await self._store.get_op(op.id)
            assert final is not None
            return final  # still planned: nothing was deleted
        final = await self._carry_out(op, announce=self._backup is not None)
        for following in self._then:  # the next only when this one finished: `sync-changes`
            if final.status is not OpStatus.DONE:
                break
            self._lines.extend(delete_result_lines(final))
            final = await self._carry_out(following, announce=True)
        return final

    async def _carry_out(self, op: Op, *, announce: bool) -> Op:
        running = await begin_op(self._store, op.id, gateway=self._gateway, ack=self._ack)
        if announce:  # what came before is above; say what comes now
            self._lines.append(delete_start_line(running))
        reporter = OpTuiReporter(running)
        self._reporter = reporter
        runner = OpRunner(
            self._store,
            self._gateway,
            self._limits,
            reporter=reporter,
            control=self._control,
            tmp_dir=self._tmp_dir,
        )
        return await runner.run(running)

    async def _back_up(self, op: Op, backup: OpBackup) -> bool:
        writer = BackupWriter(
            self._store, self._gateway, self._limits, reporter=self._reporter, control=self._control
        )
        final = await writer.run(
            backup.backup, backup.directory, backup.manifest, only_ids=backup.ids
        )
        self._lines.extend(backup_result_lines(final, hint=False))
        if not await settle_op_backup(self._store, op, final):
            self._lines.append(delete_backup_stopped_line(op, final))
            return False
        self._lines.append(delete_backup_done_line(str(backup.directory)))
        return True

    def _result_lines(self, final: Op) -> list[str]:
        if final.status is OpStatus.PLANNED:
            return []  # the backup did not finish: its lines said so already
        return delete_result_lines(final)
