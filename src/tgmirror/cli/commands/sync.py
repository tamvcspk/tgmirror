"""``tgmirror sync-changes``: carry what happened to the source of a pair over to its copies — a
message edited at the source is edited in the destination, one deleted at the source is deleted
there (docs/06-lo-trinh.md Phase 17, decision D10).

It is ``edit`` and ``delete`` with one plan for both (``engine.sync.plan_sync``): the source of the
pair is read again by id, and what differs is frozen as an ``edit`` operation and a ``delete``
operation in the destination (each its own ``O<n>``, so ``history``, ``--resume`` and ``undo``
work on them as they do everywhere). Like both, nothing changes without a confirmation that names
how many: at a terminal the total is typed back, without one ``--yes --expect N`` must come with the
plan's total, and without either the plans stay saved and nothing is touched. The edits go first;
the deletes only start once they finished, and the optional safety-net backup of the copies about to
go (``--backup-to``) sits between the two.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli import wizard
from tgmirror.cli.commands.delete import DeleteFlow, DeleteOptions, back_up, execute
from tgmirror.cli.commands.edit import mirror_target
from tgmirror.cli.errors import Declined, UsageProblem, run
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.engine.runs import resolve_run
from tgmirror.engine.sync import SyncPlan, plan_sync
from tgmirror.engine.transform import Transform
from tgmirror.store.db import Store
from tgmirror.store.ops import OpStatus
from tgmirror.store.runs import Run
from tgmirror.ui.lines import sync_plan_lines
from tgmirror.ui.messages import t


@dataclass(frozen=True, slots=True)
class SyncRequest:
    """What was asked: which of the two kinds of change to look for."""

    edits: bool = True
    deletes: bool = True


class SyncFlow(DeleteFlow):
    """``DeleteFlow``'s questions for a plan that is two operations: one confirmation (the total
    typed back), one question about the safety-net backup (of the copies that would be deleted)."""

    _kind = "sync"

    async def planned_sync(self, target: Run, request: SyncRequest) -> SyncPlan | None:
        """Plan the pair's changes and show them. ``None``: nothing differs."""
        transform = Transform.from_json(target.options.transform_json)
        mirror = await mirror_target(
            self._store, self._gateway, self._channels, str(target.id), transform
        )
        plan = await plan_sync(
            self._store,
            self._gateway,
            target,
            self._rt.config().limits,
            edits=request.edits,
            deletes=request.deletes,
            links=mirror.links,
        )
        if not plan.ops:
            self._echo(t("sync.nothing", dst=mirror.dst.title, **_left(plan)))
            return None
        for line in sync_plan_lines(target, plan):
            self._echo(line)
        return plan

    async def confirmed_sync(self, plan: SyncPlan) -> bool:
        """The confirmation of the whole plan, as ``DeleteFlow.confirmed`` gives one operation's."""
        o = self._o
        total = plan.summary.total
        ids = ", ".join(f"O{op.id}" for op in plan.ops)
        if o.expect is not None and o.expect != total:
            await self._cancel(plan)
            raise UsageProblem("sync.expect_mismatch", count=total, expected=o.expect)
        if o.yes:
            if o.expect is None:
                await self._cancel(plan)
                raise UsageProblem("sync.needs_expect", count=total)
            return True
        if not self._interactive:
            if o.backup_to is not None and plan.delete is not None:
                resolved = o.backup_to.expanduser().resolve()
                await self._store.set_op_options(plan.delete.id, backup_dir=str(resolved))
            raise UsageProblem(
                "sync.needs_yes", ids=ids, count=total, commands=_resume_commands(plan)
            )
        question = t(
            "sync.type_count",
            edits=plan.summary.edits,
            deletes=plan.summary.deletes,
            chat=plan.ops[0].chat_title,
            total=total,
        )
        if (await self._prompter.text(question)).strip() != str(total):
            await self._cancel(plan)
            self._echo(t("sync.cancelled", ids=ids))
            raise Declined
        return True

    async def _cancel(self, plan: SyncPlan) -> None:
        for op in plan.ops:
            await self._store.cancel_op(op.id)


def _left(plan: SyncPlan) -> dict[str, int]:
    s = plan.summary
    return {
        "copies": s.copies,
        "in_sync": s.in_sync,
        "lossy": s.lossy,
        "missing": s.copy_missing,
        "already": s.already_gone,
    }


def _resume_commands(plan: SyncPlan) -> str:
    parts = []
    if plan.edit is not None:
        parts.append(f"tgmirror edit --resume O{plan.edit.id} --yes --expect {plan.edit.planned}")
    if plan.delete is not None:
        parts.append(
            f"tgmirror delete --resume O{plan.delete.id} --yes --expect {plan.delete.planned}"
        )
    return "; ".join(parts)


async def _pick_run(rt: Runtime, store: Store, ref: str | None) -> Run:
    """The run whose pair is synced: the one named (``R12`` or ``12``), else — at a terminal with
    more than one pair in history — the person's choice, else the latest run."""
    if ref is None and rt.interactive:
        pairs = await store.list_pairs(wizard.PAIR_CHOICES)
        if len(pairs) > 1:
            return await wizard.pick_run(rt.prompter, pairs)
    return await resolve_run(store, None if ref is None else ref.strip().lstrip("Rr"))


def sync_changes(
    ctx: typer.Context,
    ref: Annotated[
        str | None,
        typer.Argument(
            metavar="[R<N>]",
            help="A run of the pair to sync (see `tgmirror history`; default: the latest run).",
        ),
    ] = None,
    edits: Annotated[
        bool,
        typer.Option(
            "--edits/--no-edits",
            help="Edit the copies of messages that were edited at the source (default).",
        ),
    ] = True,
    deletes: Annotated[
        bool,
        typer.Option(
            "--deletes/--no-deletes",
            help="Delete the copies of messages that were deleted at the source (default). "
            "That can be the only copy left of them.",
        ),
    ] = True,
    expect: Annotated[
        int | None,
        typer.Option(
            "--expect",
            min=0,
            help="With --yes: the number of changes the plan must hold (edits plus deletes). A "
            "different number changes nothing.",
        ),
    ] = None,
    backup_to: Annotated[
        Path | None,
        typer.Option(
            "--backup-to",
            help="Back the copies that would be deleted up into this directory before deleting "
            "anything (a backup that does not finish stops the deletes).",
        ),
    ] = None,
    no_backup: Annotated[
        bool,
        typer.Option(
            "--no-backup", help="Delete without a backup, and do not ask whether to make one."
        ),
    ] = False,
    admin_ack: Annotated[
        bool,
        typer.Option(
            "--yes-i-administer-this-channel",
            help="For the backup of a destination that restricts saving content: say that you own "
            "it and may copy it. You take full responsibility for that: tgmirror cannot check it.",
        ),
    ] = False,
    force_takeover: Annotated[
        bool,
        typer.Option(
            "--force-takeover",
            help="Carry on even if another process seems to hold the destination (only if dead).",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm without typing the count; needs --expect N."),
    ] = False,
) -> None:
    """Make the copies of a pair follow their source: edits and deletions made there since.

    The source is read again, message by message, and compared with what the pair copied. A
    message edited at the source gets its copy's text changed to what the pair would copy now
    (its transform applied); a message deleted at the source has its copy deleted. It plans first
    (O<n> for the edits, another for the deletes), shows both, and you type the total back to
    confirm; without a terminal give --yes --expect N. An edit can be undone (`tgmirror undo`); a
    delete cannot, and the copy may be the only one left, so it can be backed up first
    (--backup-to). A copy somebody else changed since the plan, or whose source turns up again, is
    left alone.

    Not carried over: changed media, reactions, order. A pair that adds a topic hashtag to its
    copies does not get their edits synced.

    Example: tgmirror sync-changes R12 --backup-to ~/mirror-safety
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if not edits and not deletes:
            raise UsageProblem("sync.nothing_asked")
        if backup_to is not None and no_backup:
            raise UsageProblem("err.conflicting_flags", flags="--backup-to / --no-backup")
        options = DeleteOptions(
            expect=expect,
            backup_to=backup_to,
            no_backup=no_backup,
            admin_ack=admin_ack,
            force_takeover=force_takeover,
            yes=yes,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                target = await _pick_run(rt, store, ref)
                flow = SyncFlow(rt, store, conn.gateway, channels, options)
                plan = await flow.planned_sync(target, SyncRequest(edits, deletes))
                if plan is None or not await flow.confirmed_sync(plan):
                    return
                if (
                    plan.delete is not None
                    and (backup := await flow.backup_plan(plan.delete)) is not None
                ):
                    await back_up(
                        rt, store, conn.gateway, plan.delete, backup, force_takeover=force_takeover
                    )
                if plan.edit is not None:
                    await execute(rt, store, conn.gateway, plan.edit, force_takeover=force_takeover)
                    final = await store.get_op(plan.edit.id)
                    if final is not None and final.status is not OpStatus.DONE:
                        typer.echo(
                            t(
                                "sync.edits_unfinished",
                                id=final.id,
                                status=t(f"status.{final.status}"),
                                commands=_resume_commands(plan),
                            )
                        )
                        raise typer.Exit(1)
                if plan.delete is not None:
                    await execute(
                        rt, store, conn.gateway, plan.delete, force_takeover=force_takeover
                    )

    run(rt, command())
