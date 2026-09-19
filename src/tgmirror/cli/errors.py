"""Turning exceptions into one human sentence and an exit code (docs/02-cli-ux.md, "Mã thoát").

``0`` ok · ``1`` general · ``2`` usage · ``3`` stopped by flood/peer_flood · ``4`` missing
permission · ``130`` interrupted. Tracebacks only with ``--debug``.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

import typer

from tgmirror.cli.runtime import Runtime
from tgmirror.core.errors import (
    BadApiCredentials,
    ConfigError,
    FloodWait,
    ForwardsRestricted,
    MissingCredentials,
    NoPermission,
    NotLoggedIn,
    PeerFlood,
    SessionBusy,
    TgMirrorError,
    TooManyChannels,
    Transient,
    UsageError,
)
from tgmirror.engine.endpoints import (
    AmbiguousChannel,
    ChannelNotFound,
    DestinationNotWritable,
    EndpointError,
    InvalidChannelTitle,
    KindMismatch,
    NewChannelUnsupported,
    SameChannel,
    SourceRestricted,
)
from tgmirror.ui.messages import t
from tgmirror.ui.tables import channel_label


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
        case SourceRestricted():
            return t("err.source_restricted", title=exc.src.title)
        case DestinationNotWritable():
            return t("err.dest_not_writable", title=exc.dst.title)
        case ForwardsRestricted() | NoPermission():
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
        case KindMismatch():
            return t(
                "err.kind_mismatch", src=t(f"kind.{exc.src.kind}"), dst=t(f"kind.{exc.dst.kind}")
            )
        case SameChannel():
            return t("err.same_channel")
        case InvalidChannelTitle():
            return t(f"err.{exc.reason}")
        case NewChannelUnsupported():
            return t("err.new_unsupported", kind=t(f"kind.{exc.kind}"))
    return t("err.generic", detail=str(exc))


def exit_code(exc: TgMirrorError) -> int:
    if isinstance(exc, FloodWait | PeerFlood):
        return 3
    if isinstance(
        exc, NoPermission | ForwardsRestricted | SourceRestricted | DestinationNotWritable
    ):
        return 4
    if isinstance(exc, UsageError | ConfigError | BadApiCredentials | EndpointError):
        return 2
    return 1


def run(rt: Runtime, coro: Coroutine[Any, Any, None]) -> None:
    """Run a command's coroutine, printing errors the CLI way and exiting with the right code."""
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        typer.echo(t("err.aborted"), err=True)
        raise typer.Exit(130) from None
    except TgMirrorError as exc:
        if rt.debug:
            raise
        typer.echo(describe(exc), err=True)
        raise typer.Exit(exit_code(exc)) from None
