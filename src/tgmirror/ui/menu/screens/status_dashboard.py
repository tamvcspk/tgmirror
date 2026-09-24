""" "Trạng thái": the same data ``tgmirror status`` shows, refreshed on its own every tick since it
only reads the store (no Telegram call), like the CLI command."""

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.keys import MenuKey
from tgmirror.engine.status import StatusReport, build_report
from tgmirror.store.db import utc_now
from tgmirror.store.runs import RunStatus
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.messages import t
from tgmirror.ui.progress import duration


class StatusDashboardScreen(Screen):
    tick_interval = 1.5

    def __init__(self, app: AppContext) -> None:
        self._app = app
        self._lines: list[str] = [t("status.none_live")]
        self.footer_hint = t("menu.footer_back")

    async def on_enter(self) -> None:
        await self._refresh()

    async def tick(self) -> ScreenResult | None:
        await self._refresh()
        return None

    async def _refresh(self) -> None:
        store = self._app.store
        live = await store.active_run()
        target = live or await store.latest_run()
        if target is None:
            self._lines = [t("err.run_none")]
            return
        report = await build_report(
            store, target, now=utc_now(), daily_cap=self._app.rt.config().limits.daily_cap
        )
        self._lines = _lines(report)

    def render(self) -> RenderableType:
        return Group(*(Text(line) for line in self._lines))

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        return "pop"


def _lines(report: StatusReport) -> list[str]:
    run_, est = report.run, report.estimate
    lines: list[str] = []
    if not report.live:
        lines.append(t("status.none_live"))
    lines.append(t("history.header", id=run_.id, src=run_.src_title, dst=run_.dst_title))
    note = f" ({run_.fail_reason})" if run_.fail_reason else ""
    lines.append(t("history.line_status", status=t(f"status.{run_.status}"), note=note))
    if report.abandoned:
        lines.append(
            t("status.abandoned", at=run_.updated_at.astimezone().strftime("%Y-%m-%d %H:%M"))
        )
    if run_.status is RunStatus.WAITING_FLOOD and run_.resume_at is not None:
        at = run_.resume_at.astimezone().strftime("%Y-%m-%d %H:%M")
        lines.append(t("status.line_resume", at=at, note=""))
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
            t("status.line_cap", left=report.left, cap=report.daily_cap, days=report.cap_days)
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
    return lines
