"""``Screen``: one page of the full-screen menu app (``ui/menu/app.py``).

A stack of these is drawn into a fixed header/body/footer ``Layout`` (``ui/menu/frame.py``); only
``body`` changes when the screen changes. Every screen renders synchronously (pure ``render()``,
testable the same way as ``TuiReporter.render()`` — no terminal needed) and reacts to one key (or,
on an idle timeout, ``tick()``) at a time.
"""

from typing import Literal

from rich.console import RenderableType

from tgmirror.cli.keys import MenuKey

Push = tuple[Literal["push"], "Screen"]
# this screen gives way to another: Esc from that one skips this one (a finished wizard -> its run)
Replace = tuple[Literal["replace"], "Screen"]
Quit = tuple[Literal["quit"], int]  # quit with this exit code (bare "quit" below means code 0)
ScreenResult = Literal["stay", "pop", "quit"] | Push | Replace | Quit


class Screen:
    """Base class with the defaults most screens want; override what differs."""

    footer_hint: str = ""
    tick_interval: float = 1.5  # how often ``tick()`` runs when no key arrives meanwhile
    run_id: int | None = None  # the run this screen drives, if any (``MenuApp`` calls it "ours")

    def render(self) -> RenderableType:
        raise NotImplementedError

    async def on_enter(self) -> ScreenResult | None:
        """Called once, right after the app pushes this screen (may fetch data the sync
        ``__init__`` cannot: it runs before the first ``render()``). A screen that decides right
        away it has nothing to show (``ResumeScreen`` with a single pair: no real choice to make)
        can return a ``ScreenResult`` here too — ``MenuApp`` applies it the same as one from
        ``handle_key``/``tick``, chaining straight into the next screen instead of flashing this
        one first."""
        return None

    async def on_return(self) -> ScreenResult | None:
        """Called when the screen above this one pops and this one is on top again (the main
        menu re-reads what it offers: someone may have logged in, or a new pair may exist)."""
        return None

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        return "stay"

    async def tick(self) -> ScreenResult | None:
        """Called after ``tick_interval`` seconds with no key: screens that refresh on their own
        (the status dashboard, a run in progress) override this. ``None`` behaves like ``"stay"``
        (kept separate so a screen that never overrides it need not return a literal)."""
        return None
