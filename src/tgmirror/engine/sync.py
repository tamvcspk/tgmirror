"""``tgmirror sync-changes``: carry what happened to the source of a pair over to its copies
(docs/06-lo-trinh.md Phase 17, decision D10).

A clone copies a message once. Afterwards the source can be edited or have a message removed, and
the copy stays as it was. This looks at every message the pair copied (``msg_map``), reads its
source again **by id**, and freezes two plans in the destination, which is the chat that changes:

* an **edit** for each copy whose text is no longer what its source says: the source was edited
  (``edit_date``) and the text the pair's own transform makes of it differs from the copy's. The
  plan is an ordinary ``edit`` operation (``before``/``after`` per message), so applying it re-reads
  every copy, leaves one somebody else changed alone, and ``undo`` puts the texts back;
* a **delete** for each copy whose source message is gone. An ordinary ``delete`` operation, plus
  the source's id on every item: the apply reads the source once more before each batch and keeps a
  copy whose source turns up after all (the plan's word that it is gone is never enough, as it is
  only an empty answer).

Nothing here touches Telegram's state. The metadata index is not used: it is a rebuildable
convenience and never the basis of a write; the reads here are live, through ``FloodGuard`` like an
``inspect`` scan (hard rule 1). What cannot be carried over: a changed media file, reactions, a
changed order; and a pair that appends a topic hashtag to its copies (``topic_as_hashtag``) has
copies whose text no transform here can reproduce, so its edits are not looked at.
"""

import asyncio
import json
from collections import deque
from dataclasses import dataclass, replace

from tgmirror.core.config import Limits
from tgmirror.core.gateway import (
    MAX_IDS_PER_CALL,
    CaptionPolicy,
    ChannelInfo,
    LinkRewrite,
    MessageReader,
    Rewrite,
    TelegramGateway,
)
from tgmirror.core.limiter import Sleep
from tgmirror.engine import reupload
from tgmirror.engine.flood import FloodGuard, inspect_guard
from tgmirror.engine.ops import (
    SAMPLE_EDGE,
    EditSample,
    NotAdmin,
    NullReporter,
    Reporter,
    Sample,
    _sample,
    _squeeze,
)
from tgmirror.store.db import Store
from tgmirror.store.msgmap import CopyRow
from tgmirror.store.ops import (
    SYNC_SRC,
    TRANSFORM,
    Op,
    OpKind,
    OpSpec,
    PlannedItem,
)
from tgmirror.store.runs import Run

EDITS_OFF_TOPIC_HASHTAG = "topic_hashtag"  # why a pair's edits are not looked at

MIRROR = "mirror"  # keys noted on the plans' ``options_json``
RUN = "run"


@dataclass(frozen=True, slots=True)
class SyncSummary:
    """What was found, for the person to judge before confirming."""

    src_title: str
    copies: int  # messages of the destination the pair put there
    edits: int  # copies whose text no longer matches their source
    in_sync: int  # edited at the source, but the copy already reads what it should
    lossy: int  # edited, but formatting an edit could not write back exactly: never touched
    copy_missing: int  # edited at the source, and the copy is gone from the destination
    deletes: int  # copies whose source message is gone
    already_gone: int  # source gone, and the copy too: nothing to do
    edit_first: tuple[EditSample, ...]
    edit_last: tuple[EditSample, ...]
    delete_first: tuple[Sample, ...]
    delete_last: tuple[Sample, ...]
    edits_off: str | None = None  # a reason edits were not looked at (``EDITS_OFF_*``)

    @property
    def total(self) -> int:
        return self.edits + self.deletes

    @property
    def delete_share(self) -> float:
        """How much of what the pair copied the plan would delete: very high usually means the
        account no longer sees the source's history, not that it was all deleted."""
        return self.deletes / self.copies if self.copies else 0.0


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """``edit``/``delete`` are ``None`` when there is nothing of that kind (no row is made)."""

    edit: Op | None
    delete: Op | None
    summary: SyncSummary

    @property
    def ops(self) -> list[Op]:
        return [op for op in (self.edit, self.delete) if op is not None]


def policy_of_pair(run: Run, links: LinkRewrite | None = None) -> CaptionPolicy:
    """What the pair did to a caption while copying, as a policy to apply to a source text now."""
    return replace(reupload.Options.of(run.options).caption, links=links)


async def plan_sync(
    store: Store,
    gateway: TelegramGateway,
    run: Run,
    limits: Limits,
    *,
    edits: bool = True,
    deletes: bool = True,
    links: LinkRewrite | None = None,
    notifier: Reporter | None = None,
    nap: Sleep = asyncio.sleep,
) -> SyncPlan:
    """Look at every copy of ``run``'s pair and freeze what to change in the destination.

    The account must administer the destination (``NotAdmin``) and be able to read the source (an
    unreachable source raises: "I cannot see it" must never be taken for "it was deleted")."""
    dst = await gateway.get_channel(run.dst_id)
    if not dst.is_admin:
        raise NotAdmin(dst)
    src = await gateway.get_channel(run.src_id)
    guard = await inspect_guard(store, limits, notifier=notifier or NullReporter(), nap=nap)
    reader = guard.reader(gateway)

    rows = await store.mirror_copy_rows(run.mirror_id)
    edits_off = EDITS_OFF_TOPIC_HASHTAG if run.options.topic_as_hashtag else None
    look_at_edits = edits and edits_off is None

    gone: list[CopyRow] = []
    edited: list[CopyRow] = []
    for start in range(0, len(rows), MAX_IDS_PER_CALL):
        chunk = rows[start : start + MAX_IDS_PER_CALL]
        present = {m.id: m for m in await reader.get_messages(src.id, [r.src_id for r in chunk])}
        for row in chunk:
            found = present.get(row.src_id)
            if found is None:
                gone.append(row)
            elif look_at_edits and row.done and found.edit_date is not None:
                edited.append(row)

    summary = _Counts()
    edit_items = (
        await _plan_edits(guard, gateway, src, dst, edited, policy_of_pair(run, links), summary)
        if look_at_edits
        else []
    )
    delete_items = await _plan_deletes(reader, src, dst, gone, summary) if deletes else []

    common = {MIRROR: run.mirror_id, RUN: run.id, SYNC_SRC: src.id}
    edit_op = delete_op = None
    if edit_items:
        options = json.dumps({TRANSFORM: run.options.transform_json, **common}, sort_keys=True)
        edit_op = await store.create_op(OpSpec(OpKind.EDIT, dst, "{}", options), edit_items)
    if delete_items:
        delete_op = await store.create_op(
            OpSpec(OpKind.DELETE, dst, "{}", json.dumps(common, sort_keys=True)), delete_items
        )
    return SyncPlan(edit_op, delete_op, summary.of(src, len(rows), edits_off))


class _Counts:
    """What the two passes tally, turned into a ``SyncSummary`` at the end."""

    def __init__(self) -> None:
        self.edits = self.in_sync = self.lossy = self.copy_missing = 0
        self.deletes = self.already_gone = 0
        self.edit_first: list[EditSample] = []
        self.edit_last: deque[EditSample] = deque(maxlen=SAMPLE_EDGE)
        self.delete_first: list[Sample] = []
        self.delete_last: deque[Sample] = deque(maxlen=SAMPLE_EDGE)

    def of(self, src: ChannelInfo, copies: int, edits_off: str | None) -> SyncSummary:
        return SyncSummary(
            src_title=src.title,
            copies=copies,
            edits=self.edits,
            in_sync=self.in_sync,
            lossy=self.lossy,
            copy_missing=self.copy_missing,
            deletes=self.deletes,
            already_gone=self.already_gone,
            edit_first=tuple(self.edit_first),
            edit_last=tuple(self.edit_last),
            delete_first=tuple(self.delete_first),
            delete_last=tuple(self.delete_last),
            edits_off=edits_off,
        )


async def _plan_edits(
    guard: FloodGuard,
    gateway: TelegramGateway,
    src: ChannelInfo,
    dst: ChannelInfo,
    edited: list[CopyRow],
    policy: CaptionPolicy,
    counts: _Counts,
) -> list[PlannedItem]:
    """For copies whose source was edited: what the source says now, as the pair would have copied
    it, against what the copy reads. Both texts come through the same markup (``rewrite_messages``),
    so equal text is equal and a difference is real."""
    items: list[PlannedItem] = []
    for start in range(0, len(edited), MAX_IDS_PER_CALL):
        chunk = edited[start : start + MAX_IDS_PER_CALL]
        src_ids = [r.src_id for r in chunk]
        dst_ids = [r.dst_id for r in chunk]
        wanted: dict[int, Rewrite] = {
            r.id: r
            for r in await guard.read(
                "rewrite_messages",
                lambda ids=src_ids: gateway.rewrite_messages(src.id, ids, policy),
            )
        }
        current: dict[int, Rewrite] = {
            r.id: r
            for r in await guard.read(
                "rewrite_messages",
                lambda ids=dst_ids: gateway.rewrite_messages(dst.id, ids, CaptionPolicy()),
            )
        }
        for row in chunk:
            want, have = wanted.get(row.src_id), current.get(row.dst_id)
            if want is None:  # went away between the two reads: the delete half will see it
                continue
            if have is None:
                counts.copy_missing += 1
            elif not (want.lossless and have.lossless):
                counts.lossy += 1
            elif want.after == have.before:
                counts.in_sync += 1
            else:
                items.append(
                    PlannedItem(
                        row.dst_id,
                        row.dst_id,
                        None,
                        json.dumps({"before": have.before, "after": want.after}),
                    )
                )
                counts.edits += 1
                sample = EditSample(row.dst_id, _squeeze(have.before), _squeeze(want.after))
                if len(counts.edit_first) < SAMPLE_EDGE:
                    counts.edit_first.append(sample)
                else:
                    counts.edit_last.append(sample)
    items.sort(key=lambda i: i.unit_id)
    return items


async def _plan_deletes(
    reader: MessageReader,
    src: ChannelInfo,
    dst: ChannelInfo,
    gone: list[CopyRow],
    counts: _Counts,
) -> list[PlannedItem]:
    """The copies of source messages that are gone and still exist in the destination (a copy
    already removed by hand needs nothing). The copies of one album go as one unit."""
    items: list[PlannedItem] = []
    heads: dict[int, int] = {}  # source album id -> its first copy: the unit
    for start in range(0, len(gone), MAX_IDS_PER_CALL):
        chunk = gone[start : start + MAX_IDS_PER_CALL]
        present = {m.id: m for m in await reader.get_messages(dst.id, [r.dst_id for r in chunk])}
        for row in chunk:
            message = present.get(row.dst_id)
            if message is None:
                counts.already_gone += 1
                continue
            unit = row.dst_id
            if row.grouped_id is not None:
                unit = heads.setdefault(row.grouped_id, row.dst_id)
            items.append(
                PlannedItem(row.dst_id, unit, row.grouped_id, json.dumps({"src": row.src_id}))
            )
            counts.deletes += 1
            sample = _sample(message)
            if len(counts.delete_first) < SAMPLE_EDGE:
                counts.delete_first.append(sample)
            else:
                counts.delete_last.append(sample)
    items.sort(key=lambda i: (i.unit_id, i.msg_id))
    return items
