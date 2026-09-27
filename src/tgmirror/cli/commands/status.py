"""``tgmirror status``: how the running clone or backup is doing (or how the latest one ended).

No Telegram connection: it reads the store, so it works from a second terminal while the clone or
backup holds the session. Progress, speed and ETA are estimates (``engine/status.py`` says how).

T1, Phase 15b: a backup (``tgmirror backup``) never showed up here at all — only ``runs`` was ever
looked at, so a backup running in another terminal, or the newest thing in the log, was invisible.
A live one takes priority (one process per session, hard rule 10, means at most one of a run/backup
is ever live at once in practice); at rest, whichever is newer wins.
"""

import json
from datetime import datetime
from typing import Annotated

import typer

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.engine.runs import RunNotFound
from tgmirror.engine.status import (
    BackupStatusReport,
    StatusReport,
    build_backup_report,
    build_report,
)
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store, utc_now
from tgmirror.store.runs import Run, RunStatus
from tgmirror.ui.messages import t
from tgmirror.ui.progress import duration


def status(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show progress, speed, ETA, failures and Telegram's limits for the running clone or backup.

    With nothing running, it shows the latest one (run or backup) instead. Works while a clone or
    backup runs in another terminal. Progress and ETA are estimates, by source message id.

    Example: tgmirror status        (or: tgmirror status --json)
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with opened_store(rt) as store:
            target = await _pick(store)
            if target is None:
                raise RunNotFound(None)
            now = utc_now()
            daily_cap = rt.config().limits.daily_cap
            if isinstance(target, Backup):
                backup_report = await build_backup_report(
                    store, target, now=now, daily_cap=daily_cap
                )
                out = (
                    json.dumps(_backup_record(backup_report), ensure_ascii=False, indent=2)
                    if as_json
                    else _backup_text(backup_report)
                )
            else:
                report = await build_report(store, target, now=now, daily_cap=daily_cap)
                out = (
                    json.dumps(_record(report), ensure_ascii=False, indent=2)
                    if as_json
                    else _text(report)
                )
        typer.echo(out)

    run(rt, command())


async def _pick(store: Store) -> Run | Backup | None:
    if (live_run := await store.active_run()) is not None:
        return live_run
    if (live_backup := await store.active_backup()) is not None:
        return live_backup
    latest_run = await store.latest_run()
    latest_backup = await store.latest_backup()
    if latest_run is None:
        return latest_backup
    if latest_backup is None:
        return latest_run
    return latest_run if latest_run.started_at >= latest_backup.started_at else latest_backup


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

    if report.cap_days > 0 and report.left:
        lines.append(
            t(
                "status.line_cap",
                left=report.left,
                cap=report.daily_cap,
                days=report.cap_days,
            )
        )
    if report.delay is not None:
        lines.append(
            t(
                "status.line_limiter",
                delay=f"{report.delay:.1f}",
                sent=report.sent_today,
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
                kind=report.last_flood.kind,
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
        "cap_rest_days": report.cap_days,
        "limiter": None
        if report.delay is None
        else {
            "delay_seconds": report.delay,
            "sent_today": report.sent_today,
            "daily_cap": report.daily_cap,
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
                sent=report.sent_today,
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
                kind=report.last_flood.kind,
                seconds=report.last_flood.seconds if report.last_flood.seconds is not None else "-",
            )
        lines.append(t("status.line_floods", count=report.floods_24h, last=last))
    return "\n".join(lines)


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
            "sent_today": report.sent_today,
            "daily_cap": report.daily_cap,
        },
        "floods_24h": report.floods_24h,
        "last_flood": None
        if flood is None
        else {"at": flood.ts.isoformat(), "kind": flood.kind, "seconds": flood.seconds},
    }
