"""The header/footer chrome around every screen, and the ``Layout`` that holds it plus the body.

Kept deliberately plain (``Text``/``Rule``, no boxed panels): the rest of the app's Rich usage
(``ui/tui.py``) is plain lines too, and a full-screen frame only needs a visible top/bottom edge,
not decoration.
"""

from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.rule import Rule
from rich.text import Text

from tgmirror.ui.messages import t


def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=2),
        Layout(name="body"),
        Layout(name="footer", size=1),
    )
    return layout


def header(who: str | None, badge: str | None) -> RenderableType:
    line = Text("tgmirror", style="bold")
    line.append("  " + who if who else "  " + t("menu.not_logged_in"), style="dim" if who else "")
    if badge:
        line.append("   " + badge, style="yellow")
    return Group(line, Rule(style="dim"))


def footer(hint: str) -> RenderableType:
    return Text(hint, style="dim")
