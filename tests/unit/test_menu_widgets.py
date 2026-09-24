"""``SelectList``/``TypeToFilter`` (``ui/menu/widgets.py``): pure, no terminal needed — same
``Console(record=True)`` pattern as ``ui/tui.py``'s tests."""

import io

from rich.console import Console, RenderableType

from tgmirror.cli.keys import MenuKey
from tgmirror.ui.menu.widgets import SelectList, TypeToFilter


def plain(renderable: RenderableType) -> str:
    console = Console(file=io.StringIO(), record=True, width=200, no_color=True)
    console.print(renderable)
    return console.export_text()


def test_select_list_starts_on_the_first_item() -> None:
    items = [("Sao chép mới", "clone"), ("Trạng thái", "status")]
    widget: SelectList[str] = SelectList(items=items)

    assert "▸ Sao chép mới" in plain(widget.render())
    assert "▸ Trạng thái" not in plain(widget.render())


def test_down_then_enter_returns_the_second_item() -> None:
    items = [("A", "a"), ("B", "b")]
    widget: SelectList[str] = SelectList(items=items)

    assert widget.handle_key(MenuKey.DOWN) is None
    assert widget.index == 1
    assert "▸ B" in plain(widget.render())
    assert widget.handle_key(MenuKey.ENTER) == "b"


def test_up_wraps_from_the_first_item_to_the_last() -> None:
    widget: SelectList[str] = SelectList(items=[("A", "a"), ("B", "b"), ("C", "c")])

    widget.handle_key(MenuKey.UP)

    assert widget.index == 2


def test_empty_list_ignores_every_key() -> None:
    widget: SelectList[str] = SelectList(items=[])

    assert widget.handle_key(MenuKey.ENTER) is None
    assert widget.handle_key(MenuKey.DOWN) is None


def test_type_to_filter_builds_and_shrinks_the_text() -> None:
    box = TypeToFilter()

    assert box.handle_key("a") is True
    assert box.handle_key("b") is True
    assert box.text == "ab"

    assert box.handle_key(MenuKey.BACKSPACE) is True
    assert box.text == "a"
    assert "a" in plain(box.render())


def test_type_to_filter_backspace_on_empty_text_changes_nothing() -> None:
    box = TypeToFilter()

    assert box.handle_key(MenuKey.BACKSPACE) is False


def test_type_to_filter_ignores_navigation_keys() -> None:
    box = TypeToFilter()

    assert box.handle_key(MenuKey.ENTER) is False
    assert box.handle_key(MenuKey.UP) is False
    assert box.text == ""
