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
from tgmirror.engine.ops import resolve_op
from tgmirror.engine.runs import resolve_run
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store
from tgmirror.store.ops import ItemStatus, Op
from tgmirror.store.runs import Run
from tgmirror.ui.lines import flood_where, op_counts_line
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
            if number is not None and number.strip().upper().startswith("O"):
                await _detail_op(store, await resolve_op(store, number), as_json)
            elif number is not None:
                await _detail(store, await resolve_run(store, number), as_json)
            else:
                await _listing(store, limit, as_json)

    run(rt, command())


def _when(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value is not None else ""


def _status(item: Run | Backup | Op) -> str:
    return t(f"status.{item.status}")


def _record(item: Run | Backup | Op) -> dict[str, object]:
    if isinstance(item, Op):
        return {
            "kind": item.kind.value,
            "operation": item.id,
            "chat": {"id": item.chat_id, "title": item.chat_title},
            "status": item.status.value,
            "note": item.fail_reason,
            "started_at": item.created_at.isoformat(),
            "ended_at": item.ended_at.isoformat() if item.ended_at else None,
            "planned": item.planned,
            "done": item.done,
            "left_alone": item.skipped,
            "gone": item.gone,
            "failed": item.failed,
            "undo_of": item.undo_of,
            "filter": json.loads(item.filters_json),
            "backup": {"directory": item.backup_dir, "finished": item.backup_done}
            if item.backup_dir is not None
            else None,
        }
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


def _started(item: Run | Backup | Op) -> datetime:
    return item.created_at if isinstance(item, Op) else item.started_at


async def _recent(store: Store, limit: int) -> list[Run | Backup | Op]:
    """The newest runs, backups *and operations* together, newest first (T1, Phase 15b; phase 20):
    independent id sequences/tables, so merged and sorted here rather than by one SQL query."""
    runs = await store.list_runs(limit)
    backups = await store.list_backups(limit)
    ops = await store.list_ops(limit)
    merged: list[Run | Backup | Op] = sorted([*runs, *backups, *ops], key=_started, reverse=True)
    return merged[:limit]


def _pair_text(item: Run | Backup | Op) -> Text:
    # Text, not an f-string: titles/paths may contain "[brackets]", which Rich would read as markup
    if isinstance(item, Op):
        return Text(f"{item.chat_title} · {t(f'history.op_kind_{item.kind}')}")
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
        letter = "O" if isinstance(item, Op) else ("B" if is_backup else "R")
        failed = "" if is_backup else (f"{item.failed:,}" if item.failed else "")
        left_out = item.skipped if isinstance(item, Op) else item.skipped_filter
        table.add_row(
            f"{letter}{item.id}",
            _when(_started(item)),
            _pair_text(item),
            _status(item),
            f"{item.done:,}",
            failed,
            f"{left_out:,}" if left_out else "",
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
            {
                "at": e.ts.isoformat(),
                "kind": e.kind,
                "seconds": e.seconds,
                "method": e.method,
                "action": e.action,
                "destination": e.dst,
                "sent_1h": e.sent_1h,
                "sent_24h": e.sent_24h,
            }
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
                    where=flood_where(e),
                )
            )


FAILED_SHOWN = 10  # failed messages named in the detail of an operation


async def _detail_op(store: Store, item: Op, as_json: bool) -> None:
    floods = await store.op_flood_events(item.id)
    if as_json:
        record = _record(item)
        record["flood_events"] = [
            {
                "at": e.ts.isoformat(),
                "kind": e.kind,
                "seconds": e.seconds,
                "method": e.method,
                "action": e.action,
                "destination": e.dst,
                "sent_1h": e.sent_1h,
                "sent_24h": e.sent_24h,
            }
            for e in floods
        ]
        typer.echo(json.dumps(record, ensure_ascii=False, indent=2))
        return
    say = typer.echo
    kind = t(f"history.op_kind_{item.kind}")
    say(t("history.op_header", id=item.id, kind=kind, chat=item.chat_title))
    note = f" ({item.fail_reason})" if item.fail_reason else ""
    say(t("history.line_status", status=_status(item), note=note))
    ended = _when(item.ended_at) or t("history.still_running")
    say(t("history.line_time", started=_when(item.created_at), ended=ended))
    say(op_counts_line(item))
    if item.undo_of is not None:
        say(t("history.line_undo_of", id=item.undo_of))
    if item.topics_dropped or item.topics_kept:
        say(t("history.line_topics", dropped=item.topics_dropped, kept=item.topics_kept))
    if item.sync_src is not None:
        say(t("history.line_sync", src=item.sync_src, run=item.sync_run or "?"))
    if item.failed:
        failed = await store.op_items_in(item.id, ItemStatus.FAILED)
        say(t("history.failed_items", count=len(failed)))
        for entry in failed[:FAILED_SHOWN]:
            say(t("history.failed_item", id=entry.msg_id, reason=entry.reason or "?"))
    filter_text = t("history.no_filter") if item.filters_json == "{}" else item.filters_json
    say(t("history.line_filter", filter=filter_text))
    if item.backup_dir is not None:
        unfinished = "" if item.backup_done else t("history.line_backup_unfinished")
        say(t("history.line_backup", dir=item.backup_dir, note=unfinished))
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
                    where=flood_where(e),
                )
            )
