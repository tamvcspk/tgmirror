""" "Xoá tin" in the full-screen menu (Phase 20.2): the same ``DeleteFlow`` as ``tgmirror delete``,
answered by keys — plan, the count typed back, the question about a backup — then ``OpScreen``
drives the safety-net backup and the delete. ``d`` in "Khám phá" hands over a chat and a filter.

Every test closes the app's ``Store``: an open aiosqlite connection keeps the interpreter alive.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from tests.fakes import ACCOUNT, FakeAuth, FakeGateway
from tests.unit.test_menu_backup_restore import asked, make_app, plain, press
from tests.unit.test_menu_explore import explored
from tgmirror.cli.commands.delete import DeleteOptions
from tgmirror.cli.keys import MenuKey
from tgmirror.cli.runtime import Runtime
from tgmirror.core.gateway import ChannelInfo
from tgmirror.engine import backupdir
from tgmirror.engine.ops import begin_op_backup, plan_delete
from tgmirror.filters.model import FilterSpec
from tgmirror.store.ops import OpStatus
from tgmirror.ui.menu.app import MenuApp
from tgmirror.ui.menu.op_screen import OpScreen
from tgmirror.ui.menu.screens.delete import delete_screen
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.main_menu import MainMenuScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t

MakeRuntime = Callable[..., Runtime]
ADS = FilterSpec.from_json('{"include": [{"hashtag": ["#ads"]}]}')


def fill(gateway: FakeGateway) -> ChannelInfo:
    chat = gateway.add_channel("Chat")
    for i in range(3):
        gateway.add_message(chat.id, f"ad {i} #ads", hashtags=("#ads",))
    for i in range(2):
        gateway.add_message(chat.id, f"keep {i}")
    return chat


def ids(gateway: FakeGateway, chat: ChannelInfo) -> list[int]:
    return [m.id for m in gateway.messages[chat.id]]


@asynccontextmanager
async def menu_of(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> AsyncIterator[MenuApp]:
    app = await make_app(make_runtime, tmp_path, gateway, FakeAuth(logged_in=ACCOUNT))
    try:
        yield app
    finally:
        await app.store.close()


async def finish(screen: OpScreen) -> None:
    await screen.on_enter()
    assert screen.task is not None
    await screen.task
    await screen.tick()


async def test_it_plans_asks_the_count_and_a_backup_then_hands_over(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat = fill(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = delete_screen(app, DeleteOptions(src="Chat", filters=ADS))

        assert await screen.on_enter() == "stay"
        assert "3" in asked(screen)  # "type 3 to delete"
        assert "O1" in plain(screen.render())  # the plan is shown above
        await press(screen, "3", MenuKey.ENTER)
        assert asked(screen) == t("delete.confirm_backup", count=3)
        await press(screen, MenuKey.ENTER)  # Yes
        assert "safety" in asked(screen)  # the default directory
        result = await press(screen, MenuKey.ENTER)  # Yes, there

        assert result[0] == "replace" and isinstance(result[1], OpScreen)
        assert ids(gateway, chat) == [1, 2, 3, 4, 5]  # nothing yet: the screen does the work
        await finish(result[1])
        assert ids(gateway, chat) == [4, 5]
        directory = app.rt.paths.data_dir / "safety" / "O1"
        assert [r.id for r in backupdir.iter_records(directory)] == [1, 2, 3]
        text = plain(result[1].render())
        assert str(directory) in text and "3/3" in text


async def test_a_no_to_the_backup_deletes_without_one(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat = fill(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = delete_screen(app, DeleteOptions(src="Chat", filters=ADS))
        await screen.on_enter()
        await press(screen, "3", MenuKey.ENTER)
        result = await press(screen, MenuKey.DOWN, MenuKey.ENTER)  # No

        assert isinstance(result[1], OpScreen)
        await finish(result[1])
        assert ids(gateway, chat) == [4, 5]
        assert not (app.rt.paths.data_dir / "safety").exists()


async def test_another_number_gives_up_and_deletes_nothing(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat = fill(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = delete_screen(app, DeleteOptions(src="Chat", filters=ADS))
        await screen.on_enter()

        result = await press(screen, "4", MenuKey.ENTER)

        assert result == "pop"
        assert ids(gateway, chat) == [1, 2, 3, 4, 5]
        op = await app.store.get_op(1)
        assert op is not None and op.status is OpStatus.CANCELLED


async def test_a_filter_selecting_nothing_says_so(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    gateway.add_channel("Chat")
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        screen = delete_screen(app, DeleteOptions(src="Chat", filters=ADS))

        result = await screen.on_enter()

        assert isinstance(result, tuple) and result[0] == "replace"
        assert isinstance(result[1], InfoScreen)
        assert "Chat" in plain(result[1].render())


async def test_a_backup_stopped_midway_leaves_the_plan_and_deletes_nothing(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat = fill(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        plan = await plan_delete(app.store, gateway, chat, ADS, app.rt.config().limits)
        assert plan.op is not None
        started = await begin_op_backup(app.store, gateway, plan.op, tmp_path / "net")
        screen = OpScreen(
            app.store, gateway, plan.op, limits=app.rt.config().limits, backup=started
        )
        screen._control.request_stop()  # q before the first message is saved

        await finish(screen)

        assert ids(gateway, chat) == [1, 2, 3, 4, 5]
        op = await app.store.get_op(plan.op.id)
        assert op is not None and op.status is OpStatus.PLANNED and not op.backup_done
        text = plain(screen.render())
        assert "O1" in text and "--resume" in text  # how to carry on


async def test_d_in_explore_opens_the_delete_wizard_with_chat_and_filter_decided(
    make_runtime: MakeRuntime, tmp_path: Path, gateway: FakeGateway
) -> None:
    async with explored(make_runtime, tmp_path, gateway) as (screen, _, _):
        await screen.handle_key(MenuKey.DOWN)
        await screen.handle_key(MenuKey.ENTER)  # video

        result = await screen.handle_key("d")

        assert isinstance(result, tuple) and result[0] == "push"
        wizard = result[1]
        assert isinstance(wizard, WizardScreen)
        await wizard.on_enter()
        # the plan is made and the first question is the count: nothing else is asked
        assert wizard.prompter.asking
        assert "3" in asked(wizard)
        assert wizard.task is not None
        wizard.task.cancel()


async def test_the_main_menu_offers_it(make_runtime: MakeRuntime, tmp_path: Path) -> None:
    gateway = FakeGateway()
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        main = MainMenuScreen(app)
        await main.on_enter()

        assert t("menu.item_delete") in plain(main.render())


async def test_badge_shows_a_delete_held_by_another_process_not_our_own(
    make_runtime: MakeRuntime, tmp_path: Path
) -> None:
    gateway = FakeGateway()
    chat = fill(gateway)
    async with menu_of(make_runtime, tmp_path, gateway) as app:
        plan = await plan_delete(app.store, gateway, chat, ADS, app.rt.config().limits)
        assert plan.op is not None
        await app.store.start_op(plan.op.id)  # "elsewhere": no OpScreen here

        await app._refresh_badge()
        assert app._badge is not None

        app.own_op_id = plan.op.id  # as pushing its OpScreen would have set it
        await app._refresh_badge()
        assert app._badge is None
