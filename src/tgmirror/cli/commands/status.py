"""``tgmirror status``: how the running clone, backup or delete is doing (or how the latest one
ended).

No Telegram connection: it reads the store, so it works from a second terminal while the clone or
backup holds the session. Progress, speed and ETA are estimates (``engine/status.py`` says how).

T1, Phase 15b: a backup (``tgmirror backup``) never showed up here at all — only ``runs`` was ever
looked at, so a backup running in another terminal, or the newest thing in the log, was invisible.
A live one takes priority (one process per session, hard rule 10, means at most one of a run/backup
is ever live at once in practice); at rest, whichever is newer wins.

Phase 20.2: an operation on a chat's own messages (``tgmirror delete``, ``O<n>``) is the third
kind, with a fraction and an ETA of its own since the plan froze its total.
"""

import json
from datetime import datetime, timedelta
from typing import Annotated

import typer

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.engine.runs import RunNotFound
from tgmirror.engine.status import (
    BackupStatusReport,
    OpStatusReport,
    StatusReport,
    build_backup_report,
    build_op_report,
    build_report,
)
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store, utc_now
from tgmirror.store.ops import Op, OpStatus
from tgmirror.store.runs import Run, RunStatus
from tgmirror.ui.lines import budget_lines, flood_kind
from tgmirror.ui.messages import t
from tgmirror.ui.progress import duration


def status(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show progress, speed, ETA, failures and Telegram's limits for the running clone, backup or
    delete.

    With nothing running, it shows the latest one (run, backup or delete) instead. Works while one
    runs in another terminal. Progress and ETA are estimates, by source message id.

    Example: tgmirror status        (or: tgmirror status --json)
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with opened_store(rt) as store:
            target = await _pick(store)
            if target is None:
                raise RunNotFound(None)
            now = utc_now()
            limits = rt.config().limits
            daily_cap = limits.daily_cap
            bad_ttl = timedelta(hours=limits.probe_after_hours)
            if isinstance(target, Op):
                op_report = await build_op_report(store, target, now=now, daily_cap=daily_cap)
                out = (
                    json.dumps(_op_record(op_report), ensure_ascii=False, indent=2)
                    if as_json
                    else _op_text(op_report)
                )
            elif isinstance(target, Backup):
                backup_report = await build_backup_report(
                    store, target, now=now, daily_cap=daily_cap
                )
                out = (
                    json.dumps(_backup_record(backup_report), ensure_ascii=False, indent=2)
                    if as_json
                    else _backup_text(backup_report)
                )
            else:
                report = await build_report(
                    store, target, now=now, daily_cap=daily_cap, bad_ttl=bad_ttl
                )
                out = (
                    json.dumps(_record(report), ensure_ascii=False, indent=2)
                    if as_json
                    else _text(report)
                )
        typer.echo(out)

    run(rt, command())


async def _pick(store: Store) -> Run | Backup | Op | None:
    if (live_run := await store.active_run()) is not None:
        return live_run
    if (live_backup := await store.active_backup()) is not None:
        return live_backup
    if (live_op := await store.active_op()) is not None:
        return live_op
    latest: list[Run | Backup | Op] = [
        found
        for found in (
            await store.latest_run(),
            await store.latest_backup(),
            await store.latest_op(),
        )
        if found is not None
    ]
    return max(latest, key=_last_activity, default=None)


def _last_activity(item: Run | Backup | Op) -> datetime:
    """When it last did anything: a delete's safety-net backup starts after its plan was made, but
    the delete is what happened last."""
    return item.ended_at or item.updated_at


def _local(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _text(report: StatusReport) -> str:
    run_, est = report.run, report.estimate
    lines: list[str] = []
    if not report.live:
        lines.append(t("status.none_live"))
    lines.append(t("history.header", id=run_.id, src=run_.src_title, dst=run_.dst_title))
    note = f" ({run_.fail_reason})" if run_.fail_reason else ""
    lines.append(t("history.line_status", status=t(f"status.{run_.status}"), note=note))
    if report.abandoned:
        lines.append(t("status.abandoned", at=_local(run_.updated_at)))
    if run_.status is RunStatus.WAITING_FLOOD and run_.resume_at is not None:
        lines.append(t("status.line_resume", at=_local(run_.resume_at), note=""))

    if run_.options.retry_of is not None:
        lines.append(t("history.line_retry", of=run_.options.retry_of))
    if est.fraction is None:
        lines.append(t("status.line_progress_unknown", cursor=run_.cursor_src_id))
    elif run_.options.retry_of is not None:
        handled = run_.done + run_.failed + run_.gone
        lines.append(
            t(
                "status.line_progress_retry",
                percent=round(est.fraction * 100),
                handled=handled,
                total=handled + (report.retry_left or 0),
            )
        )
    elif run_.options.total_items > 0:
        lines.append(
            t(
                "status.line_progress_items",
                percent=round(est.fraction * 100),
                handled=run_.handled,
                total=max(run_.options.total_items, run_.handled),
            )
        )
    else:
        lines.append(
            t(
                "status.line_progress",
                percent=round(est.fraction * 100),
                cursor=run_.cursor_src_id,
                head=run_.options.src_last_id,
            )
        )
    if est.speed is None:
        lines.append(t("status.line_speed_unknown"))
    else:
        eta = t("status.eta", eta=duration(est.eta)) if est.eta is not None else ""
        lines.append(t("status.line_speed", speed=f"{est.speed:.1f}", eta=eta))
    lines.append(
        t("history.line_counts", done=run_.done, failed=run_.failed, skipped=run_.skipped_filter)
    )

    if report.delay is not None:
        lines.append(
            t(
                "status.line_limiter",
                delay=f"{report.delay:.1f}",
                sent=report.sent_24h,
                cap=report.daily_cap,
            )
        )
    lines.extend(budget_lines(report))
    if report.floods_24h == 0:
        lines.append(t("status.line_floods_none"))
    else:
        last = ""
        if report.last_flood is not None:
            last = t(
                "status.last_flood",
                ago=duration(report.now - report.last_flood.ts),
                kind=flood_kind(report.last_flood),
                seconds=report.last_flood.seconds if report.last_flood.seconds is not None else "-",
            )
        lines.append(t("status.line_floods", count=report.floods_24h, last=last))
    if not report.live and report.failed_now:
        lines.append(t("status.retry_hint", count=report.failed_now, id=run_.id))
    return "\n".join(lines)


def _record(report: StatusReport) -> dict[str, object]:
    run_, est = report.run, report.estimate
    flood = report.last_flood
    return {
        "kind": "run",
        "run": run_.id,
        "source": {"id": run_.src_id, "title": run_.src_title},
        "destination": {"id": run_.dst_id, "title": run_.dst_title},
        "status": run_.status.value,
        "live": report.live,
        "abandoned": report.abandoned,
        "note": run_.fail_reason,
        "resume_at": run_.resume_at.isoformat() if run_.resume_at else None,
        "retry_of": run_.options.retry_of,
        "progress": est.fraction,
        "speed_per_second": est.speed,
        "eta_seconds": est.eta.total_seconds() if est.eta is not None else None,
        "copied": run_.done,
        "failed": run_.failed,
        "left_out_by_filter": run_.skipped_filter,
        "gone_from_source": run_.gone,
        "failed_now": report.failed_now,
        "source_from": run_.cursor_from,
        "source_to": run_.cursor_src_id,
        "source_last": run_.options.src_last_id or None,
        "total_items": run_.options.total_items or None,
        "handled": run_.handled,
        "left": report.left,
        "cap_rest_hours": report.cap_rest_hours,
        "cap_rest_days": -(-report.cap_rest_hours // 24),
        "limiter": None
        if report.delay is None
        else {
            "delay_seconds": report.delay,
            "sent_24h": report.sent_24h,
            "daily_cap": report.daily_cap,
            "budgets": [
                {
                    "action": b.action,
                    "budget": b.amount,
                    "window_hours": b.window_hours,
                    "sent": b.sent,
                    "flood_seconds": b.flood_seconds,
                    "flood_at": b.flood_at.isoformat(),
                    "ok": b.ok,
                    "bad": b.bad,
                    "checked_at": b.checked_at.isoformat() if b.checked_at else None,
                }
                for b in report.budgets
            ],
        },
        "floods_24h": report.floods_24h,
        "last_flood": None
        if flood is None
        else {"at": flood.ts.isoformat(), "kind": flood.kind, "seconds": flood.seconds},
    }


def _backup_text(report: BackupStatusReport) -> str:
    """As ``_text``, for a backup (T1, Phase 15b) — no total/fraction/ETA (see
    ``BackupStatusReport``'s docstring)."""
    b = report.backup
    lines: list[str] = []
    if not report.live:
        lines.append(t("status.none_live"))
    lines.append(t("backup.start", id=b.id, src=b.src_title, dir=b.dir))
    note = f" ({b.fail_reason})" if b.fail_reason else ""
    lines.append(t("history.line_status", status=t(f"status.{b.status}"), note=note))
    if report.abandoned:
        lines.append(t("status.abandoned", at=_local(b.updated_at)))
    if b.status is RunStatus.WAITING_FLOOD and b.resume_at is not None:
        lines.append(t("status.line_resume", at=_local(b.resume_at), note=""))
    if report.speed is None:
        lines.append(t("status.line_speed_unknown"))
    else:
        lines.append(t("status.line_speed", speed=f"{report.speed:.1f}", eta=""))
    lines.append(t("history.line_counts_backup", done=b.done, skipped=b.skipped_filter))
    if report.delay is not None:
        lines.append(
            t(
                "status.line_limiter",
                delay=f"{report.delay:.1f}",
                sent=report.sent_24h,
                cap=report.daily_cap,
            )
        )
    if report.floods_24h == 0:
        lines.append(t("status.line_floods_none"))
    else:
        last = ""
        if report.last_flood is not None:
            last = t(
                "status.last_flood",
                ago=duration(report.now - report.last_flood.ts),
                kind=flood_kind(report.last_flood),
                seconds=report.last_flood.seconds if report.last_flood.seconds is not None else "-",
            )
        lines.append(t("status.line_floods", count=report.floods_24h, last=last))
    return "\n".join(lines)


def _op_text(report: OpStatusReport) -> str:
    """As ``_text``, for an operation (phase 20.2)."""
    op, est = report.op, report.estimate
    lines: list[str] = []
    if not report.live:
        lines.append(t("status.none_live"))
    kind = t(f"history.op_kind_{op.kind}")
    lines.append(t("history.op_header", id=op.id, kind=kind, chat=op.chat_title))
    note = f" ({op.fail_reason})" if op.fail_reason else ""
    lines.append(t("history.line_status", status=t(f"status.{op.status}"), note=note))
    if report.abandoned:
        lines.append(t("status.abandoned", at=_local(op.updated_at)))
    if op.status is OpStatus.WAITING_FLOOD and op.resume_at is not None:
        lines.append(t("status.line_resume", at=_local(op.resume_at), note=""))
    if est.fraction is not None:
        lines.append(
            t(
                "status.line_progress_items",
                percent=round(est.fraction * 100),
                handled=op.handled,
                total=max(op.planned, op.handled),
            )
        )
    if est.speed is None:
        lines.append(t("status.line_speed_unknown"))
    else:
        eta = t("status.eta", eta=duration(est.eta)) if est.eta is not None else ""
        lines.append(t("status.line_speed", speed=f"{est.speed:.1f}", eta=eta))
    lines.append(
        t(
            "history.line_counts_op",
            done=op.done,
            skipped=op.skipped,
            gone=op.gone,
            failed=op.failed,
            planned=op.planned,
        )
    )
    if op.backup_dir is not None:
        unfinished = "" if op.backup_done else t("history.line_backup_unfinished")
        lines.append(t("history.line_backup", dir=op.backup_dir, note=unfinished))
    if report.delay is not None:
        lines.append(
            t(
                "status.line_limiter",
                delay=f"{report.delay:.1f}",
                sent=report.sent_24h,
                cap=report.daily_cap,
            )
        )
    if report.floods_24h == 0:
        lines.append(t("status.line_floods_none"))
    else:
        last = ""
        if report.last_flood is not None:
            last = t(
                "status.last_flood",
                ago=duration(report.now - report.last_flood.ts),
                kind=flood_kind(report.last_flood),
                seconds=report.last_flood.seconds if report.last_flood.seconds is not None else "-",
            )
        lines.append(t("status.line_floods", count=report.floods_24h, last=last))
    if not report.live and op.status in (OpStatus.STOPPED, OpStatus.WAITING_FLOOD, OpStatus.FAILED):
        lines.append(t("delete.continue_hint", id=op.id))
    return "\n".join(lines)


def _op_record(report: OpStatusReport) -> dict[str, object]:
    op, est = report.op, report.estimate
    flood = report.last_flood
    return {
        "kind": op.kind.value,
        "operation": op.id,
        "chat": {"id": op.chat_id, "title": op.chat_title},
        "status": op.status.value,
        "live": report.live,
        "abandoned": report.abandoned,
        "note": op.fail_reason,
        "resume_at": op.resume_at.isoformat() if op.resume_at else None,
        "progress": est.fraction,
        "speed_per_second": est.speed,
        "eta_seconds": est.eta.total_seconds() if est.eta is not None else None,
        "planned": op.planned,
        "done": op.done,
        "left_alone": op.skipped,
        "gone": op.gone,
        "failed": op.failed,
        "left": report.left,
        "backup": {"directory": op.backup_dir, "finished": op.backup_done}
        if op.backup_dir is not None
        else None,
        "limiter": None
        if report.delay is None
        else {
            "delay_seconds": report.delay,
            "sent_24h": report.sent_24h,
            "daily_cap": report.daily_cap,
        },
        "floods_24h": report.floods_24h,
        "last_flood": None
        if flood is None
        else {"at": flood.ts.isoformat(), "kind": flood.kind, "seconds": flood.seconds},
    }


def _backup_record(report: BackupStatusReport) -> dict[str, object]:
    b = report.backup
    flood = report.last_flood
    return {
        "kind": "backup",
        "backup": b.id,
        "source": {"id": b.src_id, "title": b.src_title},
        "directory": b.dir,
        "status": b.status.value,
        "live": report.live,
        "abandoned": report.abandoned,
        "note": b.fail_reason,
        "resume_at": b.resume_at.isoformat() if b.resume_at else None,
        "speed_per_second": report.speed,
        "copied": b.done,
        "left_out_by_filter": b.skipped_filter,
        "gone_from_source": b.gone,
        "source_to": b.cursor_to,
        "handled": b.handled,
        "limiter": None
        if report.delay is None
        else {
            "delay_seconds": report.delay,
            "sent_24h": report.sent_24h,
            "daily_cap": report.daily_cap,
        },
        "floods_24h": report.floods_24h,
        "last_flood": None
        if flood is None
        else {"at": flood.ts.isoformat(), "kind": flood.kind, "seconds": flood.seconds},
    }
