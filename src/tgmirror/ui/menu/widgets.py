"""Small, pure, keyboard-driven building blocks screens compose (not ``Screen``s themselves: a
screen owns one or more of these and turns their result into a ``ScreenResult``)."""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from rich.console import Group, RenderableType
from rich.text import Text

from tgmirror.cli.keys import MenuKey

T = TypeVar("T")


@dataclass
class SelectList(Generic[T]):
    """A list of (label, value) pairs; the highlighted one is returned on Enter."""

    items: list[tuple[str, T]]
    index: int = 0

    def __post_init__(self) -> None:
        self.index = min(self.index, max(len(self.items) - 1, 0))

    def render(self) -> RenderableType:
        if not self.items:
            return Text("")
        lines = []
        for i, (label, _) in enumerate(self.items):
            marker = "▸ " if i == self.index else "  "
            lines.append(Text(marker + label, style="bold cyan" if i == self.index else None))
        return Group(*lines)

    def handle_key(self, key: MenuKey | str) -> T | None:
        """The chosen value on Enter; otherwise ``None`` (Up/Down already moved the selection)."""
        if not self.items:
            return None
        if key == MenuKey.UP:
            self.index = (self.index - 1) % len(self.items)
        elif key == MenuKey.DOWN:
            self.index = (self.index + 1) % len(self.items)
        elif key == MenuKey.ENTER:
            return self.items[self.index][1]
        return None


@dataclass
class TypeToFilter:
    """A one-line search box: plain characters extend it, Backspace shortens it."""

    text: str = ""
    matched: list[str] = field(default_factory=list)

    def handle_key(self, key: MenuKey | str) -> bool:
        """``True`` when ``text`` changed (the screen should recompute what matches)."""
        if key is MenuKey.BACKSPACE:
            if not self.text:
                return False
            self.text = self.text[:-1]
            return True
        if isinstance(key, str) and len(key) == 1 and key.isprintable():
            self.text += key
            return True
        return False

    def render(self) -> RenderableType:
        return Text(f"/ {self.text}", style="dim") if self.text else Text("")
