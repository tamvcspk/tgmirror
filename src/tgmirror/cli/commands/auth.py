"""``tgmirror login``, ``logout`` and ``whoami``."""

import re
from typing import Annotated

import typer

from tgmirror.cli.errors import UsageProblem, run
from tgmirror.cli.runtime import Runtime
from tgmirror.core.auth import AccountInfo
from tgmirror.core.auth import login as run_login
from tgmirror.core.config import Config, save_credentials
from tgmirror.core.errors import MissingCredentials, NotLoggedIn
from tgmirror.ui.messages import t

MAX_API_ID_ATTEMPTS = 3


def who(account: AccountInfo) -> str:
    """Name and username; never the phone number (hard rule 6)."""
    return f"{account.name} (@{account.username})" if account.username else account.name


class _LoginPrompts:
    """``LoginPrompts`` on top of the runtime's prompter. Code and password are not echoed."""

    def __init__(self, rt: Runtime, phone_default: str) -> None:
        self._rt = rt
        self._phone_default = phone_default

    async def phone(self) -> str:
        if not self._rt.interactive:  # the code arrives out of band, so we cannot go on
            raise UsageProblem("err.no_tty")
        raw = await self._rt.prompter.text(t("login.prompt_phone"), default=self._phone_default)
        return re.sub(r"[\s\-()]", "", raw)

    async def code(self) -> str:
        return (await self._rt.prompter.secret(t("login.prompt_code"))).strip()

    async def password(self) -> str:
        return await self._rt.prompter.secret(t("login.prompt_password"))

    def notify(self, key: str) -> None:
        typer.echo(t(key))


async def _ensure_credentials(rt: Runtime) -> Config:
    """Return a config with ``api_id``/``api_hash``, asking for and saving them if missing."""
    config = rt.config()
    if config.api_id is not None and config.api_hash is not None:
        return config
    if not rt.interactive:
        raise MissingCredentials("api_id/api_hash are not configured")

    typer.echo(t("login.api_intro"))
    api_id = await _ask_api_id(rt)
    api_hash = (await rt.prompter.secret(t("login.prompt_api_hash"))).strip()
    save_credentials(rt.paths, api_id, api_hash)
    typer.echo(t("login.api_saved", path=rt.paths.config_file))
    return rt.config()


async def _ask_api_id(rt: Runtime) -> int:
    for _ in range(MAX_API_ID_ATTEMPTS):
        raw = (await rt.prompter.text(t("login.prompt_api_id"))).strip()
        if raw.isdecimal() and int(raw) > 0:
            return int(raw)
        rt.prompter.say(t("login.api_id_invalid"))
    raise UsageProblem("login.api_id_invalid")


def login(
    ctx: typer.Context,
    phone: Annotated[
        str | None, typer.Option("--phone", help="Prefill the phone number, e.g. +84901234567.")
    ] = None,
) -> None:
    """Save api_id/api_hash and log in to Telegram (phone, code, optional 2FA password).

    Example: tgmirror login
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        config = await _ensure_credentials(rt)
        async with rt.connect(config) as conn:
            if (existing := await conn.auth.account()) is not None:
                typer.echo(t("login.already", who=who(existing)))
                return
            account = await run_login(conn.auth, _LoginPrompts(rt, phone or ""))
        typer.echo(t("login.ok", who=who(account)))

    run(rt, command())


def logout(ctx: typer.Context) -> None:
    """End the Telegram session and delete the local session file.

    Example: tgmirror logout
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with rt.connect(rt.config()) as conn:
            account = await conn.auth.account()
            if account is None:
                typer.echo(t("logout.none"))
                return
            await conn.auth.log_out()
        typer.echo(t("logout.ok", who=who(account)))

    run(rt, command())


def whoami(ctx: typer.Context) -> None:
    """Show which account is logged in (name, username, id; never the phone number).

    Example: tgmirror whoami
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with rt.connect(rt.config()) as conn:
            account = await conn.auth.account()
        if account is None:
            raise NotLoggedIn("no valid session")
        typer.echo(t("whoami.line", who=who(account), id=account.id))

    run(rt, command())
