"""tgmirror error types.

The gateway maps Telethon exceptions to the ``GatewayError`` family at its boundary, so the engine
never imports Telethon (see docs/01-kien-truc.md, "Xử lý lỗi").
"""

from datetime import datetime


class TgMirrorError(Exception):
    """Base class for every error tgmirror raises on purpose."""


class ConfigError(TgMirrorError):
    """Invalid or unreadable configuration."""


class MissingCredentials(ConfigError):
    """No ``api_id``/``api_hash`` in the config or the environment."""


class GatewayError(TgMirrorError):
    """Base class for errors mapped from Telegram at the gateway boundary."""


class FloodWait(GatewayError):
    """FLOOD_WAIT_x (or SLOWMODE_WAIT_x): Telegram asks us to wait ``seconds`` before the next call.

    All three kinds are handled the same way; ``slow_mode`` and ``transport`` only tell them
    apart in ``flood_log``. ``transport`` is not a wait Telegram named: it is the HTTP 429 the
    server sent on the connection while files were moving (spike 12, and the first real run),
    and ``seconds`` is how long we choose to rest.
    """

    def __init__(self, seconds: int, *, slow_mode: bool = False, transport: bool = False) -> None:
        label = "TRANSPORT_429" if transport else ("SLOWMODE_WAIT" if slow_mode else "FLOOD_WAIT")
        super().__init__(f"{label} {seconds}s")
        self.seconds = seconds
        self.slow_mode = slow_mode
        self.transport = transport


class PeerFlood(GatewayError):
    """PEER_FLOOD: the account is flagged for spam-like behaviour. Never retried."""


class DailyCapReached(TgMirrorError):
    """Today's ``daily_cap`` is used up: not a failure, the run rests until ``resume_at``.

    It is our own budget (docs/05-chong-flood.md), not something Telegram said, so it is not a
    ``GatewayError``.
    """

    def __init__(self, resume_at: datetime, sent_today: int, cap: int) -> None:
        super().__init__(f"daily cap of {cap} messages reached ({sent_today} sent today)")
        self.resume_at = resume_at
        self.sent_today = sent_today
        self.cap = cap


class NoPermission(GatewayError):
    """Missing rights on a channel (cannot post, not admin, channel not accessible)."""


class ForwardsRestricted(GatewayError):
    """CHAT_FORWARDS_RESTRICTED: the source has "Restrict saving content" on (decision D3)."""


class FileRefExpired(GatewayError):
    """The media cannot be sent again by its id: its file reference expired (refetch the
    message and retry once) or Telegram will not reuse it (send it the long way)."""


class Transient(GatewayError):
    """Connection-level problem that is expected to go away; retried with backoff."""


class TransportPressure(GatewayError):
    """The server pushed back on the connection, below the level of a FloodWait: HTTP 429, a
    closed connection, a request that never got an answer. The part of a transfer that met it is
    repeated after a wait and the number of requests in flight is halved (``core/pool.py``).

    ``flood`` is set for HTTP 429: that one is not retried in place, it becomes a ``FloodWait``
    the run sits out."""

    def __init__(self, message: str, *, flood: bool = False) -> None:
        super().__init__(message)
        self.flood = flood


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


class RunBusy(StoreError):
    """Another process is running this clone (fresh heartbeat); ``--force-takeover`` overrides."""

    def __init__(self, run_id: int) -> None:
        super().__init__(f"run {run_id} is being run by another process")
        self.run_id = run_id


class AppDataError(TgMirrorError):
    """A problem exporting or importing tgmirror's own state (docs/06-lo-trinh.md, Phase 10)."""


class ExportBusy(AppDataError):
    """A run holds the data right now (fresh heartbeat): a mid-run snapshot moved to another
    machine and run there would send everything twice."""


class AppDataFormatError(AppDataError):
    """The archive is not an export ``tgmirror appdata import`` can read: no manifest, a malformed
    one, or a manifest format version newer than this build knows."""


class AppDataSchemaNewer(AppDataError):
    """The archive's database was written by a newer tgmirror than this one."""

    def __init__(self, found: int, known: int) -> None:
        super().__init__(f"database schema is version {found}, this tgmirror knows {known}")
        self.found = found
        self.known = known


class AppDataChecksumMismatch(AppDataError):
    """A file in the archive does not match the checksum its manifest recorded for it."""

    def __init__(self, entry: str) -> None:
        super().__init__(f"checksum mismatch for {entry}")
        self.entry = entry


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
