"""``tgmirror status``: how the running clone is doing (or how the latest run ended).

No Telegram connection: it reads the store, so it works from a second terminal while the clone
holds the session. Progress, speed and ETA are estimates (``engine/status.py`` says how).
"""

import json
from datetime import datetime
from typing import Annotated

import typer

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.engine.runs import RunNotFound
from tgmirror.engine.status import StatusReport, build_report
from tgmirror.store.db import utc_now
from tgmirror.store.runs import RunStatus
from tgmirror.ui.messages import t
from tgmirror.ui.progress import duration


def status(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show progress, speed, ETA, failures and Telegram's limits for the running clone.

    With nothing running, it shows the latest run instead. Works while a clone runs in another
    terminal. Progress and ETA are estimates, by source message id.

    Example: tgmirror status        (or: tgmirror status --json)
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with opened_store(rt) as store:
            live = await store.active_run()
            target = live or await store.latest_run()
            if target is None:
                raise RunNotFound(None)
            report = await build_report(
                store, target, now=utc_now(), daily_cap=rt.config().limits.daily_cap
            )
        typer.echo(
            json.dumps(_record(report), ensure_ascii=False, indent=2) if as_json else _text(report)
        )

    run(rt, command())


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
