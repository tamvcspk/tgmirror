"""``tgmirror login``, ``logout`` and ``whoami``."""

import re
from collections.abc import Callable
from typing import Annotated

import typer

from tgmirror.cli.errors import UsageProblem, run
from tgmirror.cli.runtime import Runtime
from tgmirror.core.auth import AccountInfo
from tgmirror.core.auth import login as run_login
from tgmirror.core.config import Config, save_credentials
from tgmirror.core.errors import MissingCredentials, NotLoggedIn
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Prompter

MAX_API_ID_ATTEMPTS = 3


def who(account: AccountInfo) -> str:
    """Name and username; never the phone number (hard rule 6)."""
    return f"{account.name} (@{account.username})" if account.username else account.name


class LoginPromptsOn:
    """``LoginPrompts`` on top of a ``Prompter`` (the runtime's, or the full-screen menu's).
    Code and password are not echoed. ``echo`` shows the flow's short notices."""

    def __init__(
        self,
        prompter: Prompter,
        phone_default: str = "",
        *,
        interactive: bool = True,
        echo: Callable[[str], None] = typer.echo,
    ) -> None:
        self._prompter = prompter
        self._phone_default = phone_default
        self._interactive = interactive
        self._echo = echo

    async def phone(self) -> str:
        if not self._interactive:  # the code arrives out of band, so we cannot go on
            raise UsageProblem("err.no_tty")
        raw = await self._prompter.text(t("login.prompt_phone"), default=self._phone_default)
        return re.sub(r"[\s\-()]", "", raw)

    async def code(self) -> str:
        return (await self._prompter.secret(t("login.prompt_code"))).strip()

    async def password(self) -> str:
        return await self._prompter.secret(t("login.prompt_password"))

    def notify(self, key: str) -> None:
        self._echo(t(key))


async def ensure_credentials(
    rt: Runtime,
    *,
    prompter: Prompter | None = None,
    interactive: bool | None = None,
    echo: Callable[[str], None] = typer.echo,
) -> Config:
    """Return a config with ``api_id``/``api_hash``, asking for and saving them if missing.
    ``prompter``/``interactive`` default to the runtime's (the menu passes its own)."""
    config = rt.config()
    if config.api_id is not None and config.api_hash is not None:
        return config
    if not (rt.interactive if interactive is None else interactive):
        raise MissingCredentials("api_id/api_hash are not configured")

    prompter = prompter or rt.prompter
    echo(t("login.api_intro"))
    api_id = await _ask_api_id(prompter)
    api_hash = (await prompter.secret(t("login.prompt_api_hash"))).strip()
    save_credentials(rt.paths, api_id, api_hash)
    echo(t("login.api_saved", path=rt.paths.config_file))
    return rt.config()


async def _ask_api_id(prompter: Prompter) -> int:
    for _ in range(MAX_API_ID_ATTEMPTS):
        raw = (await prompter.text(t("login.prompt_api_id"))).strip()
        if raw.isdecimal() and int(raw) > 0:
            return int(raw)
        prompter.say(t("login.api_id_invalid"))
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
        config = await ensure_credentials(rt)
        async with rt.connect(config) as conn:
            if (existing := await conn.auth.account()) is not None:
                typer.echo(t("login.already", who=who(existing)))
                return
            account = await run_login(
                conn.auth, LoginPromptsOn(rt.prompter, phone or "", interactive=rt.interactive)
            )
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
