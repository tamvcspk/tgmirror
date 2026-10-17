"""``tgmirror limits`` / ``tgmirror limits reset``: what the limiter learned about Telegram's limits
for each destination, and forgetting it (phase 19d4).

No Telegram connection: it reads and writes the store. A write lane that met a long FloodWait
keeps a budget (messages per window), where it was blocked (``bad``) and how far it ran clean
(``ok``); ``reset`` makes the lane "not learned" again, which the user asks for on purpose, so it
never asks back. ``tgmirror clone|run|retry --reset-limits`` does the same at the start of a run.
"""

import json
from datetime import timedelta
from typing import Annotated

import typer

from tgmirror.cli.errors import UsageProblem, run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.limiter import WRITE_ACTIONS
from tgmirror.engine.limits import ACCOUNT, LaneInfo, lane_infos
from tgmirror.store.db import Store, utc_now
from tgmirror.ui.messages import t

limits_app = typer.Typer(help="Show or forget the limits learned from Telegram's long waits.")


@limits_app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show, for each destination, what each write lane learned: its budget, where it was blocked
    (bad), how far it ran clean (ok) and when the limit was last put to the test.

    Example: tgmirror limits        (or: tgmirror limits --json)
    """
    if ctx.invoked_subcommand is not None:
        return
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with opened_store(rt) as store:
            lanes = await lane_infos(
                store,
                now=utc_now(),
                bad_ttl=timedelta(hours=rt.config().limits.probe_after_hours),
            )
        typer.echo(
            json.dumps([_record(lane) for lane in lanes], ensure_ascii=False, indent=2)
            if as_json
            else _text(lanes)
        )

    run(rt, command())


@limits_app.command("reset")
def reset(
    ctx: typer.Context,
    dst: Annotated[
        str | None,
        typer.Option(
            "--dst",
            help="Forget the limits of the lanes into this destination: its id or its title, "
            "as `tgmirror limits` shows it.",
        ),
    ] = None,
    all_destinations: Annotated[
        bool, typer.Option("--all", help="Forget the limits of every destination.")
    ] = False,
) -> None:
    """Forget what the write lanes learned about limits: each goes back to "not learned", so
    nothing stops it until its next long FloodWait. The pace it learned stays. Asks nothing.

    Example: tgmirror limits reset --dst -1001234567890        (or: --all)
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if dst is not None and all_destinations:
            raise UsageProblem("err.limits_reset_both")
        if dst is None and not all_destinations:
            raise UsageProblem("err.limits_reset_target")
        async with opened_store(rt) as store:
            # a live run keeps its limiter in memory and saves all of it back: it would undo this
            if (live := await store.active_run()) is not None:
                raise UsageProblem("err.limits_busy", id=live.id)
            if (live_backup := await store.active_backup()) is not None:
                raise UsageProblem("err.limits_busy", id=live_backup.id)
            target = None if dst is None else await _resolve(store, dst)
            count = await store.forget_budgets(ACCOUNT, target)
        where = t("limits.where_all") if target is None else t("limits.where_dst", dst=target)
        typer.echo(
            t("limits.reset_done" if count else "limits.reset_none", count=count, where=where)
        )

    run(rt, command())


async def _resolve(store: Store, ref: str) -> int:
    """A destination id, from the id itself or from the title of a destination that has lanes."""
    try:
        return int(ref)
    except ValueError:
        pass
    known: dict[int, str | None] = {
        lane.dst: lane.dst_title for lane in await lane_infos(store, now=utc_now())
    }
    wanted = ref.casefold()
    exact = [d for d, title in known.items() if title and title.casefold() == wanted]
    found = exact or [d for d, title in known.items() if title and wanted in title.casefold()]
    if not found:
        raise UsageProblem("err.limits_dst_unknown", ref=ref)
    if len(found) > 1:
        raise UsageProblem(
            "err.limits_dst_ambiguous",
            ref=ref,
            matches=", ".join(f"{d} ({known[d]})" for d in found),
        )
    return found[0]


def _when(value: object) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if hasattr(value, "astimezone") else "-"


def _text(lanes: list[LaneInfo]) -> str:
    if not lanes:
        return t("limits.none")
    out: list[str] = []
    current: int | None = None
    for lane in lanes:
        if lane.dst != current:
            current = lane.dst
            out.append(
                t("limits.dest", dst=lane.dst, title=lane.dst_title)
                if lane.dst_title
                else t("limits.dest_unknown", dst=lane.dst)
            )
        if lane.amount is None:
            out.append(
                t(
                    "limits.lane_unlearned",
                    action=lane.action,
                    sent=lane.sent,
                    delay=f"{lane.delay:.1f}",
                )
            )
            continue
        unknown = t("limits.unknown")
        out.append(
            t(
                "limits.lane_learned",
                action=lane.action,
                budget=lane.amount,
                window=lane.window_hours,
                sent=lane.sent,
                bad=lane.bad if lane.bad is not None else unknown,
                ok=lane.ok if lane.ok is not None else unknown,
                seconds=lane.flood_seconds,
                flood_at=_when(lane.flood_at),
                checked=_when(lane.checked_at),
                delay=f"{lane.delay:.1f}",
            )
        )
    return "\n".join(out)


def _record(lane: LaneInfo) -> dict[str, object]:
    assert lane.action in WRITE_ACTIONS
    return {
        "destination": {"id": lane.dst, "title": lane.dst_title},
        "action": lane.action,
        "delay_seconds": lane.delay,
        "learned": lane.amount is not None,
        "budget": lane.amount,
        "window_hours": lane.window_hours,
        "sent": lane.sent,
        "ok": lane.ok,
        "bad": lane.bad,
        "checked_at": lane.checked_at.isoformat() if lane.checked_at else None,
        "probe_fails": lane.probe_fails,
        "flood_at": lane.flood_at.isoformat() if lane.flood_at else None,
        "flood_seconds": lane.flood_seconds,
    }
