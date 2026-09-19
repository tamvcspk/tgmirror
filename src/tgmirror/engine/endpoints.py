"""Choosing and checking the two ends of a mirror: source and destination.

Flags and the wizard both end up here (the parity rule, skill ``cli-wizard``): they only differ in
how they obtain a source ``ChannelInfo`` and a destination (existing ``ChannelInfo`` or a
``NewChannelSpec``). The rules follow docs/01-kien-truc.md ("Loại nguồn") and decision D3.
Depends on the gateway protocol only (hard rule 8).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from tgmirror.core.errors import TgMirrorError
from tgmirror.core.gateway import ChannelInfo, ChatKind, TelegramGateway

MAX_TITLE = 128  # Telegram's limits for a channel title and its description
MAX_ABOUT = 255


class EndpointError(TgMirrorError):
    """Base class for problems with the chosen source or destination."""


class ChannelNotFound(EndpointError):
    def __init__(self, ref: str) -> None:
        super().__init__(f"no joined channel matches {ref!r}")
        self.ref = ref


class AmbiguousChannel(EndpointError):
    def __init__(self, ref: str, matches: Sequence[ChannelInfo]) -> None:
        super().__init__(f"{ref!r} matches {len(matches)} channels")
        self.ref = ref
        self.matches = tuple(matches)


class SourceRestricted(EndpointError):
    """Decision D3: the source restricts saving content and the user does not administer it."""

    def __init__(self, src: ChannelInfo) -> None:
        super().__init__(f"{src.title!r} restricts saving content and you are not its admin")
        self.src = src


class DestinationNotWritable(EndpointError):
    def __init__(self, dst: ChannelInfo) -> None:
        super().__init__(f"you cannot post to {dst.title!r} as an admin")
        self.dst = dst


class KindMismatch(EndpointError):
    """An existing destination must be the same kind as the source (decided 2026-09-19)."""

    def __init__(self, src: ChannelInfo, dst: ChannelInfo) -> None:
        super().__init__(f"source is a {src.kind}, destination is a {dst.kind}")
        self.src = src
        self.dst = dst


class SameChannel(EndpointError):
    def __init__(self) -> None:
        super().__init__("source and destination are the same chat")


class InvalidChannelTitle(EndpointError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        # message key of ui/messages.py minus "err.": title_empty | title_too_long | about_too_long
        self.reason = reason


class NewChannelUnsupported(EndpointError):
    """Creating a destination of this kind is not implemented yet (phase 8)."""

    def __init__(self, kind: ChatKind) -> None:
        super().__init__(f"cannot create a new {kind} destination yet")
        self.kind = kind


@dataclass(frozen=True, slots=True)
class NewChannelSpec:
    title: str
    about: str = ""


@dataclass(frozen=True, slots=True)
class Plan:
    """Validated choice. ``warnings`` are message codes for the UI (``ui/messages.py``)."""

    src: ChannelInfo
    dst: ChannelInfo | NewChannelSpec
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Endpoints:
    src: ChannelInfo
    dst: ChannelInfo
    created: bool  # the destination was created by this run
    warnings: tuple[str, ...] = ()


def find_channel(channels: Sequence[ChannelInfo], ref: str) -> ChannelInfo:
    """Resolve ``@username``, a numeric id (``-100...`` or the bare number) or an exact title.

    Only chats the account has joined can match. Titles must match exactly (ignoring case) and
    must be unique: a wrong guess for a destination would send messages to the wrong place.
    A bare word that is no title is tried as a username, because PowerShell swallows an unquoted
    ``@name`` (splatting) and users then have nothing to type but the name.
    """
    ref = ref.strip()
    matches: list[ChannelInfo]
    if ref.startswith("@"):
        name = ref[1:].lower()
        matches = [c for c in channels if c.username and c.username.lower() == name]
    elif _is_int(ref):
        number = int(ref)
        # a bare positive number is the raw id: channels are -100<id>, basic groups -<id>
        wanted = {number, -number, -1_000_000_000_000 - number} if number > 0 else {number}
        matches = [c for c in channels if c.id in wanted]
    else:
        matches = [c for c in channels if c.title.casefold() == ref.casefold()]
        if not matches:
            word = ref.casefold()
            matches = [c for c in channels if c.username and c.username.casefold() == word]
    if not matches:
        raise ChannelNotFound(ref)
    if len(matches) > 1:
        raise AmbiguousChannel(ref, matches)
    return matches[0]


def eligible_destinations(src: ChannelInfo, channels: Sequence[ChannelInfo]) -> list[ChannelInfo]:
    """Existing chats the wizard may offer as destination for ``src``."""
    return [c for c in channels if c.id != src.id and _writable_same_kind(src, c)]


def validate_new_channel(spec: NewChannelSpec) -> NewChannelSpec:
    title, about = spec.title.strip(), spec.about.strip()
    if not title:
        raise InvalidChannelTitle("title_empty")
    if len(title) > MAX_TITLE:
        raise InvalidChannelTitle("title_too_long")
    if len(about) > MAX_ABOUT:
        raise InvalidChannelTitle("about_too_long")
    return NewChannelSpec(title, about)


def plan_endpoints(src: ChannelInfo, dst: ChannelInfo | NewChannelSpec) -> Plan:
    """Check the pair without touching Telegram. Raises an ``EndpointError``."""
    warnings: list[str] = []
    if src.noforwards:
        if not src.is_admin:
            raise SourceRestricted(src)
        warnings.append("noforwards_admin")  # D3: admins may lift the restriction or reupload

    if isinstance(dst, NewChannelSpec):
        if src.kind is not ChatKind.BROADCAST:
            raise NewChannelUnsupported(src.kind)
        return Plan(src, validate_new_channel(dst), tuple(warnings))

    if dst.id == src.id:
        raise SameChannel
    if dst.kind is not src.kind:
        raise KindMismatch(src, dst)
    if not (dst.is_admin and dst.can_post):
        raise DestinationNotWritable(dst)
    return Plan(src, dst, tuple(warnings))


async def materialize(gateway: TelegramGateway, plan: Plan) -> Endpoints:
    """Create the destination if the plan asks for one."""
    if isinstance(plan.dst, NewChannelSpec):
        dst = await gateway.create_channel(plan.dst.title, plan.dst.about)
        return Endpoints(plan.src, dst, created=True, warnings=plan.warnings)
    return Endpoints(plan.src, plan.dst, created=False, warnings=plan.warnings)


def _writable_same_kind(src: ChannelInfo, dst: ChannelInfo) -> bool:
    return dst.kind is src.kind and dst.is_admin and dst.can_post


def _is_int(text: str) -> bool:
    try:
        int(text)
    except ValueError:
        return False
    return True
