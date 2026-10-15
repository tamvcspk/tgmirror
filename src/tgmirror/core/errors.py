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

    All four kinds are handled the same way; ``slow_mode``, ``premium`` and ``transport`` only
    tell them apart in ``flood_log``. ``premium`` is FLOOD_PREMIUM_WAIT_x: the wait a transfer
    gets on an account without Telegram Premium (phase 19d1). ``transport`` is not a wait
    Telegram named: it is the HTTP 429 the
    server sent on the connection while files were moving (spike 12, and the first real run),
    and ``seconds`` is how long we choose to rest.
    """

    def __init__(
        self,
        seconds: int,
        *,
        slow_mode: bool = False,
        transport: bool = False,
        premium: bool = False,
    ) -> None:
        label = (
            "TRANSPORT_429"
            if transport
            else "SLOWMODE_WAIT"
            if slow_mode
            else "FLOOD_PREMIUM_WAIT"
            if premium
            else "FLOOD_WAIT"
        )
        super().__init__(f"{label} {seconds}s")
        self.seconds = seconds
        self.slow_mode = slow_mode
        self.transport = transport
        self.premium = premium


class PeerFlood(GatewayError):
    """PEER_FLOOD: the account is flagged for spam-like behaviour. Never retried."""


class BudgetReached(TgMirrorError):
    """A lane's learned budget is used up: not a failure, the run rests until ``resume_at``, when
    the sliding window has let enough of what it sent go (phase 19d2).

    It is our own budget (docs/05-chong-flood.md), learned from a long FloodWait, not something
    Telegram said now, so it is not a ``GatewayError``. A run parked by it keeps the stored
    ``fail_reason`` ``daily_cap`` of the days when it was the fixed daily cap.
    """

    def __init__(
        self,
        resume_at: datetime,
        sent: int,
        budget: int,
        window_hours: int,
        action: str = "send",
        dst: int = 0,
    ) -> None:
        super().__init__(
            f"budget of {budget} messages per {window_hours} h reached on {action} ({sent} sent)"
        )
        self.resume_at = resume_at
        self.sent = sent
        self.budget = budget
        self.window_hours = window_hours
        self.action = action
        self.dst = dst


class NoPermission(GatewayError):
    """Missing rights on a channel (cannot post, not admin, channel not accessible)."""


class ForwardsRestricted(GatewayError):
    """CHAT_FORWARDS_RESTRICTED: the source has "Restrict saving content" on (decision D3)."""


class FileRefExpired(GatewayError):
    """The media cannot be sent again by its id: its file reference expired (refetch the
    message and retry once) or Telegram will not reuse it (send it the long way)."""


class Transient(GatewayError):
    """Connection-level problem that is expected to go away; retried with backoff."""


class ServerFailed(Transient):
    """Telegram answered one request with an internal error (``RPC_CALL_FAIL``, ...) every time
    Telethon repeated it, until Telethon gave up with a bare ``ValueError``. A ``Transient`` — a
    later try may work — but not the network's fault, and not always a passing one: a request
    that is too heavy for the server (a count over a huge topic) fails the same way again."""


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


class BackupBusy(StoreError):
    """Another process is backing up this same directory (fresh heartbeat); ``--force-takeover``
    overrides (phase 11)."""

    def __init__(self, backup_id: int) -> None:
        super().__init__(f"backup {backup_id} is being run by another process")
        self.backup_id = backup_id


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
