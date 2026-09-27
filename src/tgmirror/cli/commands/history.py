"""``tgmirror history [n]``: what earlier runs (and backups, T1 Phase 15b) did. The log is all the
state the user sees.

No Telegram connection. Without a number: the newest runs *and backups*, newest first, one line
each, marked by kind. With one: that run in detail, including the messages it failed to copy (with
the reason) and the limits Telegram set — a backup has no number of its own to look up here (its
own directory is its whole record; ``tgmirror status`` shows one in progress).
"""

import json
from datetime import datetime
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.engine.runs import resolve_run
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store
from tgmirror.store.runs import Run
from tgmirror.ui.messages import t

DETAIL_FAILURES = 20  # failed messages listed in the detail view


def history(
    ctx: typer.Context,
    number: Annotated[
        str | None,
        typer.Argument(metavar="[RUN]", help="Show this run in detail (backups have no number)."),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", "-n", min=1, max=200, help="How many entries to list.")
    ] = 20,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show earlier clones and backups, newest first, or one run in detail.

    Example: tgmirror history        (or: tgmirror history 3 --json)
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with opened_store(rt) as store:
            if number is not None:
                await _detail(store, await resolve_run(store, number), as_json)
            else:
                await _listing(store, limit, as_json)

    run(rt, command())


def _when(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value is not None else ""


def _status(item: Run | Backup) -> str:
    return t(f"status.{item.status}")


def _record(item: Run | Backup) -> dict[str, object]:
    if isinstance(item, Backup):
        return {
            "kind": "backup",
            "backup": item.id,
            "source": {"id": item.src_id, "title": item.src_title},
            "directory": item.dir,
            "status": item.status.value,
            "note": item.fail_reason,
            "started_at": item.started_at.isoformat(),
            "ended_at": item.ended_at.isoformat() if item.ended_at else None,
            "copied": item.done,
            "left_out_by_filter": item.skipped_filter,
            "gone_from_source": item.gone,
            "filter": json.loads(item.filters_json),
        }
    return {
        "kind": "run",
        "run": item.id,
        "source": {"id": item.src_id, "title": item.src_title},
        "destination": {"id": item.dst_id, "title": item.dst_title},
        "status": item.status.value,
        "note": item.fail_reason,
        "started_at": item.started_at.isoformat(),
        "ended_at": item.ended_at.isoformat() if item.ended_at else None,
        "copied": item.done,
        "failed": item.failed,
        "left_out_by_filter": item.skipped_filter,
        "gone_from_source": item.gone,
        "retry_of": item.options.retry_of,
        "source_from": item.cursor_from,
        "source_to": item.cursor_src_id,
        "filter": json.loads(item.filters_json),
    }


async def _recent(store: Store, limit: int) -> list[Run | Backup]:
    """The newest runs *and* backups together, newest first (T1, Phase 15b): two independent id
    sequences/tables, so merged and sorted here rather than by one SQL query."""
    runs = await store.list_runs(limit)
    backups = await store.list_backups(limit)
    merged: list[Run | Backup] = sorted(
        [*runs, *backups], key=lambda item: item.started_at, reverse=True
    )
    return merged[:limit]


def _pair_text(item: Run | Backup) -> Text:
    # Text, not an f-string: titles/paths may contain "[brackets]", which Rich would read as markup
    dst = item.dir if isinstance(item, Backup) else item.dst_title
    return Text(f"{item.src_title} → {dst}")


async def _listing(store: Store, limit: int, as_json: bool) -> None:
    items = await _recent(store, limit)
    if as_json:
        typer.echo(json.dumps([_record(r) for r in items], ensure_ascii=False, indent=2))
        return
    if not items:
        typer.echo(t("history.empty"))
        return
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, title=t("history.title"))
    # a bare id would be ambiguous (runs and backups number their rows independently) and a
    # separate "kind" column has no width left to spare at 80 columns (found by a real test
    # failure: the fold column wrapped its own header) — one extra letter on the id instead
    table.add_column(t("history.col_run"), justify="right")
    table.add_column(t("history.col_started"), no_wrap=True)
    table.add_column(t("history.col_pair"), overflow="fold")
    table.add_column(t("history.col_status"))
    table.add_column(t("history.col_copied"), justify="right")
    table.add_column(t("history.col_failed"), justify="right")
    table.add_column(t("history.col_filtered"), justify="right")
    for item in items:
        is_backup = isinstance(item, Backup)
        failed = "" if is_backup else (f"{item.failed:,}" if item.failed else "")
        table.add_row(
            f"{'B' if is_backup else 'R'}{item.id}",
            _when(item.started_at),
            _pair_text(item),
            _status(item),
            f"{item.done:,}",
            failed,
            f"{item.skipped_filter:,}" if item.skipped_filter else "",
        )
    Console().print(table)


async def _detail(store: Store, item: Run, as_json: bool) -> None:
    failures = await store.run_failures(item.id, DETAIL_FAILURES + 1)
    floods = await store.flood_events(item.id)
    if as_json:
        record = _record(item)
        record["failed_messages"] = [
            {"source_message": f.src_msg_id, "reason": f.reason} for f in failures
        ]
        record["flood_events"] = [
            {"at": e.ts.isoformat(), "kind": e.kind, "seconds": e.seconds, "method": e.method}
            for e in floods
        ]
        typer.echo(json.dumps(record, ensure_ascii=False, indent=2))
        return
    say = typer.echo
    say(t("history.header", id=item.id, src=item.src_title, dst=item.dst_title))
    note = f" ({item.fail_reason})" if item.fail_reason else ""
    say(t("history.line_status", status=_status(item), note=note))
    ended = _when(item.ended_at) or t("history.still_running")
    say(t("history.line_time", started=_when(item.started_at), ended=ended))
    say(
        t(
            "history.line_counts",
            done=item.done,
            failed=item.failed,
            skipped=item.skipped_filter,
        )
    )
    if item.gone:
        say(t("history.line_gone", count=item.gone))
    if item.options.retry_of is not None:  # a retry reads by id: no source range, no filter
        say(t("history.line_retry", of=item.options.retry_of))
    else:
        say(t("history.line_cursor", start=item.cursor_from, end=item.cursor_src_id))
        filter_text = t("history.no_filter") if item.filters_json == "{}" else item.filters_json
        say(t("history.line_filter", filter=filter_text))
    if failures:
        say(t("history.failed_title", count=min(len(failures), DETAIL_FAILURES)))
        for f in failures[:DETAIL_FAILURES]:
            say(t("history.failed_line", id=f.src_msg_id, reason=f.reason))
        if len(failures) > DETAIL_FAILURES:
            say(t("history.failed_more"))
    if floods:
        say(t("history.floods_title"))
        for e in floods:
            say(
                t(
                    "history.flood_line",
                    ts=_when(e.ts),
                    kind=e.kind,
                    seconds=e.seconds if e.seconds is not None else "-",
                    method=e.method or "",
                )
            )
