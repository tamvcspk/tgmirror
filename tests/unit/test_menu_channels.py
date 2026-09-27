"""``ChannelsScreen``: type-to-filter plus ↑/↓ to move the highlighted row (Phase 15b bug fix).

Before this, the screen only owned a ``TypeToFilter`` — no ``SelectList`` — so Up/Down did nothing
at all, and a joined-channel list longer than the terminal simply overflowed the frame with no way
to scroll to the rest. Now it follows the same shape as the wizard's source picker
(``ui/menu/prompter.py::SelectQuestion``): filter narrows the list, Up/Down move within it.
"""

from dataclasses import dataclass

from tests.fakes import FakeGateway
from tgmirror.cli.keys import MenuKey
from tgmirror.core.gateway import ChannelInfo
from tgmirror.ui.menu.screens.channels import ChannelsScreen


@dataclass
class _StubApp:
    gateway: FakeGateway


async def _screen(*titles: str) -> ChannelsScreen:
    gateway = FakeGateway()
    for i, title in enumerate(titles):
        gateway.channels[i] = ChannelInfo(id=i, title=title)
    screen = ChannelsScreen(_StubApp(gateway))  # type: ignore[arg-type]
    await screen.on_enter()
    return screen


async def test_up_down_move_the_highlighted_row() -> None:
    screen = await _screen("A", "B", "C")

    assert screen._list.index == 0
    await screen.handle_key(MenuKey.DOWN)
    assert screen._list.index == 1
    await screen.handle_key(MenuKey.UP)
    assert screen._list.index == 0


async def test_filtering_narrows_and_resets_the_selection() -> None:
    screen = await _screen("Alpha", "Beta", "Gamma")

    await screen.handle_key(MenuKey.DOWN)  # move off index 0 first
    for ch in "ga":
        await screen.handle_key(ch)

    assert len(screen._list.items) == 1
    assert "Gamma" in screen._list.items[0][0]
    assert screen._list.index == 0


async def test_esc_pops() -> None:
    screen = await _screen("A")

    assert await screen.handle_key(MenuKey.ESC) == "pop"
