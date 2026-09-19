"""Signing in: the ``TelegramAuth`` protocol and the login flow on top of it.

Login talks to Telegram too, so like the gateway it is a protocol (hard rules 1 and 8): the flow
below is tested with a fake, and ``core/telethon_gateway.py`` holds the Telethon implementation.
The flow asks the user through a ``LoginPrompts`` object and never prints or logs the phone, the
code or the password (hard rule 6).
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from tgmirror.core.errors import (
    CodeExpired,
    InvalidCode,
    InvalidPassword,
    InvalidPhone,
    PasswordRequired,
)

MAX_ATTEMPTS = 3  # per step (phone, code, password), so a typo does not cost the whole login


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """Who is logged in. Deliberately has no phone number (hard rule 6)."""

    id: int
    name: str
    username: str | None = None


@runtime_checkable
class TelegramAuth(Protocol):
    async def account(self) -> AccountInfo | None:
        """The logged-in account, or ``None`` when the session is missing or no longer valid."""
        ...

    async def request_code(self, phone: str) -> None:
        """Ask Telegram to send a login code. Raises ``InvalidPhone``."""
        ...

    async def sign_in_code(self, phone: str, code: str) -> None:
        """Finish with the code. Raises ``InvalidCode``, ``CodeExpired`` or ``PasswordRequired``."""
        ...

    async def sign_in_password(self, password: str) -> None:
        """Finish with the two-step verification password. Raises ``InvalidPassword``."""
        ...

    async def log_out(self) -> None:
        """End the session on Telegram's side and delete the local session file."""
        ...


class LoginPrompts(Protocol):
    """What the flow needs from the user. ``cli/`` implements it with questionary."""

    async def phone(self) -> str: ...

    async def code(self) -> str: ...

    async def password(self) -> str: ...

    def notify(self, key: str) -> None:
        """Tell the user something short ("code sent", "wrong code, try again")."""
        ...


async def login(auth: TelegramAuth, prompts: LoginPrompts) -> AccountInfo:
    """Sign in unless the session is already valid; return the account.

    Raises the last ``AuthError`` when the user runs out of attempts.
    """
    if (existing := await auth.account()) is not None:
        return existing

    phone = await _request_code(auth, prompts)
    await _enter_code(auth, prompts, phone)

    account = await auth.account()
    if account is None:  # sign-in reported success but the session is not authorized
        raise InvalidCode("login did not complete")
    return account


async def _request_code(auth: TelegramAuth, prompts: LoginPrompts) -> str:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        phone = await prompts.phone()
        try:
            await auth.request_code(phone)
        except InvalidPhone:
            if attempt == MAX_ATTEMPTS:
                raise
            prompts.notify("login.phone_invalid")
        else:
            prompts.notify("login.code_sent")
            return phone
    raise AssertionError("unreachable")  # pragma: no cover


async def _enter_code(auth: TelegramAuth, prompts: LoginPrompts, phone: str) -> None:
    resent = False
    attempt = 0
    while True:
        code = await prompts.code()
        try:
            await auth.sign_in_code(phone, code)
        except PasswordRequired:
            await _enter_password(auth, prompts)
            return
        except CodeExpired:
            if resent:
                raise
            resent = True  # one automatic resend; the wrong-code counter is not spent on expiry
            await auth.request_code(phone)
            prompts.notify("login.code_expired")
        except InvalidCode:
            attempt += 1
            if attempt >= MAX_ATTEMPTS:
                raise
            prompts.notify("login.code_invalid")
        else:
            return


async def _enter_password(auth: TelegramAuth, prompts: LoginPrompts) -> None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        password = await prompts.password()
        try:
            await auth.sign_in_password(password)
        except InvalidPassword:
            if attempt == MAX_ATTEMPTS:
                raise
            prompts.notify("login.password_invalid")
        else:
            return
