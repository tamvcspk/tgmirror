"""Operations on a chat's own messages: ``tgmirror delete`` (docs/07-inspect-transform.md section 6,
docs/06-lo-trinh.md Phase 20, decision D10).

Four steps, each of which stands alone (the second only when the person asked for it):

1. ``plan_delete`` reads the chat with the filter and **freezes** the ids it would remove into the
   store (``ops`` + ``op_items``, status ``planned``). Nothing on Telegram changes. The CLI shows
   the summary and asks for the confirmation (typing the number back, or ``--yes --expect N``).
2. ``begin_op_backup`` + ``BackupWriter.run(only_ids=...)`` + ``settle_op_backup``: the safety net
   (phase 20.2, docs/07 section 6.3 point 3) saves exactly the frozen ids into a backup directory
   *before* anything is deleted, and the plan only goes on if that backup finished.
3. ``begin_op`` takes a confirmed plan up (``running``).
4. ``OpRunner.run`` applies it batch by batch. For each batch it **reads the messages again by
   id** and checks the filter against what is there now (a message edited or removed since the
   plan is left alone), writes the batch ``pending`` ahead of the call, deletes, and marks it
   ``done`` — one transaction each (rule 3). After a crash the ``pending`` rows are settled first:
   an id that is no longer in the chat was deleted, one that is still there goes back to
   ``planned``. So running it again never leaves anything out and never touches anything outside
   the plan.

An album is never split (rule 4): a batch is cut between units. Every Telegram call goes through
``FloodGuard`` (rule 1): the plan's scan like ``inspect``'s, the apply with the ``delete`` write
lane of the chat.
"""

import asyncio
import contextlib
import json
import random
from collections import deque
from collections.abc import Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from tgmirror.core.config import Limits
from tgmirror.core.errors import (
    BudgetReached,
    FloodWait,
    ForwardsRestricted,
    NoPermission,
    PeerFlood,
    PerMessage,
    TgMirrorError,
    Transient,
)
from tgmirror.core.gateway import (
    MAX_IDS_PER_CALL,
    CaptionPolicy,
    ChannelInfo,
    MessageReader,
    SrcMessage,
    TelegramGateway,
    TransferPhase,
    Unit,
)
from tgmirror.core.limiter import DELETE, EDIT, SEND, Limiter, Sleep
from tgmirror.engine import planner, reupload
from tgmirror.engine.backup import begin_backup
from tgmirror.engine.backupdir import BackupManifest
from tgmirror.engine.flood import FloodGuard, FloodOwner, Interrupted, inspect_guard, learn_context
from tgmirror.engine.planner import Skip
from tgmirror.engine.reconcile import Outcome, judge
from tgmirror.engine.runner import RunControl
from tgmirror.engine.runs import DAILY_CAP, RunError
from tgmirror.engine.transfer import Transfer, TransferTracker
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.filters.pushdown import plan_read
from tgmirror.store.backups import Backup
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.ops import (
    BACKUP_DIR,
    BACKUP_DONE,
    DELETE_ORIGINAL,
    SILENT,
    TRANSFORM,
    UNDO_OF,
    ItemStatus,
    Op,
    OpItem,
    OpKind,
    OpSpec,
    OpStatus,
    PlannedItem,
)
from tgmirror.store.runs import Control, RunStatus

UNITS_PER_READ = MAX_IDS_PER_CALL  # units taken from the plan at a time: enough to fill a batch
SAMPLE_EDGE = 5  # messages shown from each end of the plan
SAMPLE_TEXT = 60  # characters of a sampled message

REPOST_BATCH = 10  # messages posted again between two looks at pause/stop
EDIT_BATCH = 10  # messages edited between two looks at pause/stop: an edit is one call each
SAMPLE_DIFF = 90  # characters of a text shown in a diff

SKIP_UNSUPPORTED = "unsupported"  # a poll, a game, ...: nothing to post again, left alone
SKIP_AMBIGUOUS = "ambiguous"  # a crash left it unclear whether the copy exists: not touched
SKIP_GONE = "gone"  # the message was no longer in the chat when its turn came
SKIP_CHANGED = "changed"  # it was there, but the filter no longer matches it


# a unit that cannot be sent again (a poll: it would come back without its votes; a game, an
# invoice) is left out, and so its original is never deleted
_REPOST_OPTIONS = reupload.Options(ignore_unsupported=True)


class OpError(RunError):
    """Base class for problems with the operation the user named or is starting (exit code 2)."""


class OpNotFound(OpError):
    def __init__(self, ref: str | None) -> None:
        super().__init__("there is no operation yet" if ref is None else f"no operation: {ref!r}")
        self.ref = ref  # ``None``: there is none at all


class NotAdmin(NoPermission):
    """The account does not administer the chat, so it may not remove messages from it."""

    def __init__(self, chat: ChannelInfo) -> None:
        super().__init__(f"not an administrator of {chat.title!r}")
        self.chat = chat


@dataclass(frozen=True, slots=True)
class Sample:
    id: int
    date: datetime
    media: str
    text: str


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """What the plan holds, for the person to judge before confirming."""

    messages: int
    units: int
    albums: int
    by_media: dict[str, int]
    by_month: dict[str, int]
    first: tuple[Sample, ...]
    last: tuple[Sample, ...]  # the other end; empty when the plan is short enough to show whole


@dataclass(frozen=True, slots=True)
class DeletePlan:
    """The frozen plan: ``op`` is ``None`` when the filter selected nothing (no row is made)."""

    op: Op | None
    summary: PlanSummary


class Reporter(Protocol):
    def notice(self, code: str, **params: object) -> None: ...
    def progress(self, op: Op) -> None: ...


class NullReporter:
    def notice(self, code: str, **params: object) -> None:
        pass

    def progress(self, op: Op) -> None:
        pass

    def transfer(self, transfer: Transfer) -> None:
        pass


# ---- planning ------------------------------------------------------------------------------------


def _sample(message: SrcMessage) -> Sample:
    text = " ".join(message.text.split())
    if len(text) > SAMPLE_TEXT:
        text = text[: SAMPLE_TEXT - 1] + "…"
    return Sample(message.id, message.date, message.media.value, text)


async def plan_delete(
    store: Store,
    gateway: TelegramGateway,
    chat: ChannelInfo,
    spec: FilterSpec,
    limits: Limits,
    *,
    pushdown: bool = True,
    notifier: Reporter | None = None,
    nap: Sleep = asyncio.sleep,
    kind: OpKind = OpKind.DELETE,
    options_json: str = "{}",
) -> DeletePlan:
    """Read ``chat`` with ``spec`` and freeze what it selects as a ``planned`` delete (or, with
    ``kind``, the same list for another operation on whole units: ``repost``, which also notes
    each message's forum topic).

    Re-reads the chat first (one setup request, hard rule 1's exception) because it is the rights
    that count now that decide, not those of the list it was chosen from. The scan itself is paced
    and retried like ``inspect``'s. A filter that selects nothing makes no plan."""
    current = await gateway.get_channel(chat.id)
    if not current.is_admin:
        raise NotAdmin(current)
    guard = await inspect_guard(store, limits, notifier=notifier or NullReporter(), nap=nap)
    reader = guard.reader(gateway)
    read = plan_read(spec, 0, pushdown=pushdown)
    matcher = None if spec.is_empty else Matcher(spec)

    items: list[PlannedItem] = []
    by_media: dict[str, int] = {}
    by_month: dict[str, int] = {}
    first: list[Sample] = []
    last: deque[Sample] = deque(maxlen=SAMPLE_EDGE)
    units = albums = 0
    async with aclosing(
        planner.units(
            reader,
            current.id,
            min_id=read.min_id,
            filters=read.server,
            matcher=matcher,
            complete_albums=read.complete_albums,
        )
    ) as stream:
        async for item in stream:
            if isinstance(item, Skip):  # judged and not selected (the server drops most itself)
                continue
            units += 1
            albums += item.is_album
            for m in item.messages:
                noted = None if kind is OpKind.DELETE else json.dumps({"topic": m.topic_id})
                items.append(PlannedItem(m.id, item.ids[0], m.grouped_id, noted))
                by_media[m.media.value] = by_media.get(m.media.value, 0) + 1
                month = m.date.strftime("%Y-%m")
                by_month[month] = by_month.get(month, 0) + 1
                if len(first) < SAMPLE_EDGE:
                    first.append(_sample(m))
                else:
                    last.append(_sample(m))
    summary = PlanSummary(
        messages=len(items),
        units=units,
        albums=albums,
        by_media=dict(sorted(by_media.items(), key=lambda kv: -kv[1])),
        by_month=dict(sorted(by_month.items())),
        first=tuple(first),
        last=tuple(last),
    )
    if not items:
        return DeletePlan(None, summary)
    op = await store.create_op(OpSpec(kind, current, spec.to_json(), options_json), items)
    return DeletePlan(op, summary)


@dataclass(frozen=True, slots=True)
class EditSample:
    id: int
    before: str
    after: str


@dataclass(frozen=True, slots=True)
class EditSummary:
    """What an ``edit`` plan holds: the messages whose text would change, with a few diffs, and
    what it leaves out and why."""

    messages: int
    units: int
    by_media: dict[str, int]
    by_month: dict[str, int]
    first: tuple[EditSample, ...]
    last: tuple[EditSample, ...]
    unchanged: int  # selected, but the operations leave their text as it is
    lossy: int  # selected, but their formatting would not survive an edit: never touched


@dataclass(frozen=True, slots=True)
class MirrorScope:
    """``edit --mirror`` (phase 20.5): only the messages a pair copied are edited. ``copies`` are
    the ids of those copies in the destination chat; ``options`` is noted on the operation."""

    copies: frozenset[int]
    options: dict[str, object]


@dataclass(frozen=True, slots=True)
class EditPlan:
    """``op`` is ``None`` when no selected message would change (no row is made)."""

    op: Op | None
    summary: EditSummary


class UndoError(OpError):
    """An ``undo`` that cannot be planned (exit code 2): said in the person's terms by the CLI."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


def _squeeze(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SAMPLE_DIFF else text[: SAMPLE_DIFF - 1] + "…"


async def plan_edit(
    store: Store,
    gateway: TelegramGateway,
    chat: ChannelInfo,
    spec: FilterSpec,
    policy: CaptionPolicy,
    transform_json: str,
    limits: Limits,
    *,
    pushdown: bool = True,
    notifier: Reporter | None = None,
    nap: Sleep = asyncio.sleep,
    scope: MirrorScope | None = None,
) -> EditPlan:
    """Read ``chat`` with ``spec``, work out what ``policy`` would make of each selected message's
    text and freeze those that would change, with the text before and after, as a ``planned``
    edit (phase 20.3). Nothing on Telegram changes. A message whose formatting would not survive
    being written back is left out and counted: ``undo`` could not restore it."""
    current = await gateway.get_channel(chat.id)
    if not current.is_admin:
        raise NotAdmin(current)
    guard = await inspect_guard(store, limits, notifier=notifier or NullReporter(), nap=nap)
    reader = guard.reader(gateway)
    read = plan_read(spec, 0, pushdown=pushdown)
    matcher = None if spec.is_empty else Matcher(spec)

    items: list[PlannedItem] = []
    by_media: dict[str, int] = {}
    by_month: dict[str, int] = {}
    first: list[EditSample] = []
    last: deque[EditSample] = deque(maxlen=SAMPLE_EDGE)
    counts = {"units": 0, "unchanged": 0, "lossy": 0}
    buffer: list[tuple[SrcMessage, int]] = []  # (message, id of the first message of its unit)
    changed_units: set[int] = set()

    async def flush() -> None:
        if not buffer:
            return
        by_id = {m.id: (m, unit_id) for m, unit_id in buffer}
        ids = list(by_id)
        buffer.clear()
        found = await guard.read(
            "rewrite_messages", lambda: gateway.rewrite_messages(current.id, ids, policy)
        )
        for rewrite in found:
            message, unit_id = by_id[rewrite.id]
            if not rewrite.lossless:
                counts["lossy"] += 1
            elif rewrite.before == rewrite.after:
                counts["unchanged"] += 1
            else:
                items.append(
                    PlannedItem(
                        message.id,
                        unit_id,
                        message.grouped_id,
                        json.dumps({"before": rewrite.before, "after": rewrite.after}),
                    )
                )
                changed_units.add(unit_id)
                by_media[message.media.value] = by_media.get(message.media.value, 0) + 1
                month = message.date.strftime("%Y-%m")
                by_month[month] = by_month.get(month, 0) + 1
                sample = EditSample(message.id, _squeeze(rewrite.before), _squeeze(rewrite.after))
                if len(first) < SAMPLE_EDGE:
                    first.append(sample)
                else:
                    last.append(sample)

    async with aclosing(
        planner.units(
            reader,
            current.id,
            min_id=read.min_id,
            filters=read.server,
            matcher=matcher,
            complete_albums=read.complete_albums,
        )
    ) as stream:
        async for item in stream:
            if isinstance(item, Skip):
                continue
            members = [m for m in item.messages if scope is None or m.id in scope.copies]
            if not members:
                continue  # not a copy of this pair: not ours to edit
            counts["units"] += 1
            buffer.extend((m, item.ids[0]) for m in members)
            if len(buffer) >= MAX_IDS_PER_CALL:
                await flush()
        await flush()
    items.sort(key=lambda i: (i.unit_id, i.msg_id))
    summary = EditSummary(
        messages=len(items),
        units=len(changed_units),
        by_media=dict(sorted(by_media.items(), key=lambda kv: -kv[1])),
        by_month=dict(sorted(by_month.items())),
        first=tuple(first),
        last=tuple(last),
        unchanged=counts["unchanged"],
        lossy=counts["lossy"],
    )
    if not items:
        return EditPlan(None, summary)
    noted: dict[str, object] = {TRANSFORM: transform_json, **(scope.options if scope else {})}
    options = json.dumps(noted, sort_keys=True)
    op = await store.create_op(OpSpec(OpKind.EDIT, current, spec.to_json(), options), items)
    return EditPlan(op, summary)


@dataclass(frozen=True, slots=True)
class UndoPlan:
    op: Op
    first: tuple[EditSample, ...]
    last: tuple[EditSample, ...]


async def plan_undo(store: Store, gateway: TelegramGateway, edit: Op) -> UndoPlan:
    """Plan putting back the texts an ``edit`` changed: one ``undo`` item per message it edited
    (``done``), carrying the two texts the other way round. Raises ``UndoError`` for anything but
    an edit that changed something, or one already undone (or being undone)."""
    if edit.kind is not OpKind.EDIT:
        raise UndoError("undo.not_an_edit", id=edit.id)
    if edit.status in (OpStatus.PLANNED, OpStatus.CANCELLED):
        raise UndoError("undo.never_applied", id=edit.id)
    for other in await store.list_ops(200):
        if other.undo_of == edit.id and other.status is not OpStatus.CANCELLED:
            raise UndoError("undo.already", id=edit.id, undo=other.id)
    done = await store.op_items_in(edit.id, ItemStatus.DONE)
    if not done:
        raise UndoError("undo.nothing", id=edit.id)
    chat = await gateway.get_channel(edit.chat_id)
    if not chat.is_admin:
        raise NotAdmin(chat)
    planned = [
        PlannedItem(
            i.msg_id,
            i.unit_id,
            i.grouped_id,
            json.dumps({"before": i.texts[1], "after": i.texts[0]}),
        )
        for i in done
    ]
    samples = [EditSample(i.msg_id, _squeeze(i.texts[1]), _squeeze(i.texts[0])) for i in done]
    op = await store.create_op(
        OpSpec(OpKind.UNDO, chat, edit.filters_json, json.dumps({UNDO_OF: edit.id})), planned
    )
    return UndoPlan(op, tuple(samples[:SAMPLE_EDGE]), tuple(samples[SAMPLE_EDGE:][-SAMPLE_EDGE:]))


async def resolve_op(store: Store, ref: str | None) -> Op:
    """An operation by its number (``12`` or ``O12``), or the latest when ``ref`` is ``None``."""
    if ref is None:
        if (latest := await store.latest_op()) is None:
            raise OpNotFound(None)
        return latest
    number = ref.strip().removeprefix("O").removeprefix("o")
    if number.isdecimal() and (op := await store.get_op(int(number))) is not None:
        return op
    raise OpNotFound(ref)


class RepostNeedsAcknowledgement(OpError):
    """A repost always downloads and uploads (decision D3), and this chat restricts saving
    content: it needs the person's statement."""

    def __init__(self, title: str) -> None:
        super().__init__(f"{title!r} restricts saving content and the repost was not confirmed")
        self.title = title


async def plan_repost(
    store: Store,
    gateway: TelegramGateway,
    chat: ChannelInfo,
    spec: FilterSpec,
    limits: Limits,
    *,
    delete_original: bool = False,
    silent: bool = True,
    pushdown: bool = True,
    notifier: Reporter | None = None,
    nap: Sleep = asyncio.sleep,
) -> DeletePlan:
    """Freeze what ``spec`` selects as a ``planned`` repost (phase 20.4): each unit is downloaded
    and uploaded again at the end of the chat, as a new message with a new file id, and — with
    ``delete_original`` — the original is deleted once its copy exists."""
    options = json.dumps({DELETE_ORIGINAL: delete_original, SILENT: silent}, sort_keys=True)
    return await plan_delete(
        store,
        gateway,
        chat,
        spec,
        limits,
        pushdown=pushdown,
        notifier=notifier,
        nap=nap,
        kind=OpKind.REPOST,
        options_json=options,
    )


async def begin_op(
    store: Store,
    op_id: int,
    *,
    force: bool = False,
    gateway: TelegramGateway | None = None,
    ack: bool = False,
) -> Op:
    """Take a confirmed (or interrupted) operation up: ``running``. ``OpBusy`` when another process
    works on the same chat. A repost needs ``gateway`` (it re-reads the chat) and, for a chat that
    restricts saving content, ``ack``: decision D3, checked here so no caller can skip it."""
    op = await store.get_op(op_id)
    if op is not None and op.kind is OpKind.REPOST:
        assert gateway is not None, "a repost re-reads the chat: begin_op needs the gateway"
        current = await gateway.get_channel(op.chat_id)
        if current.noforwards and not ack:
            raise RepostNeedsAcknowledgement(current.title)
    return await store.start_op(op_id, force=force)


# ---- the safety net ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpBackup:
    """A backup of a plan, opened and ready for ``BackupWriter.run(..., only_ids=ids)``."""

    backup: Backup
    manifest: BackupManifest
    directory: Path
    ids: tuple[int, ...]


async def begin_op_backup(
    store: Store,
    gateway: TelegramGateway,
    op: Op,
    directory: Path,
    *,
    ack: bool = False,
    force: bool = False,
) -> OpBackup:
    """Open the backup that goes before ``op`` is applied: the messages the plan froze, into
    ``directory`` (D3 as for any backup, ``begin_backup``). Remembers the directory on the plan, so
    an attempt that did not finish is carried on by the next one and ``history`` can say where it
    is. Nothing is deleted by this."""
    src = await gateway.get_channel(op.chat_id)
    backup, manifest = await begin_backup(
        store, gateway, src, directory, filters_json=op.filters_json, protected_ack=ack, force=force
    )
    await store.set_op_options(op.id, **{BACKUP_DIR: str(directory), BACKUP_DONE: False})
    items = await store.op_items_in(op.id, ItemStatus.PLANNED)
    return OpBackup(backup, manifest, directory, tuple(sorted(i.msg_id for i in items)))


async def settle_op_backup(store: Store, op: Op, final: Backup) -> bool:
    """Record how the safety-net backup ended. ``True``: it finished, so the plan may be applied;
    anything else (stopped, a flood, a failure) leaves the plan unapplied and ``False``."""
    finished = final.status is RunStatus.DONE
    await store.set_op_options(op.id, **{BACKUP_DONE: finished})
    return finished


# ---- applying ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpTiming:
    poll_interval: float = 2.0
    heartbeat_interval: float = 30.0


def pack(
    units: Sequence[Sequence[OpItem]], size: int = MAX_IDS_PER_CALL
) -> list[list[Sequence[OpItem]]]:
    """Units cut into batches of at most ``size`` messages, never between the messages of a unit."""
    batches: list[list[Sequence[OpItem]]] = []
    used = 0
    for unit in units:
        if not batches or used + len(unit) > size:
            batches.append([])
            used = 0
        batches[-1].append(unit)
        used += len(unit)
    return batches


def _ignore_transfer(transfer: Transfer) -> None:
    pass


def _noted(item: OpItem) -> dict[str, int]:
    """What a ``repost`` noted on an item: its forum topic and the chat's tail before the send."""
    data = json.loads(item.before_json or "{}")
    return {k: v for k, v in data.items() if isinstance(v, int)}


class OpRunner:
    """Carries out a plan already taken up by ``begin_op``, batch by batch."""

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
        timing: OpTiming | None = None,
        wait: bool = False,
        tmp_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._limits = limits
        self._tmp_dir = tmp_dir  # where a repost downloads to
        self._reporter: Reporter = reporter or NullReporter()
        # a reporter that has no use for transfers (the delete/edit ones) need not hear them
        self._tracker = TransferTracker(getattr(self._reporter, "transfer", _ignore_transfer))
        self._control = control or RunControl()
        self._sleep = sleep
        self._rng = rng
        self._clock = clock
        self._timing = timing or OpTiming()
        self._wait = wait

    async def run(self, op: Op) -> Op:
        limiter = Limiter(
            self._limits,
            lambda seconds: self._nap(seconds, op.id),
            state=await self._store.load_limiter_state(op.account),
            clock=self._clock,
            rng=self._rng,
        )
        guard = FloodGuard(
            limiter=limiter,
            store=self._store,
            owner=FloodOwner.of_op(op),
            limits=self._limits,
            notifier=self._reporter,
            nap=lambda seconds: self._nap(seconds, op.id),
            rng=self._rng,
            wait=self._wait,
        )
        reader = guard.reader(self._gateway)
        heartbeat = asyncio.create_task(self._heartbeat(op.id))
        try:
            await learn_context(guard, self._gateway)  # Premium, for the flood log
            spec = FilterSpec.from_json(op.filters_json)
            matcher = None if spec.is_empty else Matcher(spec)
            edits = op.kind in (OpKind.EDIT, OpKind.UNDO)  # one call per message
            reposts = op.kind is OpKind.REPOST
            if edits:
                op = await self._settle_edits(op, guard)
            elif reposts:
                op = await self._repost_settle(op, guard, reader)
            else:
                op = await self._settle_pending(op, reader)
            status = OpStatus.DONE
            while status is OpStatus.DONE:
                units = await self._store.op_units(op.id, ItemStatus.PLANNED, UNITS_PER_READ)
                if not units:
                    break
                size = EDIT_BATCH if edits else REPOST_BATCH if reposts else MAX_IDS_PER_CALL
                for batch in pack(units, size):
                    if not await self._gate(op):
                        status = OpStatus.STOPPED
                        break
                    if edits:
                        op = await self._edit_batch(op, guard, batch)
                    elif reposts:
                        op = await self._repost_batch(op, guard, reader, matcher, batch)
                    else:
                        op = await self._delete_batch(op, guard, reader, matcher, batch)
                    # what the lane has sent is kept with the batch, so a later run (and a budget
                    # learned from a long flood) counts the messages this one deleted or edited
                    await self._store.save_limiter_state(op.account, limiter.state)
            await self._store.finish_op(op.id, status)
        except Interrupted:
            await self._store.finish_op(op.id, OpStatus.STOPPED)
        except FloodWait as exc:
            resume_at = self._clock() + timedelta(seconds=exc.seconds)
            await self._store.finish_op(op.id, OpStatus.WAITING_FLOOD, resume_at=resume_at)
            self._reporter.notice("flood_stopped", seconds=exc.seconds, resume_at=resume_at)
            raise
        except BudgetReached as exc:  # our own budget: a rest, not a failure
            await self._store.finish_op(
                op.id, OpStatus.WAITING_FLOOD, fail_reason=DAILY_CAP, resume_at=exc.resume_at
            )
            raise
        except PeerFlood:
            await self._store.finish_op(op.id, OpStatus.FAILED, fail_reason="peer_flood")
            raise
        except TgMirrorError as exc:
            # Transient leaves its batch ``pending`` on purpose (the call may have gone through);
            # the next run settles it by reading the chat. Any other error left nothing pending.
            reason = "transient" if isinstance(exc, Transient) else f"{type(exc).__name__}: {exc}"
            await self._store.finish_op(op.id, OpStatus.FAILED, fail_reason=reason[:200])
            raise
        except BaseException as exc:
            # as ``Runner.run`` (N4, Phase 15b): whatever it was, close the row instead of leaving
            # it ``running`` with a fresh heartbeat for a process that is gone
            interrupted = isinstance(exc, KeyboardInterrupt | asyncio.CancelledError)
            reason = "interrupted" if interrupted else f"crashed: {type(exc).__name__}: {exc}"
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(
                    self._store.finish_op(op.id, OpStatus.FAILED, fail_reason=reason[:200])
                )
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        final = await self._store.get_op(op.id)
        assert final is not None
        return final

    async def _settle_pending(self, op: Op, reader: MessageReader) -> Op:
        """What a process that died mid-batch left ``pending``: read the chat. An id that is gone
        was deleted (``done``); one that is still there was not (back to ``planned``, and the
        batch that finds it will read it again like any other)."""
        pending = await self._store.op_items_in(op.id, ItemStatus.PENDING)
        if not pending:
            return op
        ids = [i.msg_id for i in pending]
        present: set[int] = set()
        for start in range(0, len(ids), MAX_IDS_PER_CALL):
            found = await reader.get_messages(op.chat_id, ids[start : start + MAX_IDS_PER_CALL])
            present.update(m.id for m in found)
        deleted = [i for i in ids if i not in present]
        again = [i for i in ids if i in present]
        if deleted:
            op = await self._store.mark_op_items(
                op.id, deleted, ItemStatus.DONE, stats={"done": len(deleted)}
            )
        if again:
            op = await self._store.mark_op_items(op.id, again, ItemStatus.PLANNED)
        self._reporter.notice("settled", deleted=len(deleted), again=len(again))
        return op

    async def _delete_batch(
        self,
        op: Op,
        guard: FloodGuard,
        reader: MessageReader,
        matcher: Matcher | None,
        batch: Sequence[Sequence[OpItem]],
    ) -> Op:
        ids = [item.msg_id for unit in batch for item in unit]
        live = {m.id: m for m in await reader.get_messages(op.chat_id, ids)}
        keep: list[int] = []
        changed: list[int] = []
        gone: list[int] = []
        for unit in batch:
            members = [live[i.msg_id] for i in unit if i.msg_id in live]
            gone.extend(i.msg_id for i in unit if i.msg_id not in live)
            if not members:
                continue
            if self._still_selected(members, matcher):
                keep.extend(m.id for m in members)
            else:
                changed.extend(m.id for m in members)
        if gone:
            op = await self._store.mark_op_items(
                op.id, gone, ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": len(gone)}
            )
        if changed:
            op = await self._store.mark_op_items(
                op.id,
                changed,
                ItemStatus.SKIPPED,
                reason=SKIP_CHANGED,
                stats={"skipped": len(changed)},
            )
        if keep:
            op = await self._delete(op, guard, keep)
        self._reporter.progress(op)
        return op

    @staticmethod
    def _still_selected(members: list[SrcMessage], matcher: Matcher | None) -> bool:
        """The filter applied again to what the chat holds now. A unit that is no longer one album
        (an edit cannot do that, but Telegram has surprised before) is left alone."""
        if matcher is None:
            return True
        if len(members) > 1 and (
            members[0].grouped_id is None or len({m.grouped_id for m in members}) != 1
        ):
            return False
        return matcher.matches(Unit(tuple(members)))

    async def _delete(self, op: Op, guard: FloodGuard, ids: list[int]) -> Op:
        await guard.pace(
            len(ids), action=DELETE
        )  # may rest, or raise BudgetReached: nothing marked
        op = await self._store.mark_op_items(op.id, ids, ItemStatus.PENDING)  # write-ahead
        try:
            deleted = await guard.write(
                "delete_messages", len(ids), lambda: self._gateway.delete_messages(op.chat_id, ids)
            )
        except PerMessage:  # every id is invalid: they are gone already
            return await self._store.mark_op_items(
                op.id, ids, ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": len(ids)}
            )
        except (FloodWait, PeerFlood, Interrupted, NoPermission):
            # Telegram refused, or we gave up before asking again: nothing was deleted
            await self._store.mark_op_items(op.id, ids, ItemStatus.PLANNED)
            raise
        # ``deleted`` can be less than asked when a message vanished between the read and the call
        deleted = min(deleted, len(ids))
        return await self._store.mark_op_items(
            op.id, ids, ItemStatus.DONE, stats={"done": deleted, "gone": len(ids) - deleted}
        )

    # ---- edit and undo (phase 20.3) -----------------------------------------------------------

    async def _live_texts(self, op: Op, guard: FloodGuard, ids: Sequence[int]) -> dict[int, str]:
        """The text of each of ``ids`` as the chat holds it now (a gone id is missing)."""
        found: dict[int, str] = {}
        for start in range(0, len(ids), MAX_IDS_PER_CALL):
            chunk = list(ids[start : start + MAX_IDS_PER_CALL])
            rewrites = await guard.read(
                "rewrite_messages",
                lambda chunk=chunk: self._gateway.rewrite_messages(
                    op.chat_id, chunk, CaptionPolicy()
                ),
            )
            found.update((r.id, r.before) for r in rewrites)
        return found

    async def _settle_edits(self, op: Op, guard: FloodGuard) -> Op:
        """What a process that died mid-edit left ``pending``: read the texts. A message that now
        reads the planned text was edited (``done``); one that still reads the old text was not
        (back to ``planned``); anything else was changed by somebody else meanwhile (left alone)."""
        pending = await self._store.op_items_in(op.id, ItemStatus.PENDING)
        if not pending:
            return op
        live = await self._live_texts(op, guard, [i.msg_id for i in pending])
        done: list[int] = []
        again: list[int] = []
        changed: list[int] = []
        gone: list[int] = []
        for item in pending:
            before, after = item.texts
            now = live.get(item.msg_id)
            if now is None:
                gone.append(item.msg_id)
            elif now == after:
                done.append(item.msg_id)
            elif now == before:
                again.append(item.msg_id)
            else:
                changed.append(item.msg_id)
        if done:
            op = await self._store.mark_op_items(
                op.id, done, ItemStatus.DONE, stats={"done": len(done)}
            )
        if again:
            op = await self._store.mark_op_items(op.id, again, ItemStatus.PLANNED)
        if changed:
            op = await self._store.mark_op_items(
                op.id,
                changed,
                ItemStatus.SKIPPED,
                reason=SKIP_CHANGED,
                stats={"skipped": len(changed)},
            )
        if gone:
            op = await self._store.mark_op_items(
                op.id, gone, ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": len(gone)}
            )
        self._reporter.notice("settled", deleted=len(done), again=len(again))
        return op

    async def _edit_batch(self, op: Op, guard: FloodGuard, batch: Sequence[Sequence[OpItem]]) -> Op:
        """Read the batch's messages again and edit each one that still reads what the plan saw.
        One that reads something else was changed since (left alone), one that already reads the
        planned text needs nothing. Each edit is written ahead as ``pending``."""
        items = [item for unit in batch for item in unit]
        live = await self._live_texts(op, guard, [i.msg_id for i in items])
        for item in items:
            before, after = item.texts
            now = live.get(item.msg_id)
            if now is None:
                op = await self._store.mark_op_items(
                    op.id, [item.msg_id], ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": 1}
                )
            elif now == after:  # already reads that: an earlier go got this far
                op = await self._store.mark_op_items(
                    op.id, [item.msg_id], ItemStatus.DONE, stats={"done": 1}
                )
            elif now != before:
                op = await self._store.mark_op_items(
                    op.id,
                    [item.msg_id],
                    ItemStatus.SKIPPED,
                    reason=SKIP_CHANGED,
                    stats={"skipped": 1},
                )
            else:
                op = await self._edit(op, guard, item.msg_id, after)
        self._reporter.progress(op)
        return op

    async def _edit(self, op: Op, guard: FloodGuard, msg_id: int, text: str) -> Op:
        await guard.pace(1, action=EDIT)  # may rest, or raise BudgetReached: nothing marked
        op = await self._store.mark_op_items(op.id, [msg_id], ItemStatus.PENDING)  # write-ahead
        try:
            await guard.write(
                "edit_message", 1, lambda: self._gateway.edit_message(op.chat_id, msg_id, text)
            )
        except PerMessage as exc:  # this one message cannot be edited; the others go on
            return await self._store.mark_op_items(
                op.id, [msg_id], ItemStatus.FAILED, reason=str(exc)[:120], stats={"failed": 1}
            )
        except (FloodWait, PeerFlood, Interrupted, NoPermission):
            await self._store.mark_op_items(op.id, [msg_id], ItemStatus.PLANNED)
            raise
        return await self._store.mark_op_items(op.id, [msg_id], ItemStatus.DONE, stats={"done": 1})

    # ---- repost (phase 20.4) ------------------------------------------------------------------

    async def _repost_settle(self, op: Op, guard: FloodGuard, reader: MessageReader) -> Op:
        """What a process that died mid-repost left: first finish the originals of copies that
        exist (``reposted``), then judge the one unit that was ``pending`` by reading the chat's
        tail since the id noted before it was sent (the way a run's reconcile does)."""
        op = await self._repost_finish(op, guard, reader)
        pending = await self._store.op_items_in(op.id, ItemStatus.PENDING)
        if not pending:
            return op
        by_tail: dict[int, list[OpItem]] = {}
        for item in pending:
            by_tail.setdefault(_noted(item).get("tail", 0), []).append(item)
        settled = {"copied": 0, "again": 0, "unsure": 0}
        for tail, items in sorted(by_tail.items()):
            ids = [i.msg_id for i in items]
            live = {m.id: m for m in await reader.get_messages(op.chat_id, ids)}
            if gone := [i for i in ids if i not in live]:
                op = await self._store.mark_op_items(
                    op.id, gone, ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": len(gone)}
                )
            originals = [live[i] for i in ids if i in live]
            if not originals:
                continue
            news = [
                m async for m in reader.iter_messages(op.chat_id, min_id=tail) if not m.is_service
            ]
            outcome, new_ids = judge(originals, news)
            kept = [m.id for m in originals]
            if outcome is Outcome.CONFIRMED:
                done = ItemStatus.REPOSTED if op.delete_original else ItemStatus.DONE
                stats = {} if op.delete_original else {"done": len(kept)}
                op = await self._store.mark_op_items(
                    op.id, kept, done, stats=stats, new_ids=dict(zip(kept, new_ids, strict=True))
                )
                settled["copied"] += len(kept)
            elif outcome is Outcome.RESEND:
                op = await self._store.mark_op_items(op.id, kept, ItemStatus.PLANNED)
                settled["again"] += len(kept)
            else:  # something is there that does not match: do not post again, do not delete
                op = await self._store.mark_op_items(
                    op.id,
                    kept,
                    ItemStatus.SKIPPED,
                    reason=SKIP_AMBIGUOUS,
                    stats={"skipped": len(kept)},
                )
                settled["unsure"] += len(kept)
        self._reporter.notice("settled_repost", **settled)
        return await self._repost_finish(op, guard, reader)

    async def _repost_finish(self, op: Op, guard: FloodGuard, reader: MessageReader) -> Op:
        """Copies exist for the ``reposted`` items: delete their originals (an original that is
        gone already is done), or, for a repost that keeps them, just close them."""
        reposted = await self._store.op_items_in(op.id, ItemStatus.REPOSTED)
        if not reposted:
            return op
        ids = [i.msg_id for i in reposted]
        if not op.delete_original:
            return await self._store.mark_op_items(
                op.id, ids, ItemStatus.DONE, stats={"done": len(ids)}
            )
        present = {m.id for m in await reader.get_messages(op.chat_id, ids)}
        if absent := [i for i in ids if i not in present]:
            op = await self._store.mark_op_items(
                op.id, absent, ItemStatus.DONE, stats={"done": len(absent)}
            )
        if present:
            op = await self._repost_delete(op, guard, sorted(present))
        return op

    async def _repost_delete(self, op: Op, guard: FloodGuard, ids: list[int]) -> Op:
        for start in range(0, len(ids), MAX_IDS_PER_CALL):
            chunk = ids[start : start + MAX_IDS_PER_CALL]
            await guard.pace(len(chunk), action=DELETE)
            await guard.write(
                "delete_messages",
                len(chunk),
                lambda chunk=chunk, chat=op.chat_id: self._gateway.delete_messages(chat, chunk),
            )
            op = await self._store.mark_op_items(
                op.id, chunk, ItemStatus.DONE, stats={"done": len(chunk)}
            )
        return op

    async def _repost_batch(
        self,
        op: Op,
        guard: FloodGuard,
        reader: MessageReader,
        matcher: Matcher | None,
        batch: Sequence[Sequence[OpItem]],
    ) -> Op:
        """Post again each unit of the batch, one after the other (a unit is downloaded, written
        ahead as ``pending`` with the chat's tail, uploaded, and recorded with its new ids), then
        delete the originals of the whole batch in one call if the repost is to delete them."""
        ids = [item.msg_id for unit in batch for item in unit]
        live = {m.id: m for m in await reader.get_messages(op.chat_id, ids)}
        reposted: list[int] = []
        for unit in batch:
            members = [live[i.msg_id] for i in unit if i.msg_id in live]
            if gone := [i.msg_id for i in unit if i.msg_id not in live]:
                op = await self._store.mark_op_items(
                    op.id, gone, ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": len(gone)}
                )
            if not members:
                continue
            if not self._still_selected(members, matcher):
                op = await self._store.mark_op_items(
                    op.id,
                    [m.id for m in members],
                    ItemStatus.SKIPPED,
                    reason=SKIP_CHANGED,
                    stats={"skipped": len(members)},
                )
                continue
            op, done = await self._repost_unit(op, guard, reader, Unit(tuple(members)), unit)
            reposted.extend(done)
        if reposted and op.delete_original:
            op = await self._repost_delete(op, guard, reposted)
        self._reporter.progress(op)
        return op

    async def _repost_unit(
        self,
        op: Op,
        guard: FloodGuard,
        reader: MessageReader,
        unit: Unit,
        items: Sequence[OpItem],
    ) -> tuple[Op, list[int]]:
        ids = list(unit.ids)
        action = reupload.plan_unit(unit, _REPOST_OPTIONS)
        if action.kind is not reupload.ActionKind.SEND:
            return (
                await self._store.mark_op_items(
                    op.id,
                    ids,
                    ItemStatus.SKIPPED,
                    reason=action.reason or SKIP_UNSUPPORTED,
                    stats={"unsupported": len(ids)},
                ),
                [],
            )
        topic = _noted(items[0]).get("topic")
        topic = None if topic in (None, 1) else int(topic)
        assert self._tmp_dir is not None, "a repost downloads: OpRunner needs a tmp_dir"
        try:
            prepared = await reader.prepare(op.chat_id, unit, self._tmp_dir, self._on_transfer)
        except PerMessage:  # gone since it was read: nothing to post again
            return (
                await self._store.mark_op_items(
                    op.id, ids, ItemStatus.SKIPPED, reason=SKIP_GONE, stats={"gone": len(ids)}
                ),
                [],
            )
        try:
            tail = await guard.read(
                "last_message_id", lambda: self._gateway.last_message_id(op.chat_id)
            )
            await guard.pace(len(ids), action=SEND)  # may rest, or raise BudgetReached
            note = json.dumps({"topic": topic, "tail": tail})
            op = await self._store.mark_op_items(
                op.id,
                ids,
                ItemStatus.PENDING,
                before_json=note,  # write-ahead
            )
            try:
                sent = await guard.write(
                    "send_prepared",
                    len(ids),
                    lambda: self._gateway.send_prepared(
                        op.chat_id,
                        prepared,
                        CaptionPolicy(),
                        self._on_transfer,
                        topic=topic,
                        silent=op.silent,
                    ),
                )
            except PerMessage as exc:  # Telegram refused this unit: the others go on
                return (
                    await self._store.mark_op_items(
                        op.id,
                        ids,
                        ItemStatus.FAILED,
                        reason=str(exc)[:120],
                        stats={"failed": len(ids)},
                    ),
                    [],
                )
            except (FloodWait, PeerFlood, Interrupted, NoPermission, ForwardsRestricted):
                await self._store.mark_op_items(op.id, ids, ItemStatus.PLANNED)
                raise
        finally:
            for path in prepared.files:
                path.unlink(missing_ok=True)
        # recorded at once: nothing between the send and this line may be interrupted, or the copy
        # would exist while the item still says ``pending``
        status = ItemStatus.REPOSTED if op.delete_original else ItemStatus.DONE
        op = await self._store.mark_op_items(
            op.id,
            ids,
            status,
            stats={"done": len(ids)} if status is ItemStatus.DONE else {},
            new_ids=dict(zip(ids, sent, strict=True)),
        )
        if same := await self._same_file_ids(op, guard, reader, unit, sent):
            op = await self._store.mark_op_items(op.id, [], status, stats={"same_file": same})
            self._reporter.notice("same_file", count=same)
        return op, ids if status is ItemStatus.REPOSTED else []

    async def _same_file_ids(
        self, op: Op, guard: FloodGuard, reader: MessageReader, unit: Unit, sent: list[int]
    ) -> int:
        """How many of the new messages carry the very same file id as their original: a repost
        exists to give the files new ids, so Telegram reusing one (the content is the same) is
        said, not hidden."""
        wanted = {m.id: m.file_key for m in unit.messages if m.file_key is not None}
        if not wanted:
            return 0
        fresh = {m.id: m.file_key for m in await reader.get_messages(op.chat_id, sent)}
        return sum(
            1
            for old, new in zip(unit.ids, sent, strict=True)
            if old in wanted and fresh.get(new) == wanted[old]
        )

    def _on_transfer(self, phase: TransferPhase, msg_id: int, done: int, total: int) -> None:
        if self._tracker is not None:
            self._tracker.update(phase, msg_id, done, total)

    # ---- control (mirrors engine.backup.BackupWriter) ----------------------------------------

    async def _requested(self, op_id: int) -> Control | None:
        if self._control.stop_requested:
            return Control.STOP
        if self._control.take_resume():
            await self._store.set_op_control(op_id, Control.NONE)
        control = await self._store.read_op_control(op_id)
        if control is Control.STOP:
            return Control.STOP
        if self._control.pause_requested or control is Control.PAUSE:
            return Control.PAUSE
        return None

    async def _gate(self, op: Op) -> bool:
        ctl = await self._requested(op.id)
        if ctl is None:
            return True
        if ctl is Control.STOP:
            return False
        return await self._hold(op)

    async def _hold(self, op: Op) -> bool:
        await self._store.set_op_status(op.id, OpStatus.PAUSED)
        self._reporter.notice("paused")
        while True:
            await self._sleep(self._timing.poll_interval)
            ctl = await self._requested(op.id)
            if ctl is Control.STOP:
                return False
            if ctl is None:
                await self._store.set_op_status(op.id, OpStatus.RUNNING)
                self._reporter.notice("resumed")
                return True

    async def _nap(self, seconds: float, op_id: int) -> None:
        remaining = seconds
        while remaining > 0:
            step = min(remaining, self._timing.poll_interval)
            await self._sleep(step)
            remaining -= step
            if await self._requested(op_id) is Control.STOP:
                raise Interrupted

    async def _heartbeat(self, op_id: int) -> None:
        while True:
            await asyncio.sleep(self._timing.heartbeat_interval)
            await self._store.heartbeat_op(op_id)
