"""Pure functions building the start/result lines of a run or a backup (Phase 15b, L2,
docs/06-lo-trinh.md): the same wording either way it is shown, so the classic CLI
(``cli/commands/run.py``/``backup.py``'s ``execute``, printed one by one with ``typer.echo``) and
the full-screen menu (``ui/menu/run_screen.py``/``backup_screen.py``, appended to a screen's running
list of lines) never drift apart from writing the same thing twice.
"""

from tgmirror.engine.ops import PlanSummary, Sample
from tgmirror.engine.status import StatusReport, rest_hours
from tgmirror.store.backups import Backup
from tgmirror.store.ops import Op, OpStatus
from tgmirror.store.runs import FilterChange, FloodEvent, Run, RunStatus, StartedRun
from tgmirror.ui.messages import t


def run_start_lines(started: StartedRun, failed_count: int | None) -> list[str]:
    """Decision D3's "you take full responsibility" line is not part of this: the classic CLI
    sends it to stderr (a warning, not progress) while the menu has no separate stream to send it
    to, so each caller prints/appends it itself before these lines (``execute``/``RunScreen``)."""
    current = started.run
    lines: list[str] = []
    if (retry_of := current.options.retry_of) is not None:  # no source cursor/filter to talk about
        lines.append(
            t(
                "run.retry_start",
                id=current.id,
                of=retry_of,
                count=failed_count or 0,
                src=current.src_title,
                dst=current.dst_title,
            )
        )
        return lines
    lines.append(
        t(
            "run.start",
            id=current.id,
            src=current.src_title,
            dst=current.dst_title,
            cursor=current.cursor_from,
        )
    )
    if started.forgot is not None:
        lines.append(t("run.fresh_started", count=started.forgot))
    elif started.filters is FilterChange.CHANGED:
        lines.append(t("run.filter_changed"))
    if started.filters is FilterChange.SAME and current.filters_json != "{}":
        lines.append(t("run.filter_reused"))
    return lines


def run_result_lines(final: Run, retry_of: int | None) -> list[str]:
    lines = [
        t(
            "run.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            failed=final.failed,
        )
    ]
    if final.skipped_filter:
        lines.append(t("run.skipped", count=final.skipped_filter))
    if final.skipped_unsupported:
        lines.append(t("run.unsupported_total", count=final.skipped_unsupported, id=final.id))
    if final.reply_lost:
        lines.append(t("run.reply_lost", count=final.reply_lost))
    if final.links_left:
        lines.append(t("run.links_left", count=final.links_left))
    if final.gone:
        lines.append(t("retry.gone", count=final.gone))
    if final.failed:
        key = "retry.still_failing" if retry_of is not None else "run.retry_hint"
        lines.append(t(key, count=final.failed, id=final.id))
    if final.status is RunStatus.STOPPED:
        if retry_of is not None:  # `run` would start a delta: the messages left are retry's job
            lines.append(t("run.retry_continue_hint", of=retry_of))
        else:
            lines.append(t("run.continue_hint", id=final.id))
    return lines


def backup_start_line(backup_id: int, src: str, directory: str) -> str:
    """``src`` is whatever label the caller already has: the classic CLI shows the fuller
    ``channel_label(...)`` (it has the ``ChannelInfo`` on hand before ``begin_backup`` runs), the
    menu shows the plain ``Backup.src_title`` — this only shares the one wording, not the label."""
    return t("backup.start", id=backup_id, src=src, dir=directory)


def backup_result_lines(final: Backup) -> list[str]:
    lines = [
        t(
            "backup.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            cursor=final.cursor_to,
        )
    ]
    if final.skipped_filter:
        lines.append(t("run.skipped", count=final.skipped_filter))
    if final.gone:
        lines.append(t("retry.gone", count=final.gone))
    if final.status is RunStatus.STOPPED:
        lines.append(t("backup.continue_hint", dir=final.dir))
    return lines


def flood_kind(event: FloodEvent) -> str:
    """A flood's kind with the limiter lane it hit (phase 19d1): ``flood_wait/forward``."""
    return f"{event.kind}/{event.action}" if event.action else event.kind


def flood_where(event: FloodEvent) -> str:
    """What a flood was logged with, for ``history``: the destination and how much the lane had
    sent in the last hour and 24 hours. Empty for a row from before phase 19d1."""
    parts = []
    if event.action:
        parts.append(f"{event.action} -> {event.dst}" if event.dst is not None else event.action)
    if event.sent_1h is not None and event.sent_24h is not None:
        parts.append(f"{event.sent_1h}/1h {event.sent_24h}/24h")
    return f" [{', '.join(parts)}]" if parts else ""


def rest_phrase(hours: int) -> str:
    """``3 giờ``, ``2 ngày``: a rest in hours, in whole days only when it is exactly that."""
    if hours >= 24 and hours % 24 == 0:
        return t("rest.days", n=hours // 24)
    return t("rest.hours", n=hours)


def budget_lines(report: StatusReport) -> list[str]:
    """What ``status`` says about the budgets learned for the run's destination (phase 19d2): the
    rest they cost, then one line per lane with its window, what it sent and the wait that set
    it; or that nothing was learned yet (only pacing and FloodWait limit the run)."""
    lines: list[str] = []
    if report.cap_rest_hours > 0 and report.left:
        binding = max(
            report.budgets,
            key=lambda b: rest_hours(report.left or 0, b.amount, b.sent, b.window_hours),
        )
        lines.append(
            t(
                "status.line_cap",
                left=report.left,
                cap=binding.amount,
                window=binding.window_hours,
                rest=rest_phrase(report.cap_rest_hours),
            )
        )
    for b in report.budgets:
        lines.append(
            t(
                "status.line_budget",
                action=b.action,
                budget=b.amount,
                window=b.window_hours,
                sent=b.sent,
                seconds=b.flood_seconds,
                at=b.flood_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            )
        )
    for b in report.budgets:
        if b.bad is None and b.ok is None:
            continue
        lines.append(
            t(
                "status.line_bounds",
                action=b.action,
                bad=b.bad if b.bad is not None else t("limits.unknown"),
                ok=b.ok if b.ok is not None else t("limits.unknown"),
                at=b.checked_at.astimezone().strftime("%Y-%m-%d %H:%M")
                if b.checked_at is not None
                else t("limits.unknown"),
            )
        )
    if not report.budgets and report.delay is not None:
        lines.append(t("status.line_budget_none"))
    return lines


YEARLY_FROM = 13  # a plan spanning this many months or more is summed up by year


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {amount:,}" for name, amount in counts.items())


def delete_plan_lines(op: Op, summary: PlanSummary) -> list[str]:
    """The frozen plan of a ``delete`` (phase 20), for the person to judge before confirming."""
    lines = [
        t(
            "delete.plan_header",
            id=op.id,
            chat=op.chat_title,
            messages=summary.messages,
            units=summary.units,
            albums=summary.albums,
        ),
        t("delete.plan_media", items=_counts(summary.by_media)),
    ]
    if len(summary.by_month) >= YEARLY_FROM:
        years: dict[str, int] = {}
        for month, amount in summary.by_month.items():
            years[month[:4]] = years.get(month[:4], 0) + amount
        lines.append(t("delete.plan_years", items=_counts(years)))
    else:
        lines.append(t("delete.plan_months", items=_counts(summary.by_month)))

    def sample(item: Sample) -> str:
        return t(
            "delete.plan_sample",
            id=item.id,
            date=item.date.astimezone().strftime("%Y-%m-%d"),
            media=item.media,
            text=item.text,
        )

    lines.extend(sample(item) for item in summary.first)
    if summary.last:
        lines.append(t("delete.plan_gap"))
        lines.extend(sample(item) for item in summary.last)
    return lines


def delete_start_line(op: Op) -> str:
    return t("delete.start", id=op.id, count=op.planned, chat=op.chat_title)


def delete_result_lines(final: Op) -> list[str]:
    lines = [
        t(
            "delete.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            planned=final.planned,
        )
    ]
    if final.skipped:
        lines.append(t("delete.left_alone", count=final.skipped))
    if final.gone:
        lines.append(t("delete.gone", count=final.gone))
    if final.status in (OpStatus.STOPPED, OpStatus.WAITING_FLOOD, OpStatus.FAILED):
        lines.append(t("delete.continue_hint", id=final.id))
    return lines
