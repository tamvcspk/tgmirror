"""Where api_id/api_hash resolve from: env, ``*_FILE``, keyring, ``config.toml`` — highest priority
first (docs/06-lo-trinh.md, Phase 9). ``fake_keyring`` (``tests/conftest.py``) installs a real,
usable in-memory backend before every test; some tests here swap in an unusable one on purpose to
check the fallback.
"""

from pathlib import Path

import keyring
import keyring.backends.fail
import keyring.backends.null
import pytest

from tests.fakes import FakeKeyring
from tgmirror.core.errors import ConfigError
from tgmirror.core.secrets import (
    ENV_API_HASH_FILE,
    ENV_API_ID_FILE,
    keyring_usable,
    read_keyring,
    resolve_credentials,
    write_keyring,
)


def test_keyring_usable_with_the_fake_backend() -> None:
    assert keyring_usable() is True


@pytest.mark.parametrize(
    "backend", [keyring.backends.fail.Keyring(), keyring.backends.null.Keyring()]
)
def test_unusable_backends_are_not_usable(backend: object) -> None:
    keyring.set_keyring(backend)

    assert keyring_usable() is False
    assert read_keyring() == (None, None)


def test_write_then_read_keyring_roundtrip() -> None:
    write_keyring(12345, "abcdef0123456789")

    assert read_keyring() == (12345, "abcdef0123456789")


def test_resolve_prefers_env_over_everything(fake_keyring: FakeKeyring) -> None:
    fake_keyring.set_password("tgmirror", "api_id", "1")
    fake_keyring.set_password("tgmirror", "api_hash", "from-keyring")

    creds = resolve_credentials(
        {"TGMIRROR_API_ID": "9", "TGMIRROR_API_HASH": "from-env"},
        config_api_id=2,
        config_api_hash="from-config",
    )

    assert (creds.api_id, creds.api_hash, creds.source) == (9, "from-env", "env")


def test_resolve_prefers_file_over_keyring_and_config(
    tmp_path: Path, fake_keyring: FakeKeyring
) -> None:
    fake_keyring.set_password("tgmirror", "api_id", "1")
    fake_keyring.set_password("tgmirror", "api_hash", "from-keyring")
    id_file = tmp_path / "api_id"
    hash_file = tmp_path / "api_hash"
    id_file.write_text("55\n", encoding="utf-8")
    hash_file.write_text("from-file\n", encoding="utf-8")

    creds = resolve_credentials(
        {ENV_API_ID_FILE: str(id_file), ENV_API_HASH_FILE: str(hash_file)},
        config_api_id=2,
        config_api_hash="from-config",
    )

    assert (creds.api_id, creds.api_hash, creds.source) == (55, "from-file", "file")


def test_resolve_prefers_keyring_over_config(fake_keyring: FakeKeyring) -> None:
    fake_keyring.set_password("tgmirror", "api_id", "77")
    fake_keyring.set_password("tgmirror", "api_hash", "from-keyring")

    creds = resolve_credentials({}, config_api_id=2, config_api_hash="from-config")

    assert creds.api_id == 77
    assert creds.api_hash == "from-keyring"
    assert creds.source.startswith("keyring (")


def test_resolve_falls_back_to_config_when_nothing_else_is_set() -> None:
    creds = resolve_credentials({}, config_api_id=2, config_api_hash="from-config")

    assert (creds.api_id, creds.api_hash, creds.source) == (2, "from-config", "config.toml")


def test_resolve_reports_none_when_nothing_is_configured_anywhere() -> None:
    creds = resolve_credentials({}, config_api_id=None, config_api_hash=None)

    assert (creds.api_id, creds.api_hash, creds.source) == (None, None, "none")


def test_unusable_keyring_falls_through_to_config_in_resolve() -> None:
    keyring.set_keyring(keyring.backends.fail.Keyring())

    creds = resolve_credentials({}, config_api_id=2, config_api_hash="from-config")

    assert (creds.api_id, creds.api_hash, creds.source) == (2, "from-config", "config.toml")


def test_api_id_file_must_contain_an_integer(tmp_path: Path) -> None:
    id_file = tmp_path / "api_id"
    id_file.write_text("not-a-number", encoding="utf-8")

    with pytest.raises(ConfigError, match=ENV_API_ID_FILE):
        resolve_credentials(
            {ENV_API_ID_FILE: str(id_file)}, config_api_id=None, config_api_hash=None
        )


def test_missing_file_reports_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    with pytest.raises(ConfigError, match="does-not-exist"):
        resolve_credentials(
            {ENV_API_HASH_FILE: str(missing)}, config_api_id=None, config_api_hash=None
        )
