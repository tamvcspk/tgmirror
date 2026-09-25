"""``tgmirror backup``: read a source into a backup directory (docs/06-lo-trinh.md, Phase 11).

One-directional and much smaller than ``engine/runner.py``'s loop: there is no destination, no
``msg_map``, and no write-ahead/reconcile (``engine/backupdir.py``'s docstring says why — the
directory is its own checkpoint). What is shared with a clone: decision D3 (a source that
restricts saving content needs the user's statement, exactly as strategy B does), reading through
``FloodGuard`` (hard rule 1), and pause/stop/resume in place.

``begin_backup`` validates and opens the log row (mirrors ``engine.runs.begin_run``);
``BackupWriter.run`` carries it out.
"""

import asyncio
import contextlib
import random
from contextlib import aclosing
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from tgmirror import __version__
from tgmirror.core.config import Limits
from tgmirror.core.errors import FloodWait, PeerFlood, PerMessage, TgMirrorError, Transient
from tgmirror.core.gateway import ChannelInfo, ChatKind, MessageReader, TelegramGateway, Unit
from tgmirror.core.limiter import Limiter, Sleep
from tgmirror.engine import backupdir, planner
from tgmirror.engine.endpoints import SourceRestricted
from tgmirror.engine.flood import FloodGuard, FloodOwner, Interrupted
from tgmirror.engine.planner import Skip
from tgmirror.engine.runner import RunControl
from tgmirror.engine.runs import check_runnable
from tgmirror.engine.transfer import Transfer, TransferTracker
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import plan_read
from tgmirror.store.backups import Backup, BackupSpec
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.runs import Control, RunStatus

FILTERS_CHANGED = "filters_changed"  # a resume whose filter no longer matches what was recorded


class BackupError(TgMirrorError):
    """Base class for problems starting or continuing a backup."""


class FiltersChanged(BackupError):
    """This directory already has messages backed up with a different filter (phase 11): unlike
    a clone, a backup keeps no record of what a filter skipped, so it cannot safely resume with a
    new one. Use a new directory instead."""

    def __init__(self, directory: Path) -> None:
        super().__init__(f"{directory} was backed up with a different filter")
        self.directory = directory

    key = FILTERS_CHANGED


class WrongSource(BackupError):
    """This directory already holds a backup of a different source."""

    def __init__(self, directory: Path, existing_title: str) -> None:
        super().__init__(f"{directory} already holds a backup of {existing_title!r}")
        self.directory = directory
        self.existing_title = existing_title


class BackupNeedsAcknowledgement(BackupError):
    """The source restricts saving content and this backup has no confirmation that the user may
    copy it (decision D3): distinct from ``engine.runs.NeedsAcknowledgement`` only so
    ``cli/errors.py`` can point the user at ``tgmirror backup`` instead of ``tgmirror clone``."""

    def __init__(self, title: str) -> None:
        super().__init__(f"{title!r} restricts saving content and the backup was not confirmed")
        self.title = title


async def check_source_for_backup(
    gateway: TelegramGateway, src: ChannelInfo, *, ack: bool
) -> ChannelInfo:
    """Decision D3 for a backup: it always downloads (there is no server-side "copy" for a
    directory), so a source that restricts saving content needs the same statement strategy B
    does. Re-reads the source (one setup request, hard rule 1's exception) because it may have
    turned the restriction on since it was chosen. Returns the freshly read ``ChannelInfo``."""
    current = await gateway.get_channel(src.id)
    if not current.noforwards:
        return current
    if ack:
        return current
    if not current.is_admin:
        raise SourceRestricted(current)
    raise BackupNeedsAcknowledgement(current.title)


async def begin_backup(
    store: Store,
    gateway: TelegramGateway,
    src: ChannelInfo,
    directory: Path,
    *,
    filters_json: str | None = None,
    protected_ack: bool = False,
    force: bool = False,
) -> tuple[Backup, backupdir.BackupManifest]:
    """Validate and start a backup into ``directory``; one already there continues (delta, by
    ``backupdir.last_id``) as long as the source and the filter match what is on disk.
    """
    current = await check_source_for_backup(gateway, src, ack=protected_ack)
    existing = backupdir.read_manifest(directory)
    filters = filters_json if filters_json is not None else "{}"
    if existing is not None:
        if existing.src_id != src.id:
            raise WrongSource(directory, existing.src_title)
        if filters_json is not None and filters_json != existing.filters_json:
            raise FiltersChanged(directory)
        filters = existing.filters_json
        protected_ack = protected_ack or existing.protected_ack
    dir_key = str(directory)
    if (previous := await store.latest_backup(dir_key)) is not None:
        check_runnable(previous, utc_now())  # refuse what Telegram already told us it would reject
    spec = BackupSpec(src=current, dir=dir_key, filters_json=filters, protected_ack=protected_ack)
    backup = await store.start_backup(spec, force=force)
    topics: tuple[backupdir.BackupTopic, ...] = ()
    if current.kind is ChatKind.FORUM:
        found = await gateway.list_topics(current.id)
        topics = tuple(backupdir.BackupTopic(t.id, t.title) for t in found)
    now = backup.started_at.isoformat()
    manifest = backupdir.BackupManifest(
        format_version=backupdir.FORMAT_VERSION,
        tgmirror_version=__version__,
        src_id=current.id,
        src_title=current.title,
        src_kind=current.kind,
        src_about="",
        src_noforwards=current.noforwards,
        protected_ack=protected_ack,
        filters_json=filters,
        topics=topics,
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
    )
    backupdir.write_manifest(directory, manifest)
    return backup, manifest


class Reporter(Protocol):
    """As ``engine.runner.Reporter``, but ``progress`` carries a ``Backup`` (phase 11)."""

    def notice(self, code: str, **params: object) -> None: ...
    def progress(self, backup: Backup) -> None: ...
    def transfer(self, transfer: Transfer) -> None: ...


class NullReporter:
    def notice(self, code: str, **params: object) -> None:
        pass

    def progress(self, backup: Backup) -> None:
        pass

    def transfer(self, transfer: Transfer) -> None:
        pass


@dataclass(frozen=True, slots=True)
class BackupTiming:
    poll_interval: float = 2.0
    heartbeat_interval: float = 30.0


class BackupWriter:
    """Carries out a backup already opened by ``begin_backup``, batch of one unit at a time."""

    def __init__(
        self,
        store: Store,
        gateway: TelegramGateway,
        limits: Limits,
        *,
        reporter: Reporter | None = None,
        control: RunControl | None = None,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        clock: Clock = utc_now,
        timing: BackupTiming | None = None,
        wait: bool = False,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._limits = limits
        self._reporter: Reporter = reporter or NullReporter()
        self._control = control or RunControl()
        self._sleep = sleep
        self._rng = rng
        self._clock = clock
        self._timing = timing or BackupTiming()
        self._wait = wait
        self._tracker = TransferTracker(self._reporter.transfer)

    async def run(
        self,
        backup: Backup,
        directory: Path,
        manifest: backupdir.BackupManifest,
        *,
        pushdown: bool = True,
    ) -> Backup:
        limiter = Limiter(
            self._limits,
            lambda seconds: self._nap(seconds, backup.id),
            state=await self._store.load_limiter_state(backup.account),
            clock=self._clock,
            rng=self._rng,
        )
        guard = FloodGuard(
            limiter=limiter,
            store=self._store,
            owner=FloodOwner.of_backup(backup),
            limits=self._limits,
            notifier=self._reporter,
            nap=lambda seconds: self._nap(seconds, backup.id),
            rng=self._rng,
            wait=self._wait,
        )
        reader = guard.reader(self._gateway)
        heartbeat = asyncio.create_task(self._heartbeat(backup.id))
        media_dir = backupdir.media_dir(directory)
        media_dir.mkdir(parents=True, exist_ok=True)
        try:
            cursor = backupdir.last_id(directory)
            spec = FilterSpec.from_json(manifest.filters_json)
            plan = plan_read(spec, cursor, pushdown=pushdown)
            matcher = None if spec.is_empty else Matcher(spec)
            status = RunStatus.DONE
            async with aclosing(
                planner.units(
                    reader,
                    backup.src_id,
                    min_id=plan.min_id,
                    filters=plan.server,
                    matcher=matcher,
                    complete_albums=plan.complete_albums,
                )
            ) as stream:
                async for item in stream:
                    if not await self._gate(backup):
                        status = RunStatus.STOPPED
                        break
                    backup = await self._handle(backup, reader, item, directory, media_dir)
            await self._store.finish_backup(backup.id, status)
        except Interrupted:
            await self._store.finish_backup(backup.id, RunStatus.STOPPED)
        except FloodWait as exc:
            resume_at = self._clock() + timedelta(seconds=exc.seconds)
            await self._store.finish_backup(backup.id, RunStatus.WAITING_FLOOD, resume_at=resume_at)
            self._reporter.notice("flood_stopped", seconds=exc.seconds, resume_at=resume_at)
            raise
        except PeerFlood:
            await self._store.finish_backup(backup.id, RunStatus.FAILED, fail_reason="peer_flood")
            raise
        except TgMirrorError as exc:
            reason = "transient" if isinstance(exc, Transient) else f"{type(exc).__name__}: {exc}"
            await self._store.finish_backup(backup.id, RunStatus.FAILED, fail_reason=reason[:200])
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        final = await self._store.get_backup(backup.id)
        assert final is not None
        return final

    async def _handle(
        self,
        backup: Backup,
        reader: MessageReader,
        item: Unit | Skip,
        directory: Path,
        media_dir: Path,
    ) -> Backup:
        if isinstance(item, Skip):
            updated = await self._store.advance_backup(
                backup.id, item.last_id, extra_stats={"skipped_filter": item.count}
            )
            self._reporter.progress(updated)
            return updated
        unit = item
        try:
            records = await reader.export_unit(backup.src_id, unit, media_dir, self._tracker.update)
        except PerMessage:  # deleted at the source since it was listed: nothing to back up
            self._reporter.notice("gone", id=unit.ids[0])
            updated = await self._store.advance_backup(
                backup.id, unit.ids[-1], extra_stats={"gone": len(unit.messages)}
            )
            self._reporter.progress(updated)
            return updated
        await asyncio.to_thread(backupdir.append_records, directory, records)
        updated = await self._store.advance_backup(
            backup.id, unit.ids[-1], extra_stats={"done": len(unit.messages)}
        )
        self._reporter.progress(updated)
        return updated

    # ---- control (mirrors engine.runner.Runner) --------------------------------------------

    async def _requested(self, backup_id: int) -> Control | None:
        if self._control.stop_requested:
            return Control.STOP
        if self._control.take_resume():
            await self._store.set_backup_control(backup_id, Control.NONE)
        control = await self._store.read_backup_control(backup_id)
        if control is Control.STOP:
            return Control.STOP
        if self._control.pause_requested or control is Control.PAUSE:
            return Control.PAUSE
        return None

    async def _gate(self, backup: Backup) -> bool:
        ctl = await self._requested(backup.id)
        if ctl is None:
            return True
        if ctl is Control.STOP:
            return False
        return await self._hold(backup)

    async def _hold(self, backup: Backup) -> bool:
        await self._store.set_backup_status(backup.id, RunStatus.PAUSED)
        self._reporter.notice("paused")
        while True:
            await self._sleep(self._timing.poll_interval)
            ctl = await self._requested(backup.id)
            if ctl is Control.STOP:
                return False
            if ctl is None:
                await self._store.set_backup_status(backup.id, RunStatus.RUNNING)
                self._reporter.notice("resumed")
                return True

    async def _nap(self, seconds: float, backup_id: int) -> None:
        remaining = seconds
        while remaining > 0:
            step = min(remaining, self._timing.poll_interval)
            await self._sleep(step)
            remaining -= step
            if await self._requested(backup_id) is Control.STOP:
                raise Interrupted

    async def _heartbeat(self, backup_id: int) -> None:
        while True:
            await asyncio.sleep(self._timing.heartbeat_interval)
            await self._store.heartbeat_backup(backup_id)
