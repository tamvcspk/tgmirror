"""Telethon is pinned, and everything private we lean on is checked (docs/06-lo-trinh.md, "Nâng cấp
Telethon").

The request pool talks to Telethon's internals (``_call``, borrowed senders, ...), which have no
compatibility promise, and strategy B relies on how ``send_file`` builds a video. None of that shows
in the tests that use a stub client, so these tests make an upgrade a deliberate act: they fail
until the version is bumped in three places and the checklist has been followed.
"""

import inspect
import re
import tomllib
from pathlib import Path

import telethon
from telethon import TelegramClient, helpers, types
from telethon.errors.common import InvalidBufferError
from telethon.network import MTProtoSender
from telethon.sessions import MemorySession
from telethon.tl.functions.upload import GetFileRequest, SaveBigFilePartRequest

from tgmirror.core import telethon_gateway

ROOT = Path(__file__).resolve().parents[2]

# What ``core/telethon_gateway.py`` reaches into that Telethon does not promise to keep.
PRIVATE_ON_THE_CLIENT = {
    "call",
    "borrow_exported_sender",
    "return_exported_sender",
    "sender",
    "get_dc",
    "connection",
    "log",
    "proxy",
    "local_addr",
}

UPGRADE = "see docs/06-lo-trinh.md, 'Nâng cấp Telethon', before changing the version"


def offline_client() -> TelegramClient:
    return TelegramClient(MemorySession(), 1, "hash")


def test_the_installed_version_is_the_one_that_was_checked() -> None:
    assert telethon.__version__ == telethon_gateway.TELETHON_CHECKED, UPGRADE


def test_pyproject_pins_that_exact_version() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pins = [d for d in data["project"]["dependencies"] if d.lower().startswith("telethon")]

    assert pins == [f"telethon=={telethon_gateway.TELETHON_CHECKED}"], UPGRADE


def test_the_private_calls_the_gateway_makes_are_the_ones_listed_here() -> None:
    """A new reach into the client's internals must be added to this list (and so to the
    checklist); one that is gone must come off it."""
    source = (ROOT / "src" / "tgmirror" / "core" / "telethon_gateway.py").read_text(
        encoding="utf-8"
    )
    used = set(re.findall(r"(?:\b_client|\bclient)\._(\w+)", source))

    assert used - {"client"} == PRIVATE_ON_THE_CLIENT


def test_the_private_surface_is_still_there() -> None:
    client = offline_client()

    for name in PRIVATE_ON_THE_CLIENT - {"sender", "connection", "log", "proxy", "local_addr"}:
        assert callable(getattr(client, f"_{name}")), f"TelegramClient._{name} is gone"
    for name in ("sender", "connection", "log", "proxy", "local_addr"):
        assert hasattr(client, f"_{name}"), f"TelegramClient._{name} is gone"
    assert isinstance(client._sender, MTProtoSender)
    assert client.session.dc_id is not None or client.session.dc_id == 0
    assert hasattr(client.session, "auth_key")


def test_the_signatures_the_pool_calls_are_unchanged() -> None:
    call = inspect.signature(TelegramClient._call).parameters
    assert list(call)[:3] == ["self", "sender", "request"]
    assert "flood_sleep_threshold" in call
    sender = inspect.signature(MTProtoSender.__init__).parameters
    assert list(sender)[:2] == ["self", "auth_key"] and "loggers" in sender
    assert list(inspect.signature(TelegramClient._borrow_exported_sender).parameters) == [
        "self",
        "dc_id",
    ]
    assert list(inspect.signature(TelegramClient._get_dc).parameters)[:2] == ["self", "dc_id"]


def test_the_requests_and_types_the_pool_builds_still_take_what_we_pass() -> None:
    get = inspect.signature(GetFileRequest).parameters
    assert {"location", "offset", "limit"} <= set(get)
    save = inspect.signature(SaveBigFilePartRequest).parameters
    assert {"file_id", "file_part", "file_total_parts", "bytes"} <= set(save)
    big = types.InputFileBig(id=1, parts=2, name="a.mp4")
    assert (big.id, big.parts, big.name) == (1, 2, "a.mp4")
    assert types.upload.FileCdnRedirect is not None
    assert callable(helpers.generate_random_long)


def test_a_transport_error_still_carries_its_http_code() -> None:
    import struct

    assert InvalidBufferError(struct.pack("<i", -429)).code == 429


def test_the_progress_callbacks_and_send_options_are_still_accepted() -> None:
    send = inspect.signature(TelegramClient.send_file).parameters
    assert {
        "progress_callback",
        "attributes",
        "thumb",
        "force_document",
        "supports_streaming",
        "nosound_video",
        "formatting_entities",
        "parse_mode",
        "mime_type",
    } <= set(send)
    assert "progress_callback" in inspect.signature(TelegramClient.download_media).parameters


async def test_a_video_posted_from_an_uploaded_handle_is_still_built_as_a_video() -> None:
    """Strategy B's worst bug was a video that showed up as a plain file. From a handle
    (``InputFileBig``, what the pool produces) Telethon must build the same media as from a path:
    not forced to a file, the video attributes and the no-sound flag kept."""
    video = types.DocumentAttributeVideo(duration=5, w=320, h=240, supports_streaming=True)
    handle = types.InputFileBig(id=1, parts=3, name="4.mp4")

    _, media, _ = await offline_client()._file_to_media(
        handle,
        force_document=False,
        attributes=[video],
        mime_type="video/mp4",
        supports_streaming=True,
        nosound_video=True,
        thumb=None,
    )

    assert isinstance(media, types.InputMediaUploadedDocument)
    assert media.force_file is False and media.mime_type == "video/mp4"
    assert video in media.attributes and media.nosound_video is True
