from collections.abc import Sequence

import pytest

from tests.fakes import FakeAuth
from tgmirror.core.auth import AccountInfo, login
from tgmirror.core.errors import (
    CodeExpired,
    InvalidCode,
    InvalidPassword,
    InvalidPhone,
    PasswordRequired,
)

GOOD_PHONE = "+84901234567"


class Prompts:
    """Scripted ``LoginPrompts``: answers are consumed in order, notifications recorded."""

    def __init__(
        self,
        phones: Sequence[str] = (GOOD_PHONE,),
        codes: Sequence[str] = ("12345",),
        passwords: Sequence[str] = (),
    ) -> None:
        self._phones, self._codes, self._passwords = list(phones), list(codes), list(passwords)
        self.notes: list[str] = []

    async def phone(self) -> str:
        return self._phones.pop(0)

    async def code(self) -> str:
        return self._codes.pop(0)

    async def password(self) -> str:
        return self._passwords.pop(0)

    def notify(self, key: str) -> None:
        self.notes.append(key)


async def test_plain_login() -> None:
    auth, prompts = FakeAuth(), Prompts()

    account = await login(auth, prompts)

    assert account == AccountInfo(id=42, name="Test User", username="tester")
    assert prompts.notes == ["login.code_sent"]


async def test_existing_session_skips_prompts() -> None:
    existing = AccountInfo(id=1, name="Already")
    auth, prompts = FakeAuth(logged_in=existing), Prompts(phones=(), codes=())

    assert await login(auth, prompts) == existing
    assert "request_code" not in auth.calls


async def test_two_step_password() -> None:
    auth = FakeAuth(password="s3cret")

    account = await login(auth, Prompts(passwords=("s3cret",)))

    assert account.id == 42
    assert auth.calls.count("sign_in_password") == 1


async def test_wrong_code_then_right_code() -> None:
    auth, prompts = FakeAuth(), Prompts(codes=("00000", "12345"))

    await login(auth, prompts)

    assert prompts.notes == ["login.code_sent", "login.code_invalid"]


async def test_three_wrong_codes_give_up() -> None:
    auth = FakeAuth()

    with pytest.raises(InvalidCode):
        await login(auth, Prompts(codes=("1", "2", "3")))

    assert auth.current is None


async def test_expired_code_is_resent_once() -> None:
    auth, prompts = FakeAuth(), Prompts(codes=("12345", "12345"))
    auth.fail_next("sign_in_code", CodeExpired("expired"))

    await login(auth, prompts)

    assert auth.calls.count("request_code") == 2
    assert "login.code_expired" in prompts.notes


async def test_second_expiry_is_an_error() -> None:
    auth = FakeAuth()
    auth.fail_next("sign_in_code", CodeExpired("expired"), times=2)

    with pytest.raises(CodeExpired):
        await login(auth, Prompts(codes=("12345", "12345")))


async def test_bad_phone_then_good_phone() -> None:
    auth, prompts = FakeAuth(), Prompts(phones=("not a phone", GOOD_PHONE))

    await login(auth, prompts)

    assert prompts.notes[0] == "login.phone_invalid"


async def test_three_bad_phones_give_up() -> None:
    with pytest.raises(InvalidPhone):
        await login(FakeAuth(), Prompts(phones=("x", "y", "z")))


async def test_wrong_password_retries_then_gives_up() -> None:
    auth = FakeAuth(password="right")

    with pytest.raises(InvalidPassword):
        await login(auth, Prompts(passwords=("a", "b", "c")))

    assert auth.calls.count("sign_in_password") == 3


async def test_password_needed_after_a_scripted_error_still_finishes() -> None:
    auth = FakeAuth()
    auth.fail_next("sign_in_code", PasswordRequired("2FA"))  # the flow must go on to the password
    auth.password = "pw"

    account = await login(auth, Prompts(passwords=("pw",)))

    assert account.username == "tester"
