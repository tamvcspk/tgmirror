"""The menu entries for the operations after ``delete`` (Phases 17, 20.3, 20.4, 20b): each is the
same flow the command runs, asked by keys, then ``OpScreen``. Nothing changes before the count is
typed back.

Every test closes the app's ``Store``: an open aiosqlite connection keeps the interpreter alive.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tests.unit.test_menu_backup_restore import asked, make_app, plain, press
from tests.unit.test_menu_delete import ADS, finish
from tgmirror.cli.app import app as cli_app
from tgmirror.cli.commands.delete import DeleteOptions
from tgmirror.cli.commands.repost import RepostOptions
from tgmirror.cli.keys import MenuKey
from tgmirror.cli.runtime import Connection, Runtime
from tgmirror.core.gateway import ChannelInfo, ChatKind
from tgmirror.store.db import Store
from tgmirror.ui.menu.app import MenuApp
from tgmirror.ui.menu.op_screen import OpScreen
from tgmirror.ui.menu.screens.edit import edit_screen
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.main_menu import MainMenuScreen
from tgmirror.ui.menu.screens.repost import repost_screen
from tgmirror.ui.menu.screens.sync import sync_screen
from tgmirror.ui.menu.screens.topics import topics_screen
from tgmirror.ui.messages import t

MakeRuntime = Callable[..., Runtime]


@pytest.fixture
def make_runtime(make_runtime: MakeRuntime) -> MakeRuntime:
    """Tiny delays: a plan reads and an operation writes at the limiter's pace."""

    def factory(**kw: Any) -> Runtime:
        rt = make_runtime(**kw)
        rt.paths.config_dir.mkdir(parents=True, exist_ok=True)
        rt.paths.config_file.write_text(
            "[limits]\nmin_delay = 0.01\nmax_delay = 0.02\nread_delay = 0.01\n"
            "long_pause_range = [0, 0]\n",
            encoding="utf-8",
        )
        return rt

    return factory


@asynccontextmanager
async def menu_of(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> AsyncIterator[MenuApp]:
    app = await make_app(make_runtime, tmp_path, gateway, FakeAuth(logged_in=ACCOUNT))
    try:
        yield app
    finally:
        await app.store.close()


def chat_with_ads(gateway: FakeGateway) -> ChannelInfo:
    chat = gateway.add_channel("Chat")
    for i in range(3):
        gateway.add_message(chat.id, f"ad {i} #ads", hashtags=("#ads",))
    gateway.add_message(chat.id, "keep")
    return chat


def texts(gateway: FakeGateway, chat: ChannelInfo) -> list[str]:
    return [m.text for m in gateway.messages[chat.id]]


def transform_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "t.yaml"
    path.write_text(body, encoding="utf-8")
    return path


async def test_the_main_menu_offers_the_operations(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat_with_ads(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        menu = MainMenuScreen(app)
        await menu.on_enter()
        shown = plain(menu.render())

        for key in ("menu.item_edit", "menu.item_repost", "menu.item_topics"):
            assert t(key) in shown
        assert t("menu.item_sync") not in shown  # nothing was ever copied: no pair to follow


async def test_edit_asks_for_the_file_then_the_count_and_edits(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat = chat_with_ads(gateway)
    path = transform_file(tmp_path, "caption:\n  - remove_hashtags: ['#ads']\n  - text_too: true\n")
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = edit_screen(app, DeleteOptions(src="Chat", filters=ADS))
        await screen.on_enter()
        assert asked(screen) == t("menu.ask_transform")
        await press(screen, *str(path), MenuKey.ENTER)
        assert "3" in asked(screen) and "O1" in plain(screen.render())  # the diff is above
        assert texts(gateway, chat)[0] == "ad 0 #ads"  # nothing yet

        result = await press(screen, "3", MenuKey.ENTER)

        assert result[0] == "replace" and isinstance(result[1], OpScreen)
        await finish(result[1])
        assert texts(gateway, chat) == ["ad 0", "ad 1", "ad 2", "keep"]


async def test_edit_refuses_a_transform_that_is_not_about_text(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat = chat_with_ads(gateway)
    path = transform_file(tmp_path, "reply: keep\ncaption:\n  - remove_hashtags: all\n")
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = edit_screen(app, DeleteOptions(src="Chat", filters=ADS))
        await screen.on_enter()

        result = await press(screen, *str(path), MenuKey.ENTER)

        assert result[0] == "replace" and isinstance(result[1], InfoScreen)
        assert texts(gateway, chat)[0] == "ad 0 #ads"


async def test_repost_with_the_originals_deleted_asks_the_count_and_a_backup(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat = chat_with_ads(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = repost_screen(app, RepostOptions(src="Chat", filters=ADS, delete_original=True))
        await screen.on_enter()
        await press(screen, "3", MenuKey.ENTER)
        assert asked(screen) == t("delete.confirm_backup", count=3)
        result = await press(screen, MenuKey.DOWN, MenuKey.ENTER)  # No backup

        assert isinstance(result[1], OpScreen)
        await finish(result[1])
        assert texts(gateway, chat) == ["keep", "ad 0 #ads", "ad 1 #ads", "ad 2 #ads"]


async def test_repost_asks_first_whether_the_originals_go(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat_with_ads(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = repost_screen(app)
        await screen.on_enter()

        assert asked(screen) == t("menu.ask_delete_original")


async def test_sync_follows_the_source_in_one_screen(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    src = gateway.add_channel("Source")
    for text in ("one", "two", "three"):
        gateway.add_message(src.id, text)
    runtime = make_runtime(gateway=gateway, interactive=True)
    cloned = await asyncio.to_thread(
        CliRunner().invoke,
        cli_app,
        ["clone", "--src", "Source", "--dst-new", "Copy", "--yes"],
        obj=runtime,
    )  # its own event loop: not from inside this one
    assert cloned.exit_code == 0, cloned.output
    dst = next(c for c in gateway.channels.values() if c.title == "Copy")
    store = await Store.open(runtime.paths.db_path)  # the history the clone just wrote
    auth = FakeAuth(logged_in=ACCOUNT)
    app = MenuApp(runtime, store, Connection(auth, gateway), await auth.account())
    try:
        await gateway.edit_message(src.id, 1, "one, edited")
        gateway.delete_message(src.id, 3)

        screen = sync_screen(app)
        await screen.on_enter()
        assert "2" in asked(screen)  # one edit and one delete: type 2
        await press(screen, "2", MenuKey.ENTER)
        result = await press(screen, MenuKey.DOWN, MenuKey.ENTER)  # no backup of the delete

        assert isinstance(result[1], OpScreen)
        await finish(result[1])
        assert texts(gateway, dst) == ["one, edited", "two"]
    finally:
        await store.close()


def forum(gateway: FakeGateway) -> tuple[ChannelInfo, int, int]:
    chat = gateway.add_channel("Forum", kind=ChatKind.FORUM)
    news = gateway.add_topic(chat.id, "News").id
    old = gateway.add_topic(chat.id, "Old").id
    gateway.add_message(chat.id, "n1", topic_id=news)
    gateway.add_message(chat.id, "o1", topic_id=old)
    gateway.add_message(chat.id, "o2", topic_id=old)
    return chat, news, old


async def test_topics_merge_by_keys(make_runtime: MakeRuntime, tmp_path: Path) -> None:
    gateway = FakeGateway()
    chat, news, _ = forum(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = topics_screen(app)
        await screen.on_enter()
        await press(screen, MenuKey.DOWN, MenuKey.ENTER)  # "Merge topics"
        await press(screen, MenuKey.ENTER)  # the forum
        await press(screen, MenuKey.DOWN, MenuKey.ENTER)  # into "News" (General is the first)
        await press(screen, MenuKey.DOWN, " ", MenuKey.ENTER)  # tick "Old"
        assert "2" in asked(screen)
        await press(screen, "2", MenuKey.ENTER)
        result = await press(screen, MenuKey.DOWN, MenuKey.ENTER)  # no backup

        assert isinstance(result[1], OpScreen)
        await finish(result[1])
        moved = [m.text for m in gateway.messages[chat.id] if m.topic_id == news]
        assert moved == ["n1", "o1", "o2"]
        assert [topic.title for topic in gateway.topics[chat.id]] == ["News"]


async def test_topics_rename_by_keys(make_runtime: MakeRuntime, tmp_path: Path) -> None:
    gateway = FakeGateway()
    chat, _, _ = forum(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = topics_screen(app)
        await screen.on_enter()
        await press(screen, MenuKey.DOWN, MenuKey.DOWN, MenuKey.DOWN, MenuKey.ENTER)  # rename
        await press(screen, *"Headlines", MenuKey.ENTER)  # the new title comes first
        await press(screen, MenuKey.ENTER)  # the forum
        await press(screen, MenuKey.DOWN, MenuKey.ENTER)  # News
        result = await press(screen, MenuKey.UP, MenuKey.ENTER)  # the default is No: up to Yes
        assert screen.task is not None
        await screen.task
        result = await screen.tick()
        assert result is not None

        assert result[0] == "replace" and isinstance(result[1], InfoScreen)
        assert [topic.title for topic in gateway.topics[chat.id]] == ["Headlines", "Old"]
