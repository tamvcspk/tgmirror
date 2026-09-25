"""Where ``api_id``/``api_hash`` can come from, besides ``config.toml`` (docs/06-lo-trinh.md,
"Phase 9 — Keyring"): environment variables, ``*_FILE`` (a path to a file holding the value, for
Docker/Kubernetes secret mounts), and the OS keyring. ``core.config`` is the only caller; it owns
``config.toml`` itself and merges that in as the lowest-priority tier.

Scope is deliberately narrow: only these two values. The ``.session`` file — the real key to the
account — is untouched here and still just a file under ``Paths.sessions_dir`` (hard rule 6).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import keyring
import keyring.errors

from tgmirror.core.errors import ConfigError

SERVICE = "tgmirror"
_KEY_API_ID = "api_id"
_KEY_API_HASH = "api_hash"

ENV_API_ID = "TGMIRROR_API_ID"
ENV_API_HASH = "TGMIRROR_API_HASH"
ENV_API_ID_FILE = "TGMIRROR_API_ID_FILE"
ENV_API_HASH_FILE = "TGMIRROR_API_HASH_FILE"

SOURCE_ENV = "env"
SOURCE_FILE = "file"
SOURCE_CONFIG = "config.toml"
SOURCE_NONE = "none"

# Backends that store nothing, or store it in the clear: treated the same as "no keyring" so a
# headless machine or container is never blocked on one, and nothing silently ends up in plaintext
# under the keyring's name (docs/06-lo-trinh.md, Phase 9, "Dùng được").
_UNUSABLE_BACKENDS = frozenset(
    {
        "keyring.backends.fail.Keyring",
        "keyring.backends.null.Keyring",
        "keyrings.alt.file.PlaintextKeyring",
    }
)


def _backend_name(backend: object) -> str:
    return f"{type(backend).__module__}.{type(backend).__qualname__}"


def keyring_usable() -> bool:
    """Whether ``keyring`` resolves to a real, secret-storing backend on this machine."""
    try:
        backend = keyring.get_keyring()
    except keyring.errors.NoKeyringError:
        return False
    return _backend_name(backend) not in _UNUSABLE_BACKENDS


def keyring_backend_label() -> str:
    """Short backend name for ``doctor``, e.g. ``WinVaultKeyring``. Caller checks usability first;
    a display name for an unusable backend is meaningless but harmless."""
    return type(keyring.get_keyring()).__name__


def read_keyring() -> tuple[int | None, str | None]:
    """``(None, None)`` when there is no usable keyring, nothing stored, or the backend refuses
    (e.g. locked) — same as "not configured here", so the caller falls through to the next tier."""
    if not keyring_usable():
        return None, None
    try:
        raw_id = keyring.get_password(SERVICE, _KEY_API_ID)
        raw_hash = keyring.get_password(SERVICE, _KEY_API_HASH)
    except keyring.errors.KeyringError:
        return None, None
    return (int(raw_id) if raw_id else None), (raw_hash or None)


def write_keyring(api_id: int, api_hash: str) -> None:
    keyring.set_password(SERVICE, _KEY_API_ID, str(api_id))
    keyring.set_password(SERVICE, _KEY_API_HASH, api_hash)


def _read_file(env_var: str, path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"cannot read {env_var}={path}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class ResolvedCredentials:
    api_id: int | None
    api_hash: str | None
    source: str  # one of the SOURCE_* constants above, or "keyring (<backend>)"


def resolve_credentials(
    env: Mapping[str, str],
    *,
    config_api_id: object,
    config_api_hash: object,
) -> ResolvedCredentials:
    """Merge every tier, lowest priority first: ``config.toml`` (already read by the caller), the
    keyring, ``*_FILE``, then the plain environment variables — matching "Thứ tự đọc" in
    docs/06-lo-trinh.md. Each tier only overrides the fields it actually sets, so e.g. an
    ``api_id`` from the keyring can still be paired with an ``api_hash`` from ``config.toml``
    if that is genuinely what is on disk (not a case we expect, but not one to break on either).
    """
    # Passed through as-is, even if malformed (e.g. a string where config.toml should have an
    # int): Config.model_validate is what reports that, same as before this tier existed.
    api_id: object = config_api_id
    api_hash: object = config_api_hash
    source = SOURCE_CONFIG if (api_id is not None or api_hash is not None) else SOURCE_NONE

    kr_id, kr_hash = read_keyring()
    if kr_id is not None or kr_hash is not None:
        api_id = kr_id if kr_id is not None else api_id
        api_hash = kr_hash if kr_hash is not None else api_hash
        source = f"keyring ({keyring_backend_label()})"

    id_file, hash_file = env.get(ENV_API_ID_FILE), env.get(ENV_API_HASH_FILE)
    if id_file is not None or hash_file is not None:
        if id_file is not None:
            raw = _read_file(ENV_API_ID_FILE, id_file)
            try:
                api_id = int(raw)
            except ValueError:
                raise ConfigError(
                    f"{ENV_API_ID_FILE} must point to a file with an integer"
                ) from None
        if hash_file is not None:
            api_hash = _read_file(ENV_API_HASH_FILE, hash_file)
        source = SOURCE_FILE

    if (raw_id := env.get(ENV_API_ID)) is not None:
        try:
            api_id = int(raw_id)
        except ValueError:
            raise ConfigError(f"{ENV_API_ID} must be an integer") from None
        source = SOURCE_ENV
    if (raw_hash := env.get(ENV_API_HASH)) is not None:
        api_hash = raw_hash
        source = SOURCE_ENV

    return ResolvedCredentials(api_id, api_hash, source)
