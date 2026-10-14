""" "Khám phá" in the full-screen menu (phase 18b): the source is read once into its index, then
every step is a recount in memory; the actions open the clone/backup flows with source and filter
already decided.

Every test closes the app's ``Store``: an open aiosqlite connection keeps the interpreter alive.
"""

import io
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console, RenderableType

from tests.fakes import FakeAuth, FakeGateway
from tests.unit.test_menu_backup_restore import make_app
from tgmirror.cli.keys import MenuKey
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import ChannelInfo, MediaKind
from tgmirror.engine import selection
from tgmirror.ui.menu.app import MenuApp
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.explore import ExploreScreen, ListScreen, explore_screen
from tgmirror.ui.menu.screens.wizard import WizardScreen

MakeRuntime = Callable[..., Runtime]
NOW = datetime(2024, 1, 5, tzinfo=UTC)


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, no_color=True, width=120, height=30)
    console.print(renderable)
    return console.export_text()


def fill(gateway: FakeGateway) -> ChannelInfo:
    src = gateway.add_channel("Source", username="srcchan")
    gateway.add_channel("Copy")
    for i in range(1, 7):
        video = i % 2 == 1
        gateway.add_message(
            src.id,
            f"m{i} #{'a' if i < 4 else 'b'}",
            media=MediaKind.VIDEO if video else MediaKind.TEXT,
            hashtags=("#a",) if i < 4 else ("#b",),
            size=1000 if video else None,
            date=NOW,
        )
    return src


@asynccontextmanager
async def app_of(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> AsyncIterator[MenuApp]:
    app = await make_app(make_runtime, tmp_path, gateway, FakeAuth())
    try:
        yield app
    finally:
        await app.store.close()


async def built(screen: ExploreScreen) -> ExploreScreen:
    await screen.on_enter()
    assert screen._task is not None
    await screen._task
    assert await screen.tick() is None
    return screen


@asynccontextmanager
async def explored(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> AsyncIterator[tuple[ExploreScreen, ChannelInfo, MenuApp]]:
    src = fill(gateway)
    async with app_of(make_runtime, tmp_path, gateway) as app:
        yield await built(ExploreScreen(app, src)), src, app


async def test_it_builds_the_index_and_shows_the_tables(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    async with explored(make_runtime, tmp_path, gateway) as (screen, src, app):
        text = plain(screen.render())

        assert "Explore" in text and "6 messages in the index" in text
        assert "By media type" in text and "video" in text and "#a" in text
        assert (app.rt.paths.index_dir / f"{src.id}.db").exists()
        assert len(gateway.calls_to("iter_messages")) == 1


async def test_enter_narrows_x_excludes_and_backspace_goes_back(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    async with explored(make_runtime, tmp_path, gateway) as (screen, _, _):
        explorer = screen._explorer
        assert explorer is not None
        assert [r.key for r in explorer.rows("media")] == ["text", "video"]
        await screen.handle_key(MenuKey.DOWN)  # on "video"

        await screen.handle_key(MenuKey.ENTER)
        assert explorer.report.selected.messages == 3
        assert "video" in plain(screen.render())

        await screen.handle_key(MenuKey.RIGHT)  # the month column
        await screen.handle_key(MenuKey.RIGHT)  # hashtags: #a (2 videos) then #b (1)
        await screen.handle_key("x")  # exclude #a
        assert explorer.report.selected.messages == 1

        await screen.handle_key(MenuKey.BACKSPACE)
        await screen.handle_key(MenuKey.BACKSPACE)
        assert explorer.report.selected.messages == 6
        assert "(not filtered yet)" in plain(screen.render())


async def test_a_row_that_cannot_be_a_filter_says_so_and_changes_nothing(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    async with explored(make_runtime, tmp_path, gateway) as (screen, _, _):
        await screen.handle_key(MenuKey.RIGHT)  # months
        await screen.handle_key("x")

        assert screen._explorer is not None and screen._explorer.steps == []
        assert "A month cannot be excluded" in plain(screen.render())


async def test_s_saves_the_filter_as_last_with_its_ids(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    async with explored(make_runtime, tmp_path, gateway) as (screen, src, app):
        await screen.handle_key(MenuKey.DOWN)
        await screen.handle_key(MenuKey.ENTER)  # video

        await screen.handle_key("s")

        path = selection.last_path(app.rt.paths.selections_dir, src.id)
        saved = selection.load(path, with_ids=True)
        assert saved.snapshot is not None and saved.snapshot.ids == (1, 3, 5)
        assert [m.value for m in saved.filters.include[0].media or ()] == ["video"]
        assert "saved" in plain(screen.render())


async def test_c_opens_the_clone_wizard_with_source_and_filter_decided(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    async with explored(make_runtime, tmp_path, gateway) as (screen, _, _):
        await screen.handle_key(MenuKey.DOWN)
        await screen.handle_key(MenuKey.ENTER)  # video

        result = await screen.handle_key("c")

        assert isinstance(result, tuple) and result[0] == "push"
        wizard = result[1]
        assert isinstance(wizard, WizardScreen)
        await wizard.on_enter()
        # the first question is the destination: source and filter are not asked again
        assert wizard.prompter.asking and wizard.prompter.question is not None
        assert "destination" in wizard.prompter.question.message.lower()
        assert wizard.task is not None
        wizard.task.cancel()


async def test_l_lists_the_selected_messages(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    async with explored(make_runtime, tmp_path, gateway) as (screen, _, _):
        await screen.handle_key(MenuKey.DOWN)
        await screen.handle_key(MenuKey.ENTER)

        result = await screen.handle_key("l")

        assert isinstance(result, tuple) and result[0] == "push"
        listing = result[1]
        assert isinstance(listing, ListScreen)
        text = plain(listing.render())
        assert "showing 3 of 3" in text and "m1" in text and "m5" in text and "m2" not in text
        assert await listing.handle_key(MenuKey.ESC) == "pop"


async def test_esc_leaves_and_u_reads_what_is_newer_into_the_same_index(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    async with explored(make_runtime, tmp_path, gateway) as (screen, src, app):
        gateway.add_message(src.id, "m7", media=MediaKind.PHOTO, date=NOW)

        result: ScreenResult = await screen.handle_key("u")
        assert isinstance(result, tuple) and result[0] == "replace"
        again = result[1]
        assert isinstance(again, ExploreScreen)
        await built(again)

        assert again._explorer is not None
        assert again._explorer.report.scanned.messages == 7
        assert [c.args[1] for c in gateway.calls_to("iter_messages")] == [0, 6]  # only the new
        assert await again.handle_key(MenuKey.ESC) == "pop"


async def test_q_while_reading_stops_and_the_screen_goes_on_with_what_it_has(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    src = fill(gateway)
    async with app_of(make_runtime, tmp_path, gateway) as app:
        screen = ExploreScreen(app, src)
        await screen.handle_key("q")  # pressed before the read starts: it stops at once

        await built(screen)

        assert screen._explorer is not None
        assert screen._explorer.report.scanned.messages < 6
        assert screen._state is not None and not screen._state.complete
        assert "does not reach the end" in plain(screen.render())


async def test_the_menu_entry_asks_for_a_source_then_opens_the_explorer(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    fill(gateway)
    async with app_of(make_runtime, tmp_path, gateway) as app:
        picker = explore_screen(app)  # type: ignore[arg-type]

        await picker.on_enter()
        assert picker.prompter.asking
        await picker.handle_key(MenuKey.ENTER)  # the first channel
        assert picker.task is not None
        result = await picker.task

        assert isinstance(result, tuple) and result[0] == "replace"
        assert isinstance(result[1], ExploreScreen)
