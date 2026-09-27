"""``tgmirror doctor`` (docs/02-cli-ux.md): a quick, read-mostly health check — session, cryptg,
write access to channels already used as a destination, and the safety notes docs/05-chong-flood.md
("Vệ sinh chung") and docs/00-tong-quan.md ask README and this command to repeat.

Every check reports what it found rather than raising: one broken check (no session, an
unreachable destination) must not hide the others. ``doctor`` exits 0 unless ``config.toml``
itself cannot be read (an ordinary ``ConfigError``, same as any other command)."""

import importlib.util
import os
import stat
from contextlib import AsyncExitStack

import typer

from tgmirror.cli.commands.auth import who
from tgmirror.cli.errors import describe, run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.auth import AccountInfo
from tgmirror.core.config import config_has_credentials, credential_source
from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import TelegramGateway
from tgmirror.core.paths import Paths
from tgmirror.core.secrets import keyring_usable
from tgmirror.store.db import Store
from tgmirror.ui.messages import t


def doctor(ctx: typer.Context) -> None:
    """Check the session, cryptg, access to channels already used as a destination, and print the
    safety notes README repeats.

    Example: tgmirror doctor
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        typer.echo(t("doctor.title"))
        # Checked before anything below gets a chance to call ``Paths.ensure()`` (``rt.connect``,
        # ``opened_store``), which would otherwise silently re-tighten a widened directory before
        # this ever saw it — this must report what it *found*, not what running doctor itself just
        # fixed (T5, Phase 15b).
        for line in _permission_lines(rt.paths):
            typer.echo(line)
        gateway: TelegramGateway | None = None
        async with AsyncExitStack() as stack:
            config = rt.config()
            if config.api_id is None or config.api_hash is None:
                typer.echo(t("doctor.session_missing_credentials"))
            else:
                typer.echo(
                    t("doctor.credential_source", source=credential_source(rt.paths, rt.env))
                )
                if config_has_credentials(rt.paths) and keyring_usable():
                    typer.echo(t("doctor.credential_move_suggested"))
                try:
                    conn = await stack.enter_async_context(rt.connect(config))
                    account: AccountInfo | None = await conn.auth.account()
                except TgMirrorError as exc:
                    typer.echo(t("doctor.session_error", detail=describe(exc)))
                else:
                    if account is None:
                        typer.echo(t("doctor.session_not_logged_in"))
                    else:
                        typer.echo(t("doctor.session_ok", who=who(account)))
                        gateway = conn.gateway

            typer.echo(
                t("doctor.cryptg_ok")
                if importlib.util.find_spec("cryptg") is not None
                else t("doctor.cryptg_missing")
            )

            async with opened_store(rt) as store:
                for line in await _destination_lines(store, gateway):
                    typer.echo(line)

        for line in _safety_lines():
            typer.echo(line)

    run(rt, command())


async def _destination_lines(store: Store, gateway: TelegramGateway | None) -> list[str]:
    pairs = await store.list_pairs()
    if not pairs:
        return [t("doctor.no_destinations")]
    if gateway is None:
        return [t("doctor.destinations_need_session")]
    lines: list[str] = []
    seen: set[int] = set()
    for pair in pairs:
        if pair.dst_id in seen:
            continue
        seen.add(pair.dst_id)
        try:
            dst = await gateway.get_channel(pair.dst_id)
        except TgMirrorError as exc:
            lines.append(t("doctor.destination_error", title=pair.dst_title, detail=describe(exc)))
            continue
        key = "doctor.destination_ok" if dst.is_admin and dst.can_post else "doctor.destination_bad"
        lines.append(t(key, title=dst.title))
    return lines


def _permission_lines(paths: Paths) -> list[str]:
    """T5, Phase 15b: ``sessions/`` should never be readable by anyone but the owner. Call this
    before anything that might call ``Paths.ensure()`` (see the caller). POSIX only: chmod bits do
    not mean the same thing on Windows. Nothing to say if the directory does not exist yet (no
    session has ever been created)."""
    if os.name == "nt" or not paths.sessions_dir.exists():
        return []
    mode = stat.S_IMODE(paths.sessions_dir.stat().st_mode)
    if mode & ~0o700:
        return [t("doctor.sessions_perm_warn", mode=oct(mode))]
    return [t("doctor.sessions_perm_ok")]


def _safety_lines() -> list[str]:
    return [t("doctor.safety_account"), t("doctor.safety_sessions"), t("doctor.safety_risk")]
