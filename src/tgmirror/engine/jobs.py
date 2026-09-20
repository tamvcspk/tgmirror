"""Creating, finding and vetting jobs. Flags and the wizard both go through ``create_job``.

The parity rule (skill ``cli-wizard``): whatever the wizard collects is expressible with flags, and
both end in the same ``create_job`` call with the same arguments.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import TelegramGateway
from tgmirror.engine.endpoints import Endpoints
from tgmirror.filters.model import FilterSpec
from tgmirror.store.db import Store
from tgmirror.store.jobs import Job, JobOptions, JobSpec, JobStatus

SUPPORTED_MODES = ("auto", "copy")  # "reupload" arrives with strategy B (phase 6)
PEER_FLOOD_COOLDOWN = timedelta(hours=24)  # docs/05-chong-flood.md: rest at least 24h
DAILY_CAP = "daily_cap"  # ``fail_reason`` of a job parked in waiting_flood by the daily cap


class JobError(TgMirrorError):
    """Base class for problems with the job the user named or is creating."""


class JobNotFound(JobError):
    def __init__(self, ref: str) -> None:
        super().__init__(f"no job matches {ref!r}")
        self.ref = ref


class AmbiguousJob(JobError):
    def __init__(self, ref: str, matches: Sequence[Job]) -> None:
        super().__init__(f"{ref!r} matches {len(matches)} jobs")
        self.ref = ref
        self.matches = tuple(matches)


class JobExists(JobError):
    """One job per source/destination pair: a second one would copy everything twice."""

    def __init__(self, job: Job) -> None:
        super().__init__(f"job {job.id} already copies this source into this destination")
        self.job = job


class ModeUnsupported(JobError):
    def __init__(self, mode: str) -> None:
        super().__init__(f"mode {mode!r} is not available")
        self.mode = mode


class JobWaiting(JobError):
    """The job must not run before ``until`` (Telegram asked to wait, or a PEER_FLOOD cool-down)."""

    def __init__(self, until: datetime, reason: str) -> None:
        super().__init__(f"job must wait until {until.isoformat()} ({reason})")
        self.until = until
        self.reason = reason  # "flood" | "daily_cap" | "peer_flood"


@dataclass(frozen=True, slots=True)
class NewJob:
    name: str | None = None  # default: "<source> → <destination>"
    mode: str = "auto"
    batch_size: int = 20
    filters: FilterSpec = field(default_factory=FilterSpec)  # default: clone everything
    pushdown: bool = True  # False: read the whole source instead of narrowing it server-side


async def create_job(
    store: Store, gateway: TelegramGateway, endpoints: Endpoints, new: NewJob | None = None
) -> Job:
    """Validate and save a job for the settled pair of endpoints."""
    new = new or NewJob()
    if new.mode not in SUPPORTED_MODES:
        raise ModeUnsupported(new.mode)
    src, dst = endpoints.src, endpoints.dst
    if (existing := await store.find_job_for_pair(src.id, dst.id)) is not None:
        raise JobExists(existing)
    spec = JobSpec(
        name=new.name or f"{src.title} → {dst.title}",
        src=src,
        dst=dst,
        mode=new.mode,
        options=JobOptions(
            batch_size=new.batch_size,
            dst_base_id=await gateway.last_message_id(dst.id),
            pushdown=new.pushdown,
        ),
        filters_json=new.filters.to_json(),
    )
    return await store.create_job(spec)


async def resolve_job(store: Store, ref: str) -> Job:
    """A job by numeric id or exact name (names must be unique to be usable)."""
    ref = ref.strip()
    if ref.isdecimal() and (job := await store.get_job(int(ref))) is not None:
        return job
    matches = await store.find_jobs_by_name(ref)
    if not matches:
        raise JobNotFound(ref)
    if len(matches) > 1:
        raise AmbiguousJob(ref, matches)
    return matches[0]


def check_runnable(job: Job, now: datetime) -> None:
    """Refuse a run that Telegram already told us would be rejected. Raises ``JobWaiting``."""
    if job.status is JobStatus.WAITING_FLOOD and job.resume_at is not None and job.resume_at > now:
        reason = "daily_cap" if job.fail_reason == DAILY_CAP else "flood"
        raise JobWaiting(job.resume_at, reason)
    if job.status is JobStatus.FAILED and job.fail_reason == "peer_flood":
        until = job.updated_at + PEER_FLOOD_COOLDOWN
        if until > now:
            raise JobWaiting(until, "peer_flood")
