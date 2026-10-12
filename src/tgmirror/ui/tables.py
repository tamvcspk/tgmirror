"""Rendering of channel lists: a Rich table for people, JSON for scripts."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timedelta

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from tgmirror.core.gateway import ChannelInfo, TopicInfo
from tgmirror.engine import inspect
from tgmirror.engine.inspect import Report as InspectReport
from tgmirror.engine.inspect import Row as InspectRow
from tgmirror.engine.preflight import Plan, PlanOptions
from tgmirror.ui.messages import t
from tgmirror.ui.progress import duration, size


def channel_label(info: ChannelInfo) -> str:
    """One-line description used in prompts and summaries."""
    label = f"[{t(f'kind.{info.kind}')}] {info.title}"
    if info.username:
        label += f" @{info.username}"
    return f"{label} ({info.id})"


def channels_json(channels: Sequence[ChannelInfo]) -> str:
    return json.dumps([asdict(c) for c in channels], ensure_ascii=False, indent=2)


def print_channels(console: Console, channels: Sequence[ChannelInfo]) -> None:
    if not channels:
        console.print(t("channels.empty"))
        return
    # Compact on purpose: it has to stay readable at 80 columns, and the username rides along
    # under the title instead of taking a column of its own.
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column(t("col.kind"))
    table.add_column(t("col.title"), overflow="fold")
    table.add_column(t("col.id"), no_wrap=True)
    table.add_column(t("col.members"), justify="right")
    table.add_column(t("col.noforwards"), justify="center")
    table.add_column(t("col.post"))
    for c in channels:
        # Text, not str: titles may contain [brackets] that Rich would read as markup
        title = Text(c.title)
        if c.username:
            title.append(f"\n@{c.username}", style="dim")
        table.add_row(
            t(f"kind.{c.kind}"),
            title,
            str(c.id),
            f"{c.participants:,}" if c.participants is not None else "?",
            t("yes") if c.noforwards else "",
            t("admin") if c.is_admin and c.can_post else (t("yes") if c.can_post else t("no")),
        )
    console.print(table)
    console.print(t("channels.count", count=len(channels)))


def topics_json(topics: Sequence[TopicInfo]) -> str:
    return json.dumps([asdict(t) for t in topics], ensure_ascii=False, indent=2)


def print_topics(console: Console, topics: Sequence[TopicInfo]) -> None:
    if not topics:
        console.print(t("topics.empty"))
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column(t("col.id"), no_wrap=True)
    table.add_column(t("col.title"), overflow="fold")
    table.add_column(t("col.closed"), justify="center")
    for topic in topics:
        table.add_row(str(topic.id), Text(topic.title), t("yes") if topic.closed else "")
    console.print(table)
    console.print(t("topics.count", count=len(topics)))


# ---- inspect (phase 18a) ----------------------------------------------------------------------


def _row_label(by: str, row: InspectRow) -> str:
    """A table row's words: the sentinel key says "none", a name rides along with its id."""
    if row.key == inspect.NONE:
        return t("inspect.none_forward" if by == "forward" else "inspect.none_sender")
    if row.name:
        return f"{row.name} ({row.key})"
    return row.key


def _date(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d") if moment else "?"


def print_report(console: Console, report: InspectReport, *, source: str, forum: bool) -> None:
    """The ``tgmirror inspect`` report: totals, then one table per ``--by``."""
    console.print(t("inspect.source", src=Text(source).plain))
    if report.scanned.messages == 0:
        console.print(t("inspect.empty"))
        return
    s = report.scanned
    console.print(
        t(
            "inspect.totals",
            messages=f"{s.messages:,}",
            units=f"{s.units:,}",
            albums=f"{s.albums:,}",
            size=size(s.bytes),
            first=_date(s.first),
            last=_date(s.last),
        )
    )
    if report.filtered:
        sel = report.selected
        console.print(
            t(
                "inspect.selected",
                messages=f"{sel.messages:,}",
                units=f"{sel.units:,}",
                albums=f"{sel.albums:,}",
                size=size(sel.bytes),
            )
        )
    if not report.complete:
        console.print(t("inspect.partial", last_id=report.last_id))
    for tab in report.tables:
        console.print()
        console.print(Text(t(f"inspect.by.{tab.by}"), style="bold"))
        if tab.by == "topic" and not forum:
            console.print(t("inspect.not_a_forum"))
            continue
        if not tab.rows:
            console.print(t("inspect.no_rows"))
            continue
        grid = Table(box=box.SIMPLE_HEAD, pad_edge=False)
        grid.add_column(t("inspect.col.value"), overflow="fold")
        grid.add_column(t("inspect.col.messages"), justify="right")
        grid.add_column(t("inspect.col.size"), justify="right")
        for row in tab.rows:
            grid.add_row(
                Text(_row_label(tab.by, row)),
                f"{row.messages:,}",
                size(row.bytes) if row.bytes else "",
            )
        if tab.other is not None:
            other = t("inspect.other", count=tab.other.key)
            grid.add_row(
                Text(other, style="dim"),
                f"{tab.other.messages:,}",
                size(tab.other.bytes) if tab.other.bytes else "",
            )
        console.print(grid)


def print_listing(console: Console, rows: Sequence[Mapping[str, object]], *, more: int = 0) -> None:
    """The matched messages (``--list``): the columns a person scans; ``--csv`` has them all."""
    console.print()
    console.print(Text(t("inspect.list_title"), style="bold"))
    grid = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    for column in ("id", "date", "media", "size", "text", "link"):
        grid.add_column(column, overflow="fold", no_wrap=column in ("id", "date"))
    for row in rows:
        grid.add_row(
            str(row["id"]),
            str(row["date"])[:16].replace("T", " "),
            str(row["media"]),
            size(row["size"]) if isinstance(row["size"], int) else "",  # type: ignore[arg-type]
            Text(str(row["text"] or "")[:60]),
            str(row["link"] or ""),
        )
    console.print(grid)
    if more:
        console.print(t("inspect.list_more", count=f"{more:,}"))


def print_plan(
    console: Console,
    plan: Plan,
    options: PlanOptions,
    *,
    cap: int,
    batch_size: int,
    protected: bool,
) -> None:
    """The pre-flight (``--plan``): how the selected units would travel and what that costs."""
    what = t(f"inspect.plan_kind.{plan.kind}", mode=options.mode, caption=options.caption.value)
    console.print()
    console.print(Text(t("inspect.plan_title", what=what), style="bold"))
    if plan.ways:
        grid = Table(box=box.SIMPLE_HEAD, pad_edge=False)
        grid.add_column(t("inspect.col.way"), overflow="fold")
        grid.add_column(t("inspect.col.units"), justify="right")
        grid.add_column(t("inspect.col.messages"), justify="right")
        grid.add_column(t("inspect.col.size"), justify="right")
        for way in plan.ways:
            grid.add_row(
                Text(t(f"inspect.way.{way.strategy}")),
                f"{way.units:,}",
                f"{way.messages:,}",
                size(way.bytes) if way.bytes else "",
            )
        console.print(grid)
    if plan.blocked:
        console.print(t("inspect.plan_blocked", what=_kinds(plan.blocked)))
    if plan.left_out:
        console.print(t("inspect.plan_left_out", what=_kinds(plan.left_out)))
    if plan.write_calls:
        console.print(
            t(
                "inspect.plan_writes",
                calls=f"{plan.write_calls:,}",
                batch=batch_size,
                time=duration(timedelta(seconds=plan.pacing_seconds)),
                days=max(plan.days, 1),
                cap=f"{cap:,}",
            )
        )
    else:
        console.print(t("inspect.plan_no_writes"))
    if plan.transfer_bytes:
        console.print(
            t(
                "inspect.plan_transfer",
                size=size(plan.transfer_bytes),
                largest=size(plan.largest_file),
            )
        )
    if protected and plan.transfer_bytes:
        console.print(t("inspect.plan_protected"))


def _kinds(counts: Sequence[tuple[str, int]]) -> str:
    return ", ".join(f"{kind} ({units})" for kind, units in counts)
