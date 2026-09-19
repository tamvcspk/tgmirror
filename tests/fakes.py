"""In-memory ``TelegramGateway`` for tests: no network, scripted failures, call log.

It mimics the parts of Telegram the engine relies on: ascending iteration with ``min_id`` and
server-side narrowing, albums sharing a ``grouped_id``, copy with a result aligned to the ids,
``noforwards`` and posting rights. Flood/peer-flood scenarios are injected with ``fail_next``.
"""

from collections import defaultdict, deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tgmirror.core.auth import AccountInfo
from tgmirror.core.errors import (
    ForwardsRestricted,
    GatewayError,
    InvalidCode,
    InvalidPassword,
    InvalidPhone,
    NoPermission,
    PasswordRequired,
)
from tgmirror.core.gateway import (
    NO_FILTER,
    ChannelInfo,
    ChatKind,
    MediaKind,
    ServerFilter,
    SrcMessage,
    Unit,
)
from tgmirror.ui.prompts import Choice

ACCOUNT = AccountInfo(id=42, name="Test User", username="tester")  # what FakeAuth logs in
MAX_FORWARD_IDS = 100  # Telegram's hard limit per forward call
_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Call:
    method: str
    args: tuple[object, ...]


class FakeGateway:
    def __init__(self) -> None:
        self.channels: dict[int, ChannelInfo] = {}
        self.messages: dict[int, list[SrcMessage]] = defaultdict(list)  # ascending by id
        self.calls: list[Call] = []
        self._failures: dict[str, deque[GatewayError]] = defaultdict(deque)
        self._next_channel_id = -1001000000001
        self._next_msg_id: dict[int, int] = defaultdict(lambda: 1)
        self._next_group_id = 10_000

    # ---- test setup -------------------------------------------------------------------------

    def add_channel(
        self,
        title: str,
        *,
        noforwards: bool = False,
        is_admin: bool = True,
        can_post: bool = True,
        username: str | None = None,
        kind: ChatKind = ChatKind.BROADCAST,
        participants: int | None = None,
    ) -> ChannelInfo:
        info = ChannelInfo(
            id=self._next_channel_id,
            title=title,
            kind=kind,
            username=username,
            participants=participants,
            noforwards=noforwards,
            is_admin=is_admin,
            can_post=can_post,
        )
        self._next_channel_id -= 1
        self.channels[info.id] = info
        return info

    def add_message(
        self,
        channel: int,
        text: str = "",
        *,
        media: MediaKind = MediaKind.TEXT,
        grouped_id: int | None = None,
        is_service: bool = False,
        hashtags: tuple[str, ...] = (),
        size: int | None = None,
        date: datetime | None = None,
    ) -> SrcMessage:
        msg_id = self._alloc_id(channel)
        msg = SrcMessage(
            id=msg_id,
            date=date or _EPOCH + timedelta(minutes=msg_id),
            text=text,
            media=media,
            grouped_id=grouped_id,
            is_service=is_service,
            hashtags=hashtags,
            size=size,
        )
        self.messages[channel].append(msg)
        return msg

    def add_album(
        self, channel: int, kinds: list[MediaKind], caption: str = ""
    ) -> list[SrcMessage]:
        """Consecutive messages sharing one ``grouped_id``; the caption sits on the first."""
        gid = self._alloc_group()
        return [
            self.add_message(channel, caption if i == 0 else "", media=kind, grouped_id=gid)
            for i, kind in enumerate(kinds)
        ]

    def fail_next(self, method: str, error: GatewayError, times: int = 1) -> None:
        """Make the next ``times`` calls of ``method`` raise ``error`` before any side effect."""
        self._failures[method].extend([error] * times)

    def calls_to(self, method: str) -> list[Call]:
        return [c for c in self.calls if c.method == method]

    # ---- TelegramGateway --------------------------------------------------------------------

    async def list_channels(self) -> list[ChannelInfo]:
        self._enter("list_channels")
        return list(self.channels.values())

    async def get_channel(self, ref: int) -> ChannelInfo:
        self._enter("get_channel", ref)
        return self._channel(ref)

    async def create_channel(self, title: str, about: str = "") -> ChannelInfo:
        self._enter("create_channel", title, about)
        return self.add_channel(title)

    async def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        self._enter("iter_messages", src, min_id, filters)
        self._channel(src)
        for msg in list(self.messages[src]):
            if msg.id <= min_id:
                continue
            if filters.max_id is not None and msg.id > filters.max_id:
                break
            if filters.since is not None and msg.date < filters.since:
                continue
            if filters.media is not None and msg.media != filters.media:
                continue
            if filters.search is not None and filters.search.lower() not in msg.text.lower():
                continue
            yield msg

    async def copy_messages(self, src: int, dst: int, ids: list[int]) -> list[int | None]:
        self._enter("copy_messages", src, dst, list(ids))
        if not ids or len(ids) > MAX_FORWARD_IDS:
            raise ValueError(f"copy_messages takes 1..{MAX_FORWARD_IDS} ids, got {len(ids)}")
        source, target = self._channel(src), self._channel(dst)
        if source.noforwards:
            raise ForwardsRestricted(f"channel {src} restricts saving content")
        if not target.can_post:
            raise NoPermission(f"cannot post to channel {dst}")

        by_id = {m.id: m for m in self.messages[src]}
        new_groups: dict[int, int] = {}  # source grouped_id -> destination grouped_id
        results: list[int | None] = []
        for msg_id in ids:
            msg = by_id.get(msg_id)
            if msg is None or msg.is_service:
                results.append(None)  # deleted or not forwardable: outcome unknown to the caller
                continue
            gid = msg.grouped_id
            if gid is not None:
                gid = new_groups.setdefault(gid, self._alloc_group())
            new_id = self._alloc_id(dst)
            self.messages[dst].append(replace(msg, id=new_id, grouped_id=gid))
            results.append(new_id)
        return results

    async def reupload(self, src: int, dst: int, unit: Unit, tmp: Path) -> list[int]:
        self._enter("reupload", src, dst, unit.ids, tmp)
        self._channel(src)
        target = self._channel(dst)
        if not target.can_post:
            raise NoPermission(f"cannot post to channel {dst}")
        gid = self._alloc_group() if unit.is_album else None
        new_ids: list[int] = []
        for msg in unit.messages:
            new_id = self._alloc_id(dst)
            self.messages[dst].append(replace(msg, id=new_id, grouped_id=gid))
            new_ids.append(new_id)
        return new_ids

    # ---- internals --------------------------------------------------------------------------

    def _enter(self, method: str, *args: object) -> None:
        self.calls.append(Call(method, args))
        if self._failures[method]:
            raise self._failures[method].popleft()

    def _channel(self, ref: int) -> ChannelInfo:
        try:
            return self.channels[ref]
        except KeyError:
            raise NoPermission(f"channel {ref} is not accessible") from None

    def _alloc_id(self, channel: int) -> int:
        msg_id = self._next_msg_id[channel]
        self._next_msg_id[channel] += 1
        return msg_id

    def _alloc_group(self) -> int:
        self._next_group_id += 1
        return self._next_group_id


class FakeAuth:
    """In-memory ``TelegramAuth``. ``code``/``password`` are what Telegram would accept.

    ``fail_next`` scripts errors: it makes the next ``times`` calls of a method raise ``error``.
    """

    def __init__(
        self,
        *,
        logged_in: AccountInfo | None = None,
        code: str = "12345",
        password: str | None = None,
    ) -> None:
        self.current = logged_in
        self.code = code
        self.password = password  # set => two-step verification is on
        self.calls: list[str] = []
        self.seen_secrets: list[str] = []  # everything the flow sent us, to assert nothing leaks
        self._failures: dict[str, deque[GatewayError]] = defaultdict(deque)
        self._phone: str | None = None
        self._code_expired = False

    def fail_next(self, method: str, error: GatewayError, times: int = 1) -> None:
        self._failures[method].extend([error] * times)

    def _enter(self, method: str) -> None:
        self.calls.append(method)
        if self._failures[method]:
            raise self._failures[method].popleft()

    async def account(self) -> AccountInfo | None:
        self._enter("account")
        return self.current

    async def request_code(self, phone: str) -> None:
        self._enter("request_code")
        if not phone.startswith("+") or not phone[1:].isdigit():
            raise InvalidPhone("bad phone")
        self._phone = phone
        self.seen_secrets.append(phone)

    async def sign_in_code(self, phone: str, code: str) -> None:
        self._enter("sign_in_code")
        self.seen_secrets.append(code)
        if phone != self._phone:
            raise InvalidPhone("no code requested for this phone")
        if code != self.code:
            raise InvalidCode("wrong code")
        if self.password is not None:
            raise PasswordRequired("2FA")
        self.current = ACCOUNT

    async def sign_in_password(self, password: str) -> None:
        self._enter("sign_in_password")
        self.seen_secrets.append(password)
        if password != self.password:
            raise InvalidPassword("wrong password")
        self.current = ACCOUNT

    async def log_out(self) -> None:
        self._enter("log_out")
        self.current = None


class ScriptedPrompter:
    """``Prompter`` that answers from queues and records every question.

    Answers are consumed in order per kind. ``select`` answers are matched by a substring of the
    choice label so tests read like a user's clicks; a missing answer fails the test loudly.
    """

    def __init__(
        self,
        *,
        text: Sequence[str] = (),
        secret: Sequence[str] = (),
        confirm: Sequence[bool] = (),
        select: Sequence[str] = (),
    ) -> None:
        self._text = deque(text)
        self._secret = deque(secret)
        self._confirm = deque(confirm)
        self._select = deque(select)
        self.asked: list[tuple[str, str]] = []  # (kind, message)
        self.said: list[str] = []
        self.select_labels: list[list[str]] = []

    def say(self, message: str) -> None:
        self.said.append(message)

    async def text(self, message: str, default: str = "") -> str:
        self.asked.append(("text", message))
        return self._text.popleft() if self._text else default

    async def secret(self, message: str) -> str:
        self.asked.append(("secret", message))
        return self._secret.popleft()

    async def confirm(self, message: str, default: bool = True) -> bool:
        self.asked.append(("confirm", message))
        return self._confirm.popleft() if self._confirm else default

    async def select(self, message: str, choices: Sequence[Choice[Any]]) -> Any:
        self.asked.append(("select", message))
        self.select_labels.append([c.label for c in choices])
        wanted = self._select.popleft()
        for choice in choices:
            if wanted in choice.label:
                return choice.value
        raise AssertionError(f"no choice containing {wanted!r} in {[c.label for c in choices]}")
