import pytest

from tests.fakes import FakeGateway
from tgmirror.core.errors import FloodWait
from tgmirror.core.gateway import ChatKind
from tgmirror.engine.endpoints import (
    AmbiguousChannel,
    ChannelNotFound,
    DestinationNotWritable,
    InvalidChannelTitle,
    NewChannelSpec,
    SameChannel,
    SourceRestricted,
    eligible_destinations,
    find_channel,
    materialize,
    plan_endpoints,
    validate_new_channel,
)


def test_find_channel_by_id_username_and_title(gateway: FakeGateway) -> None:
    news = gateway.add_channel("Daily News", username="daily_news")
    other = gateway.add_channel("Other")
    channels = list(gateway.channels.values())

    assert find_channel(channels, str(news.id)) == news
    assert find_channel(channels, "@Daily_News") == news  # usernames ignore case
    assert find_channel(channels, "daily news") == news  # exact title, ignoring case
    assert find_channel(channels, other.title) == other


def test_find_channel_accepts_a_username_without_the_at_sign(gateway: FakeGateway) -> None:
    news = gateway.add_channel("Daily News", username="daily_news")
    gateway.add_channel("daily_news")  # a title equal to the word wins over a username

    channels = list(gateway.channels.values())

    assert find_channel(channels[:1], "Daily_News") == news
    assert find_channel(channels, "daily_news") == channels[1]


def test_find_channel_accepts_the_bare_channel_id(gateway: FakeGateway) -> None:
    info = gateway.add_channel("Chan")  # id is -100<n>
    bare = str(-info.id - 1_000_000_000_000)

    assert find_channel([info], bare) == info


def test_find_channel_does_not_guess_from_a_partial_title(gateway: FakeGateway) -> None:
    gateway.add_channel("Daily News")

    with pytest.raises(ChannelNotFound):
        find_channel(list(gateway.channels.values()), "news")


def test_find_channel_rejects_duplicate_titles(gateway: FakeGateway) -> None:
    gateway.add_channel("Same")
    gateway.add_channel("Same")

    with pytest.raises(AmbiguousChannel) as caught:
        find_channel(list(gateway.channels.values()), "same")

    assert len(caught.value.matches) == 2


def test_eligible_destinations_are_writable_and_not_the_source(
    gateway: FakeGateway,
) -> None:
    """Any kind may pair with any kind since "same kind only" was dropped: cross-kind clones
    (e.g. group -> broadcast) are allowed, only forum topics are special (``topic_loss``, see
    ``test_plan_warns_about_topic_loss_leaving_a_forum``)."""
    src = gateway.add_channel("src")
    ok = gateway.add_channel("ok")
    other_kind = gateway.add_channel("a group", kind=ChatKind.SUPERGROUP)
    gateway.add_channel("read only", can_post=False)
    gateway.add_channel("not admin", is_admin=False)

    assert eligible_destinations(src, list(gateway.channels.values())) == [ok, other_kind]


def test_plan_with_existing_destination(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src"), gateway.add_channel("dst")

    plan = plan_endpoints(src, dst)

    assert plan.dst == dst and plan.warnings == ()


def test_plan_refuses_restricted_source_for_non_admin(gateway: FakeGateway) -> None:
    src = gateway.add_channel("locked", noforwards=True, is_admin=False, can_post=False)

    with pytest.raises(SourceRestricted):  # decision D3, and no destination is created
        plan_endpoints(src, NewChannelSpec("copy"))


def test_the_users_statement_of_responsibility_lets_a_non_admin_account_through(
    gateway: FakeGateway,
) -> None:
    src = gateway.add_channel("locked", noforwards=True, is_admin=False, can_post=False)
    dst = gateway.add_channel("dst")

    plan = plan_endpoints(src, dst, take_responsibility=True)

    assert plan.protected and plan.warnings == ("noforwards_unadministered",)
    with pytest.raises(SourceRestricted):  # ...and nothing else does
        plan_endpoints(src, dst)


def test_the_statement_changes_nothing_for_a_source_that_is_not_protected(
    gateway: FakeGateway,
) -> None:
    src, dst = gateway.add_channel("open"), gateway.add_channel("dst")

    plan = plan_endpoints(src, dst, take_responsibility=True)

    assert not plan.protected and plan.warnings == ()


def test_plan_warns_for_restricted_source_when_admin(gateway: FakeGateway) -> None:
    src = gateway.add_channel("locked", noforwards=True, is_admin=True)
    dst = gateway.add_channel("dst")

    assert plan_endpoints(src, dst).warnings == ("noforwards_admin",)


def test_plan_rejects_same_channel(gateway: FakeGateway) -> None:
    src = gateway.add_channel("src")

    with pytest.raises(SameChannel):
        plan_endpoints(src, src)


def test_plan_allows_a_cross_kind_existing_destination(gateway: FakeGateway) -> None:
    src = gateway.add_channel("src")
    group = gateway.add_channel("group", kind=ChatKind.SUPERGROUP)

    plan = plan_endpoints(src, group)

    assert plan.dst == group and plan.warnings == ()  # only a forum source warns (topic_loss)


@pytest.mark.parametrize("dst_kind", [ChatKind.BROADCAST, ChatKind.SUPERGROUP, ChatKind.GROUP])
def test_plan_warns_about_topic_loss_leaving_a_forum(
    gateway: FakeGateway, dst_kind: ChatKind
) -> None:
    src = gateway.add_channel("src", kind=ChatKind.FORUM)
    dst = gateway.add_channel("dst", kind=dst_kind)

    assert plan_endpoints(src, dst).warnings == ("topic_loss",)


def test_plan_does_not_warn_forum_to_forum(gateway: FakeGateway) -> None:
    src = gateway.add_channel("src", kind=ChatKind.FORUM)
    dst = gateway.add_channel("dst", kind=ChatKind.FORUM)

    assert plan_endpoints(src, dst).warnings == ()


@pytest.mark.parametrize("flags", [{"can_post": False}, {"is_admin": False}])
def test_plan_rejects_destination_without_admin_post_rights(
    gateway: FakeGateway, flags: dict[str, bool]
) -> None:
    src = gateway.add_channel("src")
    dst = gateway.add_channel("dst", **flags)

    with pytest.raises(DestinationNotWritable):
        plan_endpoints(src, dst)


@pytest.mark.parametrize(
    ("src_kind", "expected"),
    [
        (ChatKind.BROADCAST, ChatKind.BROADCAST),
        (ChatKind.SUPERGROUP, ChatKind.SUPERGROUP),
        (ChatKind.FORUM, ChatKind.FORUM),
        (ChatKind.GROUP, ChatKind.SUPERGROUP),  # a basic group cannot be created via the API
    ],
)
async def test_a_new_destination_mirrors_the_source_kind(
    gateway: FakeGateway, src_kind: ChatKind, expected: ChatKind
) -> None:
    src = gateway.add_channel("src", kind=src_kind)

    plan = plan_endpoints(src, NewChannelSpec("copy"))
    created = await materialize(gateway, plan)

    assert created.created and created.dst.kind is expected
    ((_, _, kind),) = [c.args for c in gateway.calls_to("create_channel")]
    assert kind is expected


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (NewChannelSpec("   "), "title_empty"),
        (NewChannelSpec("x" * 129), "title_too_long"),
        (NewChannelSpec("ok", "y" * 256), "about_too_long"),
    ],
)
def test_validate_new_channel_rejects(spec: NewChannelSpec, reason: str) -> None:
    with pytest.raises(InvalidChannelTitle) as caught:
        validate_new_channel(spec)

    assert caught.value.reason == reason


def test_validate_new_channel_strips() -> None:
    assert validate_new_channel(NewChannelSpec("  Copy  ", " about ")) == NewChannelSpec(
        "Copy", "about"
    )


async def test_materialize_creates_only_when_asked(gateway: FakeGateway) -> None:
    src, dst = gateway.add_channel("src"), gateway.add_channel("dst")

    existing = await materialize(gateway, plan_endpoints(src, dst))
    assert not existing.created and gateway.calls_to("create_channel") == []

    created = await materialize(gateway, plan_endpoints(src, NewChannelSpec("Copy", "about")))
    assert created.created and created.dst.title == "Copy"
    assert [c.args for c in gateway.calls_to("create_channel")] == [
        ("Copy", "about", ChatKind.BROADCAST)
    ]


async def test_materialize_lets_gateway_errors_through(gateway: FakeGateway) -> None:
    src = gateway.add_channel("src")
    gateway.fail_next("create_channel", FloodWait(30))

    with pytest.raises(FloodWait):
        await materialize(gateway, plan_endpoints(src, NewChannelSpec("Copy")))
