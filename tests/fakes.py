"""In-memory ``TelegramGateway`` for tests: no network, scripted failures, call log.

It mimics the parts of Telegram the engine relies on: ascending iteration with ``min_id`` and
server-side narrowing, albums sharing a ``grouped_id``, copy with a result aligned to the ids,
``noforwards`` and posting rights. Flood/peer-flood scenarios are injected with ``fail_next``.
"""

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import keyring.backend

from tgmirror.core.auth import AccountInfo
from tgmirror.core.errors import (
    ForwardsRestricted,
    GatewayError,
    InvalidCode,
    InvalidPassword,
    InvalidPhone,
    NoPermission,
    PasswordRequired,
    PerMessage,
)
from tgmirror.core.gateway import (
    ALBUM_MARGIN,
    MAX_IDS_PER_CALL,
    NO_FILTER,
    CaptionMode,
    CaptionPolicy,
    ChannelInfo,
    ChatKind,
    ExportedMedia,
    ExportedMessage,
    MediaKind,
    OnTransfer,
    Prepared,
    ServerFilter,
    SrcMessage,
    TopicInfo,
    TransferPhase,
    Unit,
)
from tgmirror.ui.prompts import Choice

ACCOUNT = AccountInfo(id=42, name="Test User", username="tester")  # what FakeAuth logs in
MAX_FORWARD_IDS = MAX_IDS_PER_CALL  # Telegram's hard limit per forward call
_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Call:
    method: str
    args: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _ExportExtra:
    """Poll/geo/contact data ``SrcMessage`` has no room for (phase 11, backup): set through
    ``add_message`` and read back only by ``export_unit``, exactly like the real gateway reads it
    from the Telethon message it re-reads."""

    poll_options: tuple[str, ...] = ()
    poll_quiz: bool = False
    poll_correct_option: int | None = None
    geo: tuple[float, float] | None = None
    venue_title: str | None = None
    contact: tuple[str, str, str] | None = None  # phone, first name, last name


class FakeGateway:
    def __init__(self) -> None:
        self.channels: dict[int, ChannelInfo] = {}
        self.messages: dict[int, list[SrcMessage]] = defaultdict(list)  # ascending by id
        self.calls: list[Call] = []
        self._failures: dict[str, deque[GatewayError]] = defaultdict(deque)
        self._poisoned: dict[tuple[int, int], str] = {}
        self._next_channel_id = -1001000000001
        self._next_msg_id: dict[int, int] = defaultdict(lambda: 1)
        self._next_group_id = 10_000
        self.topics: dict[int, list[TopicInfo]] = {}  # channel -> topics (General is implicit)
        self._next_topic_id: dict[int, int] = defaultdict(lambda: 2)  # 1 is always General
        self._export_extra: dict[tuple[int, int], _ExportExtra] = {}  # (channel, msg_id) -> extra

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

    def add_topic(self, channel: int, title: str, *, closed: bool = False) -> TopicInfo:
        """A forum topic besides the implicit General (id 1)."""
        topic = TopicInfo(id=self._next_topic_id[channel], title=title, closed=closed)
        self._next_topic_id[channel] += 1
        self.topics.setdefault(channel, []).append(topic)
        return topic

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
        duration: float | None = None,
        mime: str | None = None,
        views: int | None = None,
        date: datetime | None = None,
        quiz_unanswered: bool = False,
        title: str | None = None,
        topic_id: int | None = None,
        from_user_id: int | None = None,
        poll_options: tuple[str, ...] = (),
        poll_quiz: bool = False,
        poll_correct_option: int | None = None,
        geo: tuple[float, float] | None = None,
        venue_title: str | None = None,
        contact: tuple[str, str, str] | None = None,
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
            duration=duration,
            mime=mime,
            views=views,
            quiz_unanswered=quiz_unanswered,
            title=title,
            topic_id=topic_id,
            from_user_id=from_user_id,
        )
        self.messages[channel].append(msg)
        if poll_options or geo is not None or contact is not None:
            self._export_extra[(channel, msg_id)] = _ExportExtra(
                poll_options, poll_quiz, poll_correct_option, geo, venue_title, contact
            )
        return msg

    def add_album(
        self,
        channel: int,
        kinds: list[MediaKind],
        caption: str = "",
        *,
        topic_id: int | None = None,
    ) -> list[SrcMessage]:
        """Consecutive messages sharing one ``grouped_id``; the caption sits on the first."""
        gid = self._alloc_group()
        return [
            self.add_message(
                channel, caption if i == 0 else "", media=kind, grouped_id=gid, topic_id=topic_id
            )
            for i, kind in enumerate(kinds)
        ]

    def fail_next(self, method: str, error: GatewayError, times: int = 1) -> None:
        """Make the next ``times`` calls of ``method`` raise ``error`` before any side effect."""
        self._failures[method].extend([error] * times)

    def poison(self, channel: int, msg_id: int, reason: str = "MESSAGE_ID_INVALID") -> None:
        """A ``copy_messages`` whose ids include this one raises ``PerMessage`` (no side effect)."""
        self._poisoned[(channel, msg_id)] = reason

    def heal(self, channel: int, msg_id: int) -> None:
        """Undo ``poison``: Telegram accepts this message from now on."""
        self._poisoned.pop((channel, msg_id), None)

    def calls_to(self, method: str) -> list[Call]:
        return [c for c in self.calls if c.method == method]

    # ---- TelegramGateway --------------------------------------------------------------------

    async def list_channels(self) -> list[ChannelInfo]:
        self._enter("list_channels")
        return list(self.channels.values())

    async def get_channel(self, ref: int) -> ChannelInfo:
        self._enter("get_channel", ref)
        return self._channel(ref)

    async def create_channel(
        self, title: str, about: str = "", kind: ChatKind = ChatKind.BROADCAST
    ) -> ChannelInfo:
        self._enter("create_channel", title, about, kind)
        return self.add_channel(title, kind=kind)

    async def list_topics(self, src: int) -> list[TopicInfo]:
        self._enter("list_topics", src)
        self._channel(src)
        general = TopicInfo(id=1, title="General")
        return [general, *self.topics.get(src, [])]

    async def create_topic(self, dst: int, title: str) -> int:
        self._enter("create_topic", dst, title)
        if not self._channel(dst).can_post:
            raise NoPermission(f"cannot post to channel {dst}")
        return self.add_topic(dst, title).id

    async def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        self._enter("iter_messages", src, min_id, filters)
        for msg in self._selected(src, min_id, filters):
            yield msg

    async def count(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int:
        """What ``iter_messages`` would return, counted (service messages too, like Telegram)."""
        self._enter("count", src, min_id, filters)
        return len(self._selected(src, min_id, filters))

    def _selected(self, src: int, min_id: int, filters: ServerFilter) -> list[SrcMessage]:
        self._channel(src)
        history = list(self.messages[src])
        max_id = filters.max_id
        # Like the real gateway: dates become positions, with a margin so that an album on the
        # boundary is returned whole.
        if filters.since is not None:
            first = next((m.id for m in history if m.date >= filters.since), None)
            if first is None:
                return []
            min_id = max(min_id, first - 1 - ALBUM_MARGIN)
        if filters.until is not None:
            past = next((m.id for m in history if m.date >= filters.until), None)
            if past is not None:
                bound = past + ALBUM_MARGIN
                max_id = bound if max_id is None else min(max_id, bound)
        found: list[SrcMessage] = []
        for msg in history:
            if msg.id <= min_id:
                continue
            if max_id is not None and msg.id > max_id:
                break
            if filters.media is not None and msg.media != filters.media:
                continue
            if filters.search is not None and filters.search.lower() not in msg.text.lower():
                continue
            found.append(msg)
        return found

    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]:
        self._enter("get_messages", src, list(ids))
        if not ids or len(ids) > MAX_IDS_PER_CALL:
            raise ValueError(f"get_messages takes 1..{MAX_IDS_PER_CALL} ids, got {len(ids)}")
        self._channel(src)
        wanted = set(ids)
        return [m for m in self.messages[src] if m.id in wanted]

    def delete_message(self, channel: int, msg_id: int) -> None:
        """The message disappears from the channel (a later read no longer finds it)."""
        self.messages[channel] = [m for m in self.messages[channel] if m.id != msg_id]

    async def last_message_id(self, chat: int) -> int:
        self._enter("last_message_id", chat)
        self._channel(chat)
        return self.messages[chat][-1].id if self.messages[chat] else 0

    async def copy_messages(
        self, src: int, dst: int, ids: list[int], *, topic: int | None = None
    ) -> list[int | None]:
        self._enter("copy_messages", src, dst, list(ids), topic)
        if not ids or len(ids) > MAX_FORWARD_IDS:
            raise ValueError(f"copy_messages takes 1..{MAX_FORWARD_IDS} ids, got {len(ids)}")
        source, target = self._channel(src), self._channel(dst)
        if source.noforwards:
            raise ForwardsRestricted(f"channel {src} restricts saving content")
        if not target.can_post:
            raise NoPermission(f"cannot post to channel {dst}")
        for msg_id in ids:
            if (reason := self._poisoned.get((src, msg_id))) is not None:
                raise PerMessage(reason)

        by_id = {m.id: m for m in self.messages[src]}
        new_groups: dict[int, int] = {}  # source grouped_id -> destination grouped_id
        results: list[int | None] = []
        for msg_id in ids:
            msg = by_id.get(msg_id)
            if msg is None or msg.is_service:
                results.append(None)  # deleted or not forwardable: Telegram makes no message for it
                continue
            gid = msg.grouped_id
            if gid is not None:
                gid = new_groups.setdefault(gid, self._alloc_group())
            new_id = self._alloc_id(dst)
            self.messages[dst].append(replace(msg, id=new_id, grouped_id=gid, topic_id=topic))
            results.append(new_id)
        return results

    async def prepare(
        self, src: int, unit: Unit, tmp: Path, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        """Reads the unit again and "downloads" its media: one small file per message that has
        some, so a test can see that the engine keeps them until the unit is sent and then removes
        them. It reports each file to ``on_transfer`` (started, then done)."""
        self._enter("prepare", src, unit.ids, tmp)
        self._channel(src)
        have = {m.id for m in self.messages[src]}
        if any(i not in have for i in unit.ids):
            raise PerMessage("gone_from_source")
        files: list[Path] = []
        for msg in unit.messages:
            if msg.media in _NO_FILE:
                continue
            total = msg.size or 1
            if on_transfer is not None:
                on_transfer(TransferPhase.DOWNLOAD, msg.id, 0, total)
            files.append(await asyncio.to_thread(_download, tmp, msg.id))
            if on_transfer is not None:
                on_transfer(TransferPhase.DOWNLOAD, msg.id, total, total)
        return Prepared(unit, tuple(files))

    async def export_unit(
        self, src: int, unit: Unit, media_dir: Path, on_transfer: OnTransfer | None = None
    ) -> list[ExportedMessage]:
        """As ``prepare``, but returns self-contained records instead of a ``Prepared`` (phase 11,
        backup): downloaded files are named by message id, like the real gateway, and poll/geo/
        contact data comes from ``_export_extra`` (``add_message``'s extra keyword arguments)."""
        self._enter("export_unit", src, unit.ids, media_dir)
        self._channel(src)
        have = {m.id for m in self.messages[src]}
        if any(i not in have for i in unit.ids):
            raise PerMessage("gone_from_source")
        out: list[ExportedMessage] = []
        for msg in unit.messages:
            media: ExportedMedia | None = None
            if msg.media not in _NO_FILE:
                total = msg.size or 1
                if on_transfer is not None:
                    on_transfer(TransferPhase.DOWNLOAD, msg.id, 0, total)
                path = await asyncio.to_thread(_download, media_dir, msg.id)
                if on_transfer is not None:
                    on_transfer(TransferPhase.DOWNLOAD, msg.id, total, total)
                media = ExportedMedia(
                    kind=msg.media,
                    filename=path.name,
                    mime=msg.mime,
                    size=msg.size,
                    duration=msg.duration,
                )
            else:
                extra = self._export_extra.get((src, msg.id))
                if msg.media is MediaKind.POLL:
                    media = ExportedMedia(
                        kind=MediaKind.POLL,
                        poll_question=msg.title,
                        poll_options=extra.poll_options if extra else (),
                        poll_quiz=extra.poll_quiz if extra else False,
                        poll_correct_option=extra.poll_correct_option if extra else None,
                    )
                elif msg.media is MediaKind.GEO and extra is not None and extra.geo is not None:
                    media = ExportedMedia(
                        kind=MediaKind.GEO,
                        geo_lat=extra.geo[0],
                        geo_lon=extra.geo[1],
                        venue_title=extra.venue_title,
                    )
                elif msg.media is MediaKind.CONTACT and extra is not None and extra.contact:
                    phone, first, last = extra.contact
                    media = ExportedMedia(
                        kind=MediaKind.CONTACT,
                        contact_phone=phone,
                        contact_first_name=first,
                        contact_last_name=last,
                    )
                elif msg.media in (MediaKind.GAME, MediaKind.INVOICE):
                    media = ExportedMedia(kind=msg.media, title=msg.title)
            out.append(
                ExportedMessage(
                    id=msg.id,
                    date=msg.date,
                    grouped_id=msg.grouped_id,
                    topic_id=msg.topic_id,
                    from_user_id=msg.from_user_id,
                    text_html=msg.text,
                    views=msg.views,
                    media=media,
                )
            )
        return out

    async def upload_prepared(
        self, prepared: Prepared, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        """Uploads the bytes ahead of the post: no message is created. A unit with nothing to
        upload is returned as it is."""
        self._enter("upload_prepared", prepared.unit.ids)
        if not prepared.files or prepared.uploaded:
            return prepared
        if on_transfer is not None:
            size = sum(m.size or 1 for m in prepared.unit.messages)
            first = prepared.unit.messages[0].id
            on_transfer(TransferPhase.UPLOAD, first, 0, size)
            on_transfer(TransferPhase.UPLOAD, first, size, size)
        return Prepared(prepared.unit, prepared.files, prepared.handle, uploaded=True)

    async def send_prepared(
        self,
        dst: int,
        prepared: Prepared,
        caption: CaptionPolicy,
        on_transfer: OnTransfer | None = None,
        *,
        topic: int | None = None,
    ) -> list[int]:
        self._enter("send_prepared", dst, prepared.unit.ids, caption, topic)
        target = self._channel(dst)
        if not target.can_post:
            raise NoPermission(f"cannot post to channel {dst}")
        missing = [p for p in prepared.files if not p.exists()]
        assert not missing, f"the engine removed downloads before the unit was sent: {missing}"
        gid = self._alloc_group() if prepared.unit.is_album else None
        new_ids: list[int] = []
        if on_transfer is not None and prepared.files and not prepared.uploaded:
            size = sum(m.size or 1 for m in prepared.unit.messages)
            first = prepared.unit.messages[0].id
            on_transfer(TransferPhase.UPLOAD, first, 0, size)
            on_transfer(TransferPhase.UPLOAD, first, size, size)
        texts = _rewrite_unit(prepared.unit, caption)
        for msg, text in zip(prepared.unit.messages, texts, strict=True):
            new_id = self._alloc_id(dst)
            self.messages[dst].append(
                replace(msg, id=new_id, grouped_id=gid, text=text, topic_id=topic)
            )
            new_ids.append(new_id)
        return new_ids

    async def fetch(self, src: int, unit: Unit) -> Prepared:
        """Reads the unit again for sending by reference: nothing is downloaded."""
        self._enter("fetch", src, unit.ids)
        self._channel(src)
        have = {m.id for m in self.messages[src]}
        if any(i not in have for i in unit.ids):
            raise PerMessage("gone_from_source")
        return Prepared(unit)

    async def send_by_reference(
        self, dst: int, prepared: Prepared, caption: CaptionPolicy, *, topic: int | None = None
    ) -> list[int]:
        """Posts the unit by the ids of its files. ``noforwards`` is not checked on purpose: the
        engine must never ask for this on a protected source, and a test can see that it did not."""
        self._enter("send_by_reference", dst, prepared.unit.ids, caption, topic)
        if not self._channel(dst).can_post:
            raise NoPermission(f"cannot post to channel {dst}")
        gid = self._alloc_group() if prepared.unit.is_album else None
        new_ids: list[int] = []
        texts = _rewrite_unit(prepared.unit, caption)
        for msg, text in zip(prepared.unit.messages, texts, strict=True):
            new_id = self._alloc_id(dst)
            self.messages[dst].append(
                replace(msg, id=new_id, grouped_id=gid, text=text, topic_id=topic)
            )
            new_ids.append(new_id)
        return new_ids

    async def send_text(self, dst: int, text: str, *, topic: int | None = None) -> int:
        self._enter("send_text", dst, text, topic)
        if not self._channel(dst).can_post:
            raise NoPermission(f"cannot post to channel {dst}")
        new_id = self._alloc_id(dst)
        date = _EPOCH + timedelta(minutes=new_id)
        self.messages[dst].append(SrcMessage(id=new_id, date=date, text=text, topic_id=topic))
        return new_id

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


# media without a file to download: the message itself carries them (or there is nothing)
_NO_FILE = frozenset(
    {
        MediaKind.TEXT,
        MediaKind.WEBPAGE,
        MediaKind.POLL,
        MediaKind.GEO,
        MediaKind.CONTACT,
        MediaKind.GAME,
        MediaKind.INVOICE,
    }
)

# Self-contained media has no caption slot at all: a topic hashtag (phase 8) never applies to it,
# unlike plain text/webpage, which have no *file* but do have a caption-like slot of their own.
_SELF_CONTAINED = frozenset(
    {MediaKind.POLL, MediaKind.GEO, MediaKind.CONTACT, MediaKind.GAME, MediaKind.INVOICE}
)


def _download(tmp: Path, msg_id: int) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / str(msg_id)
    path.write_bytes(b"x")
    return path


def _rewrite(msg: SrcMessage, policy: CaptionPolicy) -> str:
    """The caption after ``policy``; only the caption of a media message is ever rewritten. A
    topic hashtag (phase 8) is appended last, to any text with a slot for it (a caption, or plain
    text/webpage) but never to self-contained media, which has none."""
    if msg.media in _SELF_CONTAINED:
        return msg.text
    if msg.media in _NO_FILE:  # plain text/webpage: the mode never touches it, the hashtag still
        text = msg.text
    else:
        match policy.mode:
            case CaptionMode.NONE:
                text = ""
            case CaptionMode.APPEND:
                text = f"{msg.text}\n\n{policy.text}" if msg.text else msg.text
            case CaptionMode.STRIP_LINKS:
                text = " ".join(w for w in msg.text.split(" ") if "t.me/" not in w)
            case _:
                text = msg.text
    if policy.hashtag:
        text = f"{text}\n{policy.hashtag}" if text else policy.hashtag
    return text


def _rewrite_unit(unit: Unit, policy: CaptionPolicy) -> list[str]:
    """``_rewrite`` for each message of a unit; an album gets the topic hashtag once, on its first
    caption left (or its first item), like the real gateway's ``_album_captions``."""
    if not unit.is_album or not policy.hashtag:
        return [_rewrite(m, policy) for m in unit.messages]
    texts = [_rewrite(m, replace(policy, hashtag=None)) for m in unit.messages]
    at = next((i for i, text in enumerate(texts) if text), 0)
    texts[at] = f"{texts[at]}\n{policy.hashtag}" if texts[at] else policy.hashtag
    return texts


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
        checkbox: Sequence[Sequence[str]] = (),
    ) -> None:
        self._text = deque(text)
        self._secret = deque(secret)
        self._confirm = deque(confirm)
        self._select = deque(select)
        self._checkbox = deque(checkbox)
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

    async def checkbox(self, message: str, choices: Sequence[Choice[Any]]) -> list[Any]:
        """Ticks the choices whose label is in the next queued list (none when the queue is dry)."""
        self.asked.append(("checkbox", message))
        wanted = self._checkbox.popleft() if self._checkbox else ()
        unknown = set(wanted) - {c.label for c in choices}
        if unknown:
            raise AssertionError(
                f"no checkbox choice {sorted(unknown)} in {[c.label for c in choices]}"
            )
        return [c.value for c in choices if c.label in wanted]


class FakeKeyring(keyring.backend.KeyringBackend):
    """In-memory keyring backend (docs/06-lo-trinh.md, Phase 9): a real, usable backend as far as
    ``core.secrets`` can tell, so tests exercise the keyring branch without ever touching the
    machine's real Credential Manager/Keychain/Secret Service. Installed for every test by the
    ``fake_keyring`` fixture in ``conftest.py``."""

    priority = 1

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)
