"""What the spike scripts share: open the saved Telethon session and name a chat.

The spikes answer questions only real Telegram can (docs/06-lo-trinh.md, "Việc cần xác minh sớm"),
so they use the Telethon client directly, private helpers included. They are read-only unless the
script says otherwise on its first line, and they never print secrets.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from telethon import TelegramClient, utils

from tgmirror.core.config import load_config
from tgmirror.core.paths import Paths
from tgmirror.core.telethon_gateway import TelethonAuth, TelethonGateway, telethon_session


@asynccontextmanager
async def session() -> AsyncIterator[tuple[TelegramClient, TelethonGateway]]:
    """The logged-in client (made as ``tgmirror`` makes it: no silent FloodWait sleeps) and its
    gateway."""
    paths = Paths.default()
    async with telethon_session(paths, load_config(paths)) as (auth, gateway):
        assert isinstance(auth, TelethonAuth) and isinstance(gateway, TelethonGateway)
        if await auth.account() is None:
            raise SystemExit("Not logged in: run `tgmirror login` first.")
        yield gateway._client, gateway  # noqa: SLF001 - spikes reach for the raw client on purpose


async def chat_id(client: TelegramClient, ref: str) -> int:
    """A marked peer id from ``-100123...``, ``@username`` or a t.me name."""
    if ref.lstrip("-").isdigit():
        return int(ref)
    return int(utils.get_peer_id(await client.get_entity(ref)))


def mb(count: float) -> str:
    return f"{count / (1024 * 1024):.1f} MB"
