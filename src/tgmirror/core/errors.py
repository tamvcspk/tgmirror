"""tgmirror error types.

The gateway maps Telethon exceptions to the ``GatewayError`` family at its boundary, so the engine
never imports Telethon (see docs/01-kien-truc.md, "Xử lý lỗi").
"""


class TgMirrorError(Exception):
    """Base class for every error tgmirror raises on purpose."""


class ConfigError(TgMirrorError):
    """Invalid or unreadable configuration."""


class MissingCredentials(ConfigError):
    """No ``api_id``/``api_hash`` in the config or the environment."""


class GatewayError(TgMirrorError):
    """Base class for errors mapped from Telegram at the gateway boundary."""


class FloodWait(GatewayError):
    """FLOOD_WAIT_x: Telegram asks us to wait ``seconds`` before the next call."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"FLOOD_WAIT {seconds}s")
        self.seconds = seconds


class PeerFlood(GatewayError):
    """PEER_FLOOD: the account is flagged for spam-like behaviour. Never retried."""


class NoPermission(GatewayError):
    """Missing rights on a channel (cannot post, not admin, channel not accessible)."""


class ForwardsRestricted(GatewayError):
    """CHAT_FORWARDS_RESTRICTED: the source has "Restrict saving content" on (decision D3)."""


class FileRefExpired(GatewayError):
    """FILE_REFERENCE_EXPIRED: refetch the message and retry once."""


class Transient(GatewayError):
    """Connection-level problem that is expected to go away; retried with backoff."""


class TooManyChannels(GatewayError):
    """The account reached Telegram's limit on channels it may own or join."""


class SessionBusy(GatewayError):
    """The session file is locked: another tgmirror process uses it (one process per session)."""


class PerMessage(GatewayError):
    """One message cannot be processed (invalid media, ...). ``reason`` goes to msg_map."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StoreError(TgMirrorError):
    """The SQLite state is unusable or was asked to do something inconsistent."""


class SchemaTooNew(StoreError):
    """The database was written by a newer tgmirror than this one."""


class JobBusy(StoreError):
    """Another runner holds the job (fresh heartbeat). ``--force-takeover`` overrides it."""

    def __init__(self, job_id: int) -> None:
        super().__init__(f"job {job_id} is being run by another process")
        self.job_id = job_id


class UsageError(TgMirrorError):
    """The command was invoked wrongly (missing flag, no terminal for a prompt). Exit code 2."""


class AuthError(GatewayError):
    """Base class for login problems."""


class NotLoggedIn(AuthError):
    """No valid session: run ``tgmirror login``."""


class BadApiCredentials(AuthError):
    """Telegram rejected ``api_id``/``api_hash``."""


class InvalidPhone(AuthError):
    """The phone number is not accepted (format, or banned)."""


class InvalidCode(AuthError):
    """Wrong login code."""


class CodeExpired(AuthError):
    """The login code expired; a new one must be requested."""


class PasswordRequired(AuthError):
    """The account has two-step verification: a password is needed to finish signing in."""


class InvalidPassword(AuthError):
    """Wrong two-step verification password."""
