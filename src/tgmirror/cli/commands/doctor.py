"""``tgmirror doctor`` (docs/02-cli-ux.md): a quick, read-mostly health check — session, cryptg,
write access to channels already used as a destination, and the safety notes docs/05-chong-flood.md
("Vệ sinh chung") and docs/00-tong-quan.md ask README and this command to repeat.

Every check reports what it found rather than raising: one broken check (no session, an
unreachable destination) must not hide the others. ``doctor`` exits 0 unless ``config.toml``
itself cannot be read (an ordinary ``ConfigError``, same as any other command)."""

import importlib.util
from contextlib import AsyncExitStack

import typer

from tgmirror.cli.commands.auth import who
from tgmirror.cli.errors import describe, run
from tgmirror.cli.runtime import Runtime, opened_store
from tgmirror.core.auth import AccountInfo
from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import TelegramGateway
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
        gateway: TelegramGateway | None = None
        async with AsyncExitStack() as stack:
            config = rt.config()
            if config.api_id is None or config.api_hash is None:
                typer.echo(t("doctor.session_missing_credentials"))
            else:
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


def _safety_lines() -> list[str]:
    return [t("doctor.safety_account"), t("doctor.safety_sessions"), t("doctor.safety_risk")]
