"""Turning exceptions into one human sentence and an exit code (docs/02-cli-ux.md, "Mã thoát").

``0`` ok · ``1`` general · ``2`` usage · ``3`` stopped by flood/peer_flood/daily cap · ``4`` missing
permission · ``130`` interrupted. Tracebacks only with ``--debug``.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import typer

from tgmirror.cli.runtime import Runtime
from tgmirror.core.errors import (
    AppDataChecksumMismatch,
    AppDataError,
    AppDataFormatError,
    AppDataSchemaNewer,
    BackupBusy,
    BadApiCredentials,
    ConfigError,
    DailyCapReached,
    ExportBusy,
    FloodWait,
    ForwardsRestricted,
    MissingCredentials,
    NoPermission,
    NotLoggedIn,
    PeerFlood,
    RunBusy,
    SchemaTooNew,
    SessionBusy,
    StoreError,
    TgMirrorError,
    TooManyChannels,
    Transient,
    UsageError,
)
from tgmirror.engine.backup import (
    BackupError,
    BackupNeedsAcknowledgement,
    FiltersChanged,
    WrongSource,
)
from tgmirror.engine.endpoints import (
    AmbiguousChannel,
    ChannelNotFound,
    DestinationNotWritable,
    EndpointError,
    InvalidChannelTitle,
    SameChannel,
    SourceRestricted,
)
from tgmirror.engine.reupload import UnsupportedMedia
from tgmirror.engine.runs import (
    InvalidOptions,
    ModeUnsupported,
    NeedsAcknowledgement,
    RunError,
    RunNotFound,
    RunWaiting,
)
from tgmirror.filters.model import FilterError
from tgmirror.filters.parser import FilterMix
from tgmirror.ui.messages import t
from tgmirror.ui.tables import channel_label


class Declined(Exception):  # noqa: N818 - a user's answer, not a failure
    """The user said no to a confirmation (``err.aborted``, exit code 1). Raised by the shared
    flows instead of ``typer.Exit`` so the full-screen menu can call them too: ``run`` below turns
    it into the CLI's message and exit code, the menu just returns to where it was."""


class UsageProblem(UsageError):
    """A usage error that already carries its message key and parameters."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


def describe(exc: TgMirrorError) -> str:
    """The message shown to the user, in the active language."""
    match exc:
        case UsageProblem():
            return t(exc.key, **exc.params)
        case UsageError():
            return t("err.generic", detail=str(exc))
        case MissingCredentials():
            return t("err.missing_credentials")
        case ConfigError():
            return t("err.config", detail=str(exc))
        case NotLoggedIn():
            return t("err.not_logged_in")
        case BadApiCredentials():
            return t("err.bad_api")
        case FloodWait():
            return t("err.flood", seconds=exc.seconds)
        case PeerFlood():
            return t("err.peer_flood")
        case DailyCapReached():
            until = exc.resume_at.astimezone().strftime("%Y-%m-%d %H:%M")
            return t("err.daily_cap", sent=exc.sent_today, cap=exc.cap, until=until)
        case SourceRestricted():
            return t("err.source_restricted", title=exc.src.title)
        case DestinationNotWritable():
            return t("err.dest_not_writable", title=exc.dst.title)
        case ForwardsRestricted():
            return t("err.forwards_restricted")
        case NoPermission():
            return t("err.no_permission", detail=str(exc))
        case TooManyChannels():
            return t("err.too_many_channels")
        case SessionBusy():
            return t("err.session_busy")
        case Transient():
            return t("err.transient")
        case ChannelNotFound():
            return t("err.channel_not_found", ref=exc.ref)
        case AmbiguousChannel():
            return t(
                "err.ambiguous",
                ref=exc.ref,
                matches=", ".join(channel_label(c) for c in exc.matches),
            )
        case SameChannel():
            return t("err.same_channel")
        case InvalidChannelTitle():
            return t(f"err.{exc.reason}")
        case RunNotFound():
            return t("err.run_none") if exc.ref is None else t("err.run_not_found", ref=exc.ref)
        case ModeUnsupported():
            return t("err.mode_unsupported", mode=exc.mode)
        case InvalidOptions():
            return t(f"err.opt_{exc.key}")
        case NeedsAcknowledgement():
            return t("err.needs_admin_ack_rerun", title=exc.title)
        case UnsupportedMedia():
            return t("err.unsupported_media", id=exc.msg_id, kind=exc.kind)
        case RunWaiting():
            until = exc.until.astimezone().strftime("%Y-%m-%d %H:%M")
            return t(f"err.run_waiting_{exc.reason}", until=until)
        case RunBusy():
            return t("err.run_busy", id=exc.run_id)
        case FilterMix():
            return t("err.filter_mix")
        case FilterError():
            return t("err.filter", detail=exc.detail)
        case SchemaTooNew():
            return t("err.schema_too_new")
        case StoreError():
            return t("err.store", detail=str(exc))
        case ExportBusy():
            return t("err.appdata_export_busy")
        case AppDataSchemaNewer():
            return t("err.appdata_schema_newer", found=exc.found, known=exc.known)
        case AppDataChecksumMismatch():
            return t("err.appdata_checksum", entry=exc.entry)
        case AppDataFormatError():
            return t("err.appdata_format", detail=str(exc))
        case BackupNeedsAcknowledgement():
            return t("err.backup_needs_admin_ack", title=exc.title)
        case FiltersChanged():
            return t("err.backup_filters_changed", dir=exc.directory)
        case WrongSource():
            return t("err.backup_wrong_source", dir=exc.directory, title=exc.existing_title)
        case BackupBusy():
            return t("err.backup_busy", id=exc.backup_id)
    return t("err.generic", detail=str(exc))


def exit_code(exc: TgMirrorError) -> int:
    if isinstance(exc, FloodWait | PeerFlood | DailyCapReached | RunWaiting):
        return 3
    if isinstance(
        exc, NoPermission | ForwardsRestricted | SourceRestricted | DestinationNotWritable
    ):
        return 4
    if isinstance(
        exc,
        UsageError
        | ConfigError
        | BadApiCredentials
        | EndpointError
        | RunError
        | FilterError
        | AppDataError
        | BackupError,
    ):
        return 2
    return 1


T = TypeVar("T")


def run(rt: Runtime, coro: Coroutine[Any, Any, T]) -> T:
    """Run a command's coroutine, printing errors the CLI way and exiting with the right code.

    Every command ignores the return value (their coroutines return ``None``); the bare
    ``tgmirror`` menu (``cli/app.py``) is the one caller that uses it, for the exit code
    ``ui.menu.app.launch`` decides.
    """
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        typer.echo(t("err.aborted"), err=True)
        raise typer.Exit(130) from None
    except Declined:
        typer.echo(t("err.aborted"), err=True)
        raise typer.Exit(1) from None
    except TgMirrorError as exc:
        if rt.debug:
            raise
        typer.echo(describe(exc), err=True)
        raise typer.Exit(exit_code(exc)) from None
