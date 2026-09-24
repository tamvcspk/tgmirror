""" "Lịch sử": the runs ``tgmirror history`` lists, and one of them in detail — native
``SelectList`` + detail render (not the CLI's ``Table``/``typer.echo``, so it fits the same
frame)."""

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.keys import MenuKey
from tgmirror.store.runs import FailedMessage, FloodEvent, Run
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.widgets import SelectList
from tgmirror.ui.messages import t

LIMIT = 20
DETAIL_FAILURES = 20


class HistoryScreen(Screen):
    def __init__(self, app: AppContext) -> None:
        self._app = app
        self._list: SelectList[Run] = SelectList(items=[])
        self.footer_hint = t("menu.footer_pick")

    async def on_enter(self) -> None:
        runs = await self._app.store.list_runs(LIMIT)
        self._list = SelectList(
            items=[
                (
                    f"{r.id:>4}  {r.started_at.astimezone().strftime('%Y-%m-%d %H:%M')}  "
                    f"{r.src_title} → {r.dst_title}  {t(f'status.{r.status}')}  "
                    f"{t('menu.history_counts', done=r.done, failed=r.failed)}",
                    r,
                )
                for r in runs
            ]
        )

    def render(self) -> RenderableType:
        if not self._list.items:
            return Text(t("history.empty"))
        return self._list.render()

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if key == MenuKey.ESC:
            return "pop"
        chosen = self._list.handle_key(key)
        if chosen is None:
            return "stay"
        return ("push", HistoryDetailScreen(self._app, chosen))


class HistoryDetailScreen(Screen):
    def __init__(self, app: AppContext, item: Run) -> None:
        self._app = app
        self._item = item
        self._failures: list[FailedMessage] = []
        self._floods: list[FloodEvent] = []
        self.footer_hint = t("menu.footer_back")

    async def on_enter(self) -> None:
        self._failures = await self._app.store.run_failures(self._item.id, DETAIL_FAILURES + 1)
        self._floods = await self._app.store.flood_events(self._item.id)

    def render(self) -> RenderableType:
        item = self._item
        lines = [t("history.header", id=item.id, src=item.src_title, dst=item.dst_title)]
        note = f" ({item.fail_reason})" if item.fail_reason else ""
        lines.append(t("history.line_status", status=t(f"status.{item.status}"), note=note))
        ended = (
            item.ended_at.astimezone().strftime("%Y-%m-%d %H:%M")
            if item.ended_at
            else t("history.still_running")
        )
        started = item.started_at.astimezone().strftime("%Y-%m-%d %H:%M")
        lines.append(t("history.line_time", started=started, ended=ended))
        lines.append(
            t(
                "history.line_counts",
                done=item.done,
                failed=item.failed,
                skipped=item.skipped_filter,
            )
        )
        if item.gone:
            lines.append(t("history.line_gone", count=item.gone))
        if item.options.retry_of is not None:
            lines.append(t("history.line_retry", of=item.options.retry_of))
        else:
            lines.append(t("history.line_cursor", start=item.cursor_from, end=item.cursor_src_id))
            filter_text = t("history.no_filter") if item.filters_json == "{}" else item.filters_json
            lines.append(t("history.line_filter", filter=filter_text))
        if self._failures:
            lines.append(t("history.failed_title", count=min(len(self._failures), DETAIL_FAILURES)))
            for f in self._failures[:DETAIL_FAILURES]:
                lines.append(t("history.failed_line", id=f.src_msg_id, reason=f.reason))
            if len(self._failures) > DETAIL_FAILURES:
                lines.append(t("history.failed_more"))
        if self._floods:
            lines.append(t("history.floods_title"))
            for e in self._floods:
                lines.append(
                    t(
                        "history.flood_line",
                        ts=e.ts.astimezone().strftime("%Y-%m-%d %H:%M"),
                        kind=e.kind,
                        seconds=e.seconds if e.seconds is not None else "-",
                        method=e.method or "",
                    )
                )
        return Group(*(Text(line) for line in lines))

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        return "pop"
