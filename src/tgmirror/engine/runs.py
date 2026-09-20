"""Starting and finding runs. Flags and the wizard both end in ``begin_run``.

The parity rule (skill ``cli-wizard``): whatever the wizard collects is expressible with flags, and
both end in the same ``begin_run`` call with the same arguments.

A run is one execution of a clone (the log). What makes the next run of the same pair a delta is
the mirror the store keeps for it; nothing here or in the CLI ever names or lists it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import ChannelInfo, TelegramGateway
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.runs import Run, RunOptions, RunSpec, RunStatus, StartedRun

SUPPORTED_MODES = ("auto", "copy")  # "reupload" arrives with strategy B (phase 6)
PEER_FLOOD_COOLDOWN = timedelta(hours=24)  # docs/05-chong-flood.md: rest at least 24h
DAILY_CAP = "daily_cap"  # ``fail_reason`` of a run parked in waiting_flood by the daily cap


class RunError(TgMirrorError):
    """Base class for problems with the run the user named or is starting."""


class RunNotFound(RunError):
    def __init__(self, ref: str | None) -> None:
        super().__init__("there is no run yet" if ref is None else f"no run matches {ref!r}")
        self.ref = ref  # ``None``: the history is empty


class ModeUnsupported(RunError):
    def __init__(self, mode: str) -> None:
        super().__init__(f"mode {mode!r} is not available")
        self.mode = mode


class RunWaiting(RunError):
    """The clone must not run before ``until`` (Telegram asked to wait, or a PEER_FLOOD rest)."""

    def __init__(self, until: datetime, reason: str) -> None:
        super().__init__(f"the clone must wait until {until.isoformat()} ({reason})")
        self.until = until
        self.reason = reason  # "flood" | "daily_cap" | "peer_flood"


@dataclass(frozen=True, slots=True)
class RunRequest:
    """What the user asked for; the same whether it came from flags, the wizard or ``run [n]``."""

    mode: str = "auto"
    batch_size: int = 20
    pushdown: bool = True
    # Canonical filter JSON; ``None`` keeps the filter of an earlier run of the pair.
    filters_json: str | None = None
    force: bool = False  # ``--force-takeover``
    fresh: bool = False  # ``--fresh``: forget the pair's progress and copy everything again
    retry_of: int | None = None  # ``retry``: send the ``failed`` messages of this run again


async def begin_run(
    store: Store,
    gateway: TelegramGateway,
    src: ChannelInfo,
    dst: ChannelInfo,
    request: RunRequest | None = None,
    *,
    clock: Clock = utc_now,
) -> StartedRun:
    """Validate and start a run of the pair; a pair seen before continues from its cursor.

    Like the destination read below, the source read is setup that happens before any copying
    (hard rule 1's exception), one cheap request per run.
    """
    request = request or RunRequest()
    if request.mode not in SUPPORTED_MODES:
        raise ModeUnsupported(request.mode)
    previous = await store.latest_run(src.id, dst.id)
    if previous is not None:
        check_runnable(previous, clock())
    # The destination's newest message only matters for a pair that is new, or starts fresh:
    # it is recorded once, so reconcile never scans what the destination held before.
    known = await store.find_mirror(src.id, dst.id) is not None
    base = 0 if known and not request.fresh else await gateway.last_message_id(dst.id)
    # A retry is measured by how many failed messages are left, so it needs no source total.
    head = 0 if request.retry_of is not None else await gateway.last_message_id(src.id)
    spec = RunSpec(
        src=src,
        dst=dst,
        mode=request.mode,
        options=RunOptions(request.batch_size, base, request.pushdown, head, request.retry_of),
        filters_json=request.filters_json,
    )
    return await store.start_run(spec, force=request.force, fresh=request.fresh)


async def resolve_run(store: Store, ref: str | None) -> Run:
    """A run by its number, or the latest one when ``ref`` is ``None``."""
    if ref is None:
        if (latest := await store.latest_run()) is None:
            raise RunNotFound(None)
        return latest
    ref = ref.strip()
    if ref.isdecimal() and (run := await store.get_run(int(ref))) is not None:
        return run
    raise RunNotFound(ref)


def check_runnable(last: Run, now: datetime) -> None:
    """Refuse a run that Telegram already told us would be rejected. Raises ``RunWaiting``."""
    if (
        last.status is RunStatus.WAITING_FLOOD
        and last.resume_at is not None
        and last.resume_at > now
    ):
        reason = "daily_cap" if last.fail_reason == DAILY_CAP else "flood"
        raise RunWaiting(last.resume_at, reason)
    if last.status is RunStatus.FAILED and last.fail_reason == "peer_flood":
        until = (last.ended_at or last.updated_at) + PEER_FLOOD_COOLDOWN
        if until > now:
            raise RunWaiting(until, "peer_flood")
