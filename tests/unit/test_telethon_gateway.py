"""The Telethon boundary, tested without a network: real Telethon types, a stub client."""

from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telethon import errors, types
from telethon.tl.functions.channels import CreateChannelRequest, ToggleForumRequest
from telethon.tl.functions.messages import CreateForumTopicRequest, GetForumTopicsRequest

from tgmirror.core.auth import TelegramAuth
from tgmirror.core.errors import (
    BadApiCredentials,
    CodeExpired,
    FileRefExpired,
    FloodWait,
    ForwardsRestricted,
    GatewayError,
    InvalidCode,
    InvalidPassword,
    InvalidPhone,
    NoPermission,
    NotLoggedIn,
    PasswordRequired,
    PeerFlood,
    TooManyChannels,
    Transient,
)
from tgmirror.core.gateway import ChatKind, TelegramGateway
from tgmirror.core.telethon_gateway import (
    TelethonAuth,
    TelethonGateway,
    channel_info,
    make_client,
    map_exception,
    mapped_errors,
)

PHOTO = types.ChatPhotoEmpty()
RIGHTS_POST = types.ChatAdminRights(post_messages=True)
RIGHTS_NO_POST = types.ChatAdminRights(delete_messages=True)


def channel(**kw: Any) -> types.Channel:
    return types.Channel(
        id=kw.pop("id", 123), title=kw.pop("title", "T"), photo=PHOTO, date=None, **kw
    )


# ---- error mapping --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (errors.FloodWaitError(None, capture=30), FloodWait),
        (errors.SlowModeWaitError(None, capture=15), FloodWait),
        (errors.PeerFloodError(None), PeerFlood),
        (errors.PhoneCodeInvalidError(None), InvalidCode),
        (errors.PhoneCodeEmptyError(None), InvalidCode),
        (errors.PhoneCodeExpiredError(None), CodeExpired),
        (errors.SessionPasswordNeededError(None), PasswordRequired),
        (errors.PasswordHashInvalidError(None), InvalidPassword),
        (errors.PhoneNumberInvalidError(None), InvalidPhone),
        (errors.PhoneNumberBannedError(None), InvalidPhone),
        (errors.ApiIdInvalidError(None), BadApiCredentials),
        (errors.AuthKeyUnregisteredError(None), NotLoggedIn),
        (errors.SessionRevokedError(None), NotLoggedIn),
        (errors.UserDeactivatedBanError(None), NotLoggedIn),
        (errors.ChatWriteForbiddenError(None), NoPermission),
        (errors.ChatAdminRequiredError(None), NoPermission),
        (errors.ChannelPrivateError(None), NoPermission),
        (errors.ChatForwardsRestrictedError(None), ForwardsRestricted),
        (errors.FileReferenceExpiredError(None), FileRefExpired),
        (errors.ChannelsTooMuchError(None), TooManyChannels),
        (ConnectionResetError(), Transient),
        (TimeoutError(), Transient),
        (errors.ServerError(None, "INTERNAL", 500), Transient),
        (errors.RPCError(None, "SOMETHING_NEW", 400), GatewayError),
    ],
)
def test_map_exception(raised: BaseException, expected: type[GatewayError]) -> None:
    mapped = map_exception(raised)

    assert type(mapped) is expected


def test_flood_wait_keeps_its_seconds() -> None:
    mapped = map_exception(errors.FloodWaitError(None, capture=42))

    assert isinstance(mapped, FloodWait) and mapped.seconds == 42


def test_unknown_rpc_error_is_named_by_telegram_string() -> None:
    mapped = map_exception(errors.RPCError(None, "SOMETHING_NEW", 400))

    assert mapped is not None and "SOMETHING_NEW" in str(mapped)


def test_unrelated_exceptions_are_not_mapped() -> None:
    assert map_exception(ValueError("bug")) is None


def test_mapped_errors_chains_the_original() -> None:
    original = errors.PeerFloodError(None)

    with pytest.raises(PeerFlood) as caught, mapped_errors():
        raise original

    assert caught.value.__cause__ is original


def test_mapped_errors_passes_unrelated_exceptions_through() -> None:
    with pytest.raises(ValueError), mapped_errors():
        raise ValueError("bug")


# ---- entities -> ChannelInfo ----------------------------------------------------------------


def test_broadcast_creator() -> None:
    info = channel_info(
        channel(broadcast=True, creator=True, noforwards=True, participants_count=9)
    )

    assert info is not None
    assert info.id == -1000000000123  # marked id, what users and the session cache use
    assert (info.kind, info.is_admin, info.can_post, info.noforwards) == (
        ChatKind.BROADCAST,
        True,
        True,
        True,
    )
    assert info.participants == 9


def test_broadcast_admin_needs_post_messages_right() -> None:
    can = channel_info(channel(broadcast=True, admin_rights=RIGHTS_POST))
    cannot = channel_info(channel(broadcast=True, admin_rights=RIGHTS_NO_POST))

    assert (can.is_admin, can.can_post) == (True, True)  # type: ignore[union-attr]
    assert (cannot.is_admin, cannot.can_post) == (True, False)  # type: ignore[union-attr]


def test_broadcast_subscriber_cannot_post() -> None:
    info = channel_info(channel(broadcast=True))

    assert info is not None and (info.is_admin, info.can_post) == (False, False)


def test_supergroup_member_can_post_unless_restricted() -> None:
    open_group = channel_info(channel(megagroup=True))
    muted = channel_info(
        channel(
            megagroup=True, default_banned_rights=types.ChatBannedRights(None, send_messages=True)
        )
    )

    assert open_group is not None and open_group.kind is ChatKind.SUPERGROUP
    assert (open_group.is_admin, open_group.can_post) == (False, True)
    assert muted is not None and muted.can_post is False


def test_supergroup_admin_posts_despite_default_restrictions() -> None:
    info = channel_info(
        channel(
            megagroup=True,
            admin_rights=RIGHTS_NO_POST,
            default_banned_rights=types.ChatBannedRights(None, send_messages=True),
        )
    )

    assert info is not None and (info.is_admin, info.can_post) == (True, True)


def test_expired_restriction_no_longer_blocks_posting() -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    future = datetime.now(UTC) + timedelta(days=1)

    expired = channel_info(
        channel(megagroup=True, banned_rights=types.ChatBannedRights(past, send_messages=True))
    )
    active = channel_info(
        channel(megagroup=True, banned_rights=types.ChatBannedRights(future, send_messages=True))
    )

    assert expired is not None and expired.can_post is True
    assert active is not None and active.can_post is False


def test_forum_and_basic_group_kinds() -> None:
    forum = channel_info(channel(megagroup=True, forum=True))
    group = channel_info(
        types.Chat(
            id=5, title="G", photo=PHOTO, participants_count=3, date=None, version=1, creator=True
        )
    )

    assert forum is not None and forum.kind is ChatKind.FORUM
    assert group is not None and group.kind is ChatKind.GROUP
    assert group.id == -5 and group.is_admin and group.can_post


def test_username_falls_back_to_active_extra_username() -> None:
    usernames = [
        types.Username("old", editable=False, active=False),
        types.Username("new", False, True),
    ]

    info = channel_info(channel(broadcast=True, usernames=usernames))

    assert info is not None and info.username == "new"


def test_things_that_are_not_clonable_chats_are_skipped() -> None:
    left = channel(broadcast=True, left=True)
    migrated = types.Chat(
        id=5, title="old", photo=PHOTO, participants_count=1, date=None, version=1, deactivated=True
    )
    user = types.User(id=1)
    neither = channel()  # neither broadcast nor megagroup

    assert [channel_info(x) for x in (left, migrated, user, neither)] == [None] * 4


# ---- client, auth and gateway on a stub client ----------------------------------------------


class StubClient:
    """Just enough of ``TelegramClient`` for the calls the gateway and auth make."""

    def __init__(
        self,
        *,
        dialogs: list[Any] | None = None,
        result: Any = None,
        entity_result: Any = None,
    ) -> None:
        self.dialogs = dialogs or []
        self.result = result
        # ``get_entity`` normally answers the same ``result``; a kind that re-fetches after
        # creating (forum) needs a different one (phase 8).
        self.entity_result = entity_result
        self.requests: list[Any] = []
        self.raises: BaseException | None = None
        self.authorized = True
        self.signed_in: list[tuple[Any, ...]] = []
        # a call answers each of these in order (list_topics pages), then falls back to ``result``
        self.results: deque[Any] = deque()

    async def iter_dialogs(self) -> AsyncIterator[Any]:
        for dialog in self.dialogs:
            yield dialog

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if self.raises:
            raise self.raises
        return self.results.popleft() if self.results else self.result

    async def get_entity(self, ref: int) -> Any:
        if self.raises:
            raise self.raises
        return self.result if self.entity_result is None else self.entity_result

    async def get_input_entity(self, ref: int) -> Any:
        if self.raises:
            raise self.raises
        return f"peer:{ref}"

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_me(self) -> Any:
        return SimpleNamespace(id=7, first_name="Ada", last_name="L", username=None, phone="+100")

    async def send_code_request(self, phone: str) -> None:
        if self.raises:
            raise self.raises

    async def sign_in(self, *args: Any, **kwargs: Any) -> None:
        self.signed_in.append((args, kwargs))
        if self.raises:
            raise self.raises

    async def log_out(self) -> bool:
        return True


def test_gateway_and_auth_satisfy_the_protocols() -> None:
    client: Any = StubClient()

    assert isinstance(TelethonGateway(client), TelegramGateway)
    assert isinstance(TelethonAuth(client), TelegramAuth)


async def test_list_channels_skips_users_and_left_chats() -> None:
    dialogs = [
        SimpleNamespace(entity=channel(id=1, title="A", broadcast=True, creator=True)),
        SimpleNamespace(entity=types.User(id=2)),
        SimpleNamespace(entity=channel(id=3, title="B", broadcast=True, left=True)),
        SimpleNamespace(entity=channel(id=4, title="C", megagroup=True)),
    ]

    infos = await TelethonGateway(StubClient(dialogs=dialogs)).list_channels()  # type: ignore[arg-type]

    assert [i.title for i in infos] == ["A", "C"]


async def test_create_channel_sends_a_broadcast_request() -> None:
    created = channel(id=99, title="Copy", broadcast=True, creator=True)
    client = StubClient(result=SimpleNamespace(chats=[created]))

    info = await TelethonGateway(client).create_channel("Copy", "about")  # type: ignore[arg-type]

    (request,) = client.requests
    assert isinstance(request, CreateChannelRequest)
    assert (request.title, request.about, request.broadcast) == ("Copy", "about", True)
    assert (info.id, info.title, info.can_post, info.is_admin) == (
        -1000000000099,
        "Copy",
        True,
        True,
    )


async def test_create_channel_maps_flood_wait() -> None:
    client = StubClient()
    client.raises = errors.FloodWaitError(None, capture=300)

    with pytest.raises(FloodWait) as caught:
        await TelethonGateway(client).create_channel("Copy")  # type: ignore[arg-type]

    assert caught.value.seconds == 300


async def test_create_channel_supergroup_sends_megagroup() -> None:
    """Phase 8: every non-broadcast kind is a megagroup (docs/01-kien-truc.md, "Loại nguồn")."""
    created = channel(id=1, title="G", megagroup=True, creator=True)
    client = StubClient(result=SimpleNamespace(chats=[created]))

    info = await TelethonGateway(client).create_channel(  # type: ignore[arg-type]
        "G", kind=ChatKind.SUPERGROUP
    )

    (request,) = client.requests
    assert isinstance(request, CreateChannelRequest)
    assert (request.broadcast, request.megagroup) == (None, True)
    assert info.kind is ChatKind.SUPERGROUP


async def test_create_channel_forum_also_toggles_forum_and_refetches() -> None:
    """A fresh megagroup does not report ``forum=True`` until ``ToggleForumRequest`` runs, so the
    gateway re-fetches the entity afterwards (unverified on a real account, phase 8)."""
    created = channel(id=1, title="F", megagroup=True, creator=True)  # not a forum yet
    refetched = channel(id=1, title="F", megagroup=True, forum=True, creator=True)
    client = StubClient(result=SimpleNamespace(chats=[created]), entity_result=refetched)

    info = await TelethonGateway(client).create_channel(  # type: ignore[arg-type]
        "F", kind=ChatKind.FORUM
    )

    create_request, toggle_request = client.requests
    assert isinstance(create_request, CreateChannelRequest) and create_request.megagroup
    assert isinstance(toggle_request, ToggleForumRequest)
    assert (toggle_request.enabled, toggle_request.tabs) == (True, False)
    assert info.kind is ChatKind.FORUM


async def test_list_topics_maps_forum_topics_and_paginates() -> None:
    page1 = [
        types.ForumTopic(
            id=i,
            date=None,
            peer=types.PeerChannel(1),
            title=f"T{i}",
            icon_color=0,
            top_message=i,
            read_inbox_max_id=0,
            read_outbox_max_id=0,
            unread_count=0,
            unread_mentions_count=0,
            unread_reactions_count=0,
            unread_poll_votes_count=0,
            from_id=types.PeerUser(user_id=1),
            notify_settings=types.PeerNotifySettings(),
            closed=(i == 3),
        )
        for i in range(1, 101)
    ]
    page2 = [
        types.ForumTopic(
            id=101,
            date=None,
            peer=types.PeerChannel(1),
            title="T101",
            icon_color=0,
            top_message=101,
            read_inbox_max_id=0,
            read_outbox_max_id=0,
            unread_count=0,
            unread_mentions_count=0,
            unread_reactions_count=0,
            unread_poll_votes_count=0,
            from_id=types.PeerUser(user_id=1),
            notify_settings=types.PeerNotifySettings(),
        )
    ]
    last_activity = datetime(2026, 9, 1, tzinfo=UTC)
    top = SimpleNamespace(id=100, date=last_activity)  # page 1's last topic's newest message
    client = StubClient()
    client.results.extend(
        [
            SimpleNamespace(topics=page1, count=101, messages=[top]),
            SimpleNamespace(topics=page2, count=101, messages=[]),
        ]
    )

    topics = await TelethonGateway(client).list_topics(-1001)  # type: ignore[arg-type]

    assert [t.id for t in topics] == list(range(1, 102))
    assert topics[2].closed is True and topics[0].closed is False
    first_request, second_request = client.requests
    assert isinstance(first_request, GetForumTopicsRequest)
    assert (first_request.offset_date, first_request.offset_id, first_request.offset_topic) == (
        None,
        0,
        0,
    )
    # Telegram orders topics by their latest message: the next page starts from its date, not
    # from the topic's own creation date
    assert (second_request.offset_date, second_request.offset_id, second_request.offset_topic) == (
        last_activity,
        100,
        100,
    )


async def test_list_topics_keeps_paging_past_deleted_topics() -> None:
    """A page shortened by deleted topics is not the last one: ``count`` says how many exist."""
    deleted = [types.ForumTopicDeleted(id=i) for i in range(1, 51)]
    alive = [
        types.ForumTopic(
            id=i,
            date=None,
            peer=types.PeerChannel(1),
            title=f"T{i}",
            icon_color=0,
            top_message=i,
            read_inbox_max_id=0,
            read_outbox_max_id=0,
            unread_count=0,
            unread_mentions_count=0,
            unread_reactions_count=0,
            unread_poll_votes_count=0,
            from_id=types.PeerUser(user_id=1),
            notify_settings=types.PeerNotifySettings(),
        )
        for i in range(51, 102)
    ]
    client = StubClient()
    client.results.extend(
        [
            SimpleNamespace(topics=deleted + alive[:50], count=101, messages=[]),
            SimpleNamespace(topics=alive[50:], count=101, messages=[]),
        ]
    )

    topics = await TelethonGateway(client).list_topics(-1001)  # type: ignore[arg-type]

    assert [t.id for t in topics] == list(range(51, 102))
    assert len(client.requests) == 2


async def test_create_topic_reads_the_new_id_from_updates() -> None:
    client = StubClient(
        result=SimpleNamespace(updates=[types.UpdateMessageID(id=777, random_id=1)])
    )

    topic_id = await TelethonGateway(client).create_topic(  # type: ignore[arg-type]
        -1001, "Announcements"
    )

    assert topic_id == 777
    (request,) = client.requests
    assert isinstance(request, CreateForumTopicRequest)
    assert request.title == "Announcements"


async def test_get_channel_unknown_entity_is_no_permission() -> None:
    client = StubClient()
    client.raises = ValueError("Could not find the input entity")

    with pytest.raises(NoPermission):
        await TelethonGateway(client).get_channel(-1001)  # type: ignore[arg-type]


async def test_auth_account_and_logout() -> None:
    client = StubClient()
    auth = TelethonAuth(client)  # type: ignore[arg-type]

    account = await auth.account()
    assert account is not None and account.name == "Ada L" and account.id == 7
    assert not hasattr(account, "phone")  # never carried around (hard rule 6)

    client.authorized = False
    assert await auth.account() is None


async def test_auth_sign_in_maps_errors_and_passes_arguments() -> None:
    client = StubClient()
    auth = TelethonAuth(client)  # type: ignore[arg-type]

    await auth.sign_in_code("+1", "123")
    await auth.sign_in_password("pw")
    assert client.signed_in == [(("+1", "123"), {}), ((), {"password": "pw"})]

    client.raises = errors.SessionPasswordNeededError(None)
    with pytest.raises(PasswordRequired):
        await auth.sign_in_code("+1", "123")


def test_make_client_never_sleeps_on_flood_wait(tmp_path: Path) -> None:
    client = make_client(tmp_path / "s.session", 12345, "0123456789abcdef0123456789abcdef")
    try:
        assert client.flood_sleep_threshold == 0  # decision D6
    finally:
        client.session.close()


def test_slow_mode_is_a_flood_wait_that_says_so() -> None:
    mapped = map_exception(errors.SlowModeWaitError(None, capture=15))

    assert isinstance(mapped, FloodWait) and (mapped.seconds, mapped.slow_mode) == (15, True)
    plain = map_exception(errors.FloodWaitError(None, capture=15))
    assert isinstance(plain, FloodWait) and plain.slow_mode is False
