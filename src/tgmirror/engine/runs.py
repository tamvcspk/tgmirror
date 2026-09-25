"""Starting and finding runs. Flags and the wizard both end in ``begin_run``.

The parity rule (skill ``cli-wizard``): whatever the wizard collects is expressible with flags, and
both end in the same ``begin_run`` call with the same arguments.

A run is one execution of a clone (the log). What makes the next run of the same pair a delta is
the mirror the store keeps for it; nothing here or in the CLI ever names or lists it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import CaptionMode, ChannelInfo, TelegramGateway
from tgmirror.engine.endpoints import SourceRestricted
from tgmirror.engine.strategy import MODES, may_reupload
from tgmirror.store.db import Clock, Store, utc_now
from tgmirror.store.runs import Run, RunOptions, RunSpec, RunStatus, StartedRun

SUPPORTED_MODES = MODES  # auto | copy | reupload
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


class InvalidOptions(RunError):
    """Options that do not go together (``key`` names the message that says why)."""

    def __init__(self, key: str) -> None:
        super().__init__(f"invalid options: {key}")
        self.key = key


class NeedsAcknowledgement(RunError):
    """The source restricts saving content and this run has no confirmation from the user that they
    may copy it (decision D3): only ``clone`` can obtain one."""

    def __init__(self, title: str) -> None:
        super().__init__(f"{title!r} restricts saving content and the run was not confirmed")
        self.title = title


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
    # Strategy B (``--caption``, ``--caption-text``, ``--reset-polls``, ``--ignore-unsupported``,
    # ``--placeholder``); they only make sense for a run that can re-upload.
    caption: str = "keep"
    caption_text: str = ""
    reset_polls: bool = False
    ignore_unsupported: bool = False
    placeholder: bool = False
    protected_ack: bool = False  # the user confirmed a source that restricts saving content (D3)
    # Phase 8: keep a forum's topic as a hashtag when the destination cannot hold topics; a forward
    # cannot carry it, so ``--mode copy`` refuses it and ``auto`` sends such units again.
    topic_as_hashtag: bool = False


def check_options(request: RunRequest) -> None:
    """Refuse a request whose options contradict each other. Raises ``ModeUnsupported`` for an
    unknown mode and ``InvalidOptions`` otherwise."""
    if request.mode not in SUPPORTED_MODES:
        raise ModeUnsupported(request.mode)
    if request.caption not in {m.value for m in CaptionMode}:
        raise InvalidOptions("caption_unknown")
    if request.caption == CaptionMode.APPEND and not request.caption_text.strip():
        raise InvalidOptions("caption_text_missing")
    if request.caption != CaptionMode.APPEND and request.caption_text:
        raise InvalidOptions("caption_text_unused")
    if request.mode == "copy" and request.caption != CaptionMode.KEEP:
        raise InvalidOptions("caption_needs_reupload")
    strategy_b = request.reset_polls or request.ignore_unsupported or request.placeholder
    if strategy_b and request.mode != "reupload":
        raise InvalidOptions("reupload_flags_need_reupload")
    if request.topic_as_hashtag and request.mode == "copy":
        raise InvalidOptions("topic_hashtag_needs_rewrite")


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
    check_options(request)
    protected = False
    if may_reupload(request.mode, request.caption, request.topic_as_hashtag):
        protected = await check_source(gateway, src, request)
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
        options=RunOptions(
            batch_size=request.batch_size,
            dst_base_id=base,
            pushdown=request.pushdown,
            caption=request.caption,
            caption_text=request.caption_text,
            reset_polls=request.reset_polls,
            ignore_unsupported=request.ignore_unsupported,
            placeholder=request.placeholder,
            protected_ack=request.protected_ack,
            topic_as_hashtag=request.topic_as_hashtag,
            src_last_id=head,
            src_protected=protected,
            retry_of=request.retry_of,
        ),
        filters_json=request.filters_json,
    )
    return await store.start_run(spec, force=request.force, fresh=request.fresh)


async def check_source(gateway: TelegramGateway, src: ChannelInfo, request: RunRequest) -> bool:
    """Decision D3 for a run that can download content: read the source again (one setup request)
    because it may have turned on "Restrict saving content" since the pair was chosen, and ``run``
    and ``retry`` never go through ``plan_endpoints``.

    The user's own statement (``--yes-i-administer-this-channel``, recorded by ``clone`` in the
    run's options and carried on by ``run``/``retry``) is what lets a run go on. Without it a
    protected source is refused: ``SourceRestricted`` when this account does not administer it,
    ``NeedsAcknowledgement`` when it does (the prompt of ``clone`` is enough for those).

    Returns whether the source restricts saving content (a run the user vouched for goes on,
    but the long way: files are downloaded and uploaded, never sent by their id).
    """
    current = await gateway.get_channel(src.id)
    if not current.noforwards:
        return False
    if request.protected_ack:
        return True
    if not current.is_admin:
        raise SourceRestricted(current)
    raise NeedsAcknowledgement(current.title)


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
