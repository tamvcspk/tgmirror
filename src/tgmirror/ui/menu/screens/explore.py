""" "Khám phá": count a source, narrow it row by row, then copy exactly that
(docs/07-inspect-transform.md 4.5, phase 18b).

The same ``inspect`` machinery as the CLI, on the source's local index: the screen brings the index
up to date first (read through ``FloodGuard``, ``q`` stops it and the screen goes on with what it
has), loads it, and from then on every step is a recount in memory — no Telegram call.

The actions open the very flows of the commands (``CloneFlow``, ``BackupFlow``) with the source and
the filter already decided, so their other questions — destination, decision D3, the confirmation —
are asked as always (rule 7: nothing here that only the menu can do).
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from tgmirror.cli import wizard
from tgmirror.cli.commands.backup import BackupOptions
from tgmirror.cli.commands.clone import CloneOptions
from tgmirror.cli.commands.delete import DeleteOptions
from tgmirror.cli.errors import describe
from tgmirror.cli.keys import MenuKey
from tgmirror.core.errors import FloodWait, TgMirrorError
from tgmirror.core.gateway import ChannelInfo, ChatKind, Unit
from tgmirror.engine import index as indexing
from tgmirror.engine import inspect as inspecting
from tgmirror.engine import planner, selection
from tgmirror.engine import transform as transforms
from tgmirror.engine.explore import CYCLE, FIXED, Explorer, NotFilterable
from tgmirror.engine.flood import Interrupted, inspect_guard
from tgmirror.engine.transform import Transform, TransformError
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterError
from tgmirror.store.index import MetadataIndex
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import Screen, ScreenResult
from tgmirror.ui.menu.screens.backup import backup_screen
from tgmirror.ui.menu.screens.clone import clone_screen
from tgmirror.ui.menu.screens.delete import delete_screen
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.menu.widgets import Fit, SelectList
from tgmirror.ui.messages import t
from tgmirror.ui.progress import size
from tgmirror.ui.tables import row_label

LIST_LIMIT = 500  # rows the list screen shows
NAP_STEP = 0.5


class _Notices:
    """``FloodGuard``'s notifier: the last thing Telegram made the read wait for."""

    def __init__(self) -> None:
        self.line: str | None = None

    def notice(self, code: str, **params: object) -> None:
        self.line = t(f"inspect.{code}", **params)


class ExploreScreen(Screen):
    tick_interval = 0.4

    def __init__(self, app: AppContext, channel: ChannelInfo, *, refresh: bool = False) -> None:
        self._app = app
        self._channel = channel
        self._refresh = refresh
        self._task: asyncio.Task[None] | None = None
        self._stop = False
        self._read = (0, 0)  # messages read, up to id
        self._notices = _Notices()
        self._explorer: Explorer | None = None
        self._units: list[Unit] = []
        self._state: indexing.IndexInfo | None = None
        self._flood: FloodWait | None = None
        self._note: str | None = None
        self._focus = 0  # the column the cursor is in
        self._cursor = [0, 0, 0, 0]  # the highlighted row of each column
        self._extra = 0  # which of ``CYCLE`` the last column shows
        # phase 21: a transform loaded with ``t``: its routing rules make the "destination topic"
        # column, and it goes with the source into the clone wizard
        self._transform = Transform()

    # ---- building the index -----------------------------------------------------------------

    @property
    def footer_hint(self) -> str:  # type: ignore[override]
        key = "explore.footer" if self._explorer is not None else "explore.footer_building"
        return t(key)

    async def on_enter(self) -> ScreenResult | None:
        self._task = asyncio.create_task(self._build())
        return None

    async def _build(self) -> None:
        app, channel = self._app, self._channel
        forum = channel.kind is ChatKind.FORUM

        async def nap(seconds: float) -> None:
            remaining = seconds
            while remaining > 0:
                step = min(remaining, NAP_STEP)
                await asyncio.sleep(step)
                remaining -= step
                if self._stop:
                    raise Interrupted

        guard = await inspect_guard(
            app.store, app.rt.config().limits, notifier=self._notices, nap=nap
        )
        reader = guard.reader(app.gateway)
        titles: dict[int, str] = {}
        if forum:
            titles = {topic.id: topic.title for topic in await reader.list_topics(channel.id)}
        known = indexing.IndexedSource(
            id=channel.id,
            title=channel.title,
            username=channel.username,
            kind=channel.kind,
            noforwards=channel.noforwards,
            topics=titles,
        )
        index = await MetadataIndex.open(indexing.path_of(app.rt.paths.index_dir, channel.id))
        try:
            try:
                await indexing.update(
                    index,
                    reader,
                    known,
                    refresh=self._refresh,
                    stop=lambda: self._stop,
                    on_progress=lambda read, last: setattr(self, "_read", (read, last)),
                )
            except Interrupted:
                pass
            except FloodWait as exc:  # too long to sit out: go on with what the index has
                self._flood = exc
            self._state = await indexing.info(index)
            async for item in planner.units(indexing.IndexReader(index, known), channel.id):
                assert isinstance(item, Unit)  # no matcher: nothing is skipped
                self._units.append(item)
        finally:
            await index.close()
        self._explorer = Explorer(
            self._units, forum=forum, topic_titles=titles, rules=self._transform.topics
        )

    async def tick(self) -> ScreenResult | None:
        task = self._task
        if task is None or not task.done():
            return None
        if task.cancelled():
            return "pop"
        exc = task.exception()
        if exc is None:
            return None
        if isinstance(exc, TgMirrorError):
            return ("replace", InfoScreen([describe(exc)]))
        raise exc

    # ---- drawing ----------------------------------------------------------------------------

    def render(self) -> RenderableType:
        explorer = self._explorer
        head: list[RenderableType] = [Text(t("explore.title", src=self._channel.title), "bold")]
        if explorer is None:
            read, last = self._read
            head.append(Text(t("inspect.indexing", messages=f"{read:,}", last_id=last)))
            if self._notices.line:
                head.append(Text(self._notices.line, style="yellow"))
            return Group(*head)
        state = self._state
        scanned = explorer.report.scanned.messages
        if state is not None and state.updated_at is not None:
            when = state.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
            head.append(Text(t("explore.index_line", messages=f"{scanned:,}", when=when), "dim"))
        if state is not None and not state.complete:
            head.append(Text(t("explore.partial"), style="yellow"))
        if self._flood is not None:
            head.append(Text(t("explore.flood", seconds=self._flood.seconds), style="yellow"))
        if scanned == 0:
            head.append(Text(t("explore.empty")))
            return Group(*head)
        steps = "  ".join(step.label for step in explorer.steps) or t("explore.no_filter")
        selected = explorer.report.selected
        head.append(Text(t("explore.filter", steps=steps)))
        head.append(
            Text(
                t(
                    "inspect.selected",
                    messages=f"{selected.messages:,}",
                    units=f"{selected.units:,}",
                    albums=f"{selected.albums:,}",
                    size=size(selected.bytes),
                )
            )
        )
        if self._note:
            head.append(Text(self._note, style="yellow"))
        return Fit(Group(*head), self._columns)

    def _shown(self) -> list[str]:
        assert self._explorer is not None
        extras = [by for by in CYCLE if by in self._explorer.tables]
        return [*FIXED, extras[self._extra % len(extras)]]

    def _columns(self, rows: int | None) -> RenderableType:
        assert self._explorer is not None
        explorer = self._explorer
        height = max((rows or 12) - 2, 3)
        outer = Table.grid(expand=True, padding=(0, 2))
        shown = self._shown()
        for _ in shown:
            outer.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        cells: list[RenderableType] = []
        for i, by in enumerate(shown):
            focused = i == self._focus
            table = explorer.rows(by)
            cursor = min(self._cursor[i], max(len(table) - 1, 0))
            first = min(max(cursor - height // 2, 0), max(len(table) - height, 0))
            grid = Table.grid(expand=True)
            grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
            grid.add_column(justify="right", no_wrap=True)
            title = t(f"inspect.by.{by}")
            grid.add_row(Text(title, style="bold cyan" if focused else "bold"), "")
            if not table:
                grid.add_row(Text(t("inspect.no_rows"), style="dim"), "")
            for j in range(first, min(first + height, len(table))):
                row = table[j]
                on = focused and j == cursor
                style = "bold cyan" if on else None
                grid.add_row(
                    Text(("▸ " if on else "  ") + row_label(by, row), style=style),
                    Text(f"{row.messages:,}", style=style),
                )
            cells.append(grid)
        outer.add_row(*cells)
        return outer

    # ---- keys -------------------------------------------------------------------------------

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if self._explorer is None:  # still reading: any "leave" key stops the read, nothing else
            if key in (MenuKey.ESC, "q", "Q"):
                self._stop = True
            return "stay"
        self._note = None
        if key == MenuKey.ESC:
            return "pop"
        if self._explorer.report.scanned.messages == 0:
            return "stay"
        handled = self._navigate(key) or self._narrow(key)
        if handled:
            return "stay"
        return self._act(key)

    def _navigate(self, key: MenuKey | str) -> bool:
        assert self._explorer is not None
        shown = self._shown()
        if key == MenuKey.LEFT:
            self._focus = (self._focus - 1) % len(shown)
        elif key == MenuKey.RIGHT:
            self._focus = (self._focus + 1) % len(shown)
        elif key in (MenuKey.UP, MenuKey.DOWN):
            count = len(self._explorer.rows(shown[self._focus]))
            if count:
                step = -1 if key == MenuKey.UP else 1
                self._cursor[self._focus] = (self._cursor[self._focus] + step) % count
        elif key == MenuKey.TAB:
            self._extra += 1
            self._cursor[3] = 0
        else:
            return False
        return True

    def _narrow(self, key: MenuKey | str) -> bool:
        assert self._explorer is not None
        explorer = self._explorer
        if key == MenuKey.BACKSPACE:
            explorer.pop()
            return True
        if key not in (MenuKey.ENTER, "x", "X"):
            return False
        by = self._shown()[self._focus]
        rows = explorer.rows(by)
        if not rows:
            return True
        row = rows[min(self._cursor[self._focus], len(rows) - 1)]
        kind = "include" if key == MenuKey.ENTER else "exclude"
        try:
            explorer.add(kind, by, row.key)
        except NotFilterable as exc:
            self._note = t(exc.reason)
        except FilterError as exc:
            self._note = str(exc.detail)
        return True

    def _act(self, key: MenuKey | str) -> ScreenResult:
        assert self._explorer is not None
        explorer = self._explorer
        letter = key if len(key) == 1 else ""
        if letter in ("l", "L"):
            return ("push", ListScreen(self._lines(), explorer.report.selected.messages))
        if letter in ("s", "S"):
            self._note = self._save()
        elif letter in ("t", "T"):
            return ("push", self._transform_screen())
        elif letter in ("c", "C"):
            clone = CloneOptions(
                src=str(self._channel.id), filters=explorer.spec, transform=self._transform
            )
            return ("push", clone_screen(self._app, clone))
        elif letter in ("b", "B"):
            backup = BackupOptions(src=str(self._channel.id), filters=explorer.spec)
            return ("push", backup_screen(self._app, backup))
        elif letter in ("d", "D"):
            delete = DeleteOptions(src=str(self._channel.id), filters=explorer.spec)
            return ("push", delete_screen(self._app, delete))
        elif letter == "u":
            return ("replace", ExploreScreen(self._app, self._channel))
        elif letter == "R":
            return ("replace", ExploreScreen(self._app, self._channel, refresh=True))
        return "stay"

    def _transform_screen(self) -> WizardScreen:
        """Ask for a transform file and load its routing rules into the explorer (rule 7: the
        same file ``--transform`` takes). An empty answer drops them. A bad file is said, and the
        rules in use stay."""

        async def flow(prompter: MenuPrompter) -> ScreenResult:
            raw = (await prompter.path(t("explore.transform_prompt"))).strip().strip("\"'")
            explorer = self._explorer
            assert explorer is not None
            if not raw:
                self._transform = Transform()
                explorer.set_rules(None)
                self._note = t("explore.transform_dropped")
                return "pop"
            path = await asyncio.to_thread(lambda: Path(raw).expanduser())
            try:
                loaded = await asyncio.to_thread(transforms.load, path)
            except (TransformError, OSError) as exc:
                text = exc.detail if isinstance(exc, TransformError) else str(exc)
                return ("replace", InfoScreen([str(text)]))
            if not loaded.topics.routing:
                return ("replace", InfoScreen([t("explore.transform_none", path=path)]))
            self._transform = loaded
            explorer.set_rules(loaded.topics)
            self._note = t("explore.transform_loaded")
            return "pop"

        return WizardScreen(t("explore.transform_title"), flow)

    def _selected_units(self) -> list[Unit]:
        assert self._explorer is not None
        spec = self._explorer.spec
        if spec.is_empty:
            return list(self._units)
        matcher = Matcher(spec)
        return [unit for unit in self._units if matcher.matches(unit)]

    def _lines(self) -> list[str]:
        out: list[str] = []
        for unit in self._selected_units():
            for m in unit.messages:
                row = inspecting.describe_message(m)
                text = row["text"] or ""
                out.append(
                    f"{m.id:>8}  {str(row['date'])[:10]}  {m.media.value:<10} "
                    f"{size(m.size) if m.size else '':>9}  {text[:70]}"
                )
                if len(out) >= LIST_LIMIT:
                    return out
        return out

    def _save(self) -> str:
        assert self._explorer is not None
        now = datetime.now(UTC)
        units = self._selected_units()
        ids = sorted(i for unit in units for i in unit.ids)
        origin = selection.SelectionSource(
            id=self._channel.id, title=self._channel.title or None, backup=None
        )
        snapshot = selection.Snapshot(now, self._explorer.report.last_id, len(ids), tuple(ids))
        path = selection.last_path(self._app.rt.paths.selections_dir, self._channel.id)
        selection.dump(selection.Selection(self._explorer.spec, origin, snapshot), path, now=now)
        return t("explore.saved", count=f"{len(ids):,}")


class ListScreen(Screen):
    """The messages the current filter selects (the first ``LIST_LIMIT``), to read and scroll."""

    def __init__(self, lines: list[str], total: int) -> None:
        self._lines = lines
        self._total = total
        self._list: SelectList[None] = SelectList(items=[(line, None) for line in lines])
        self.footer_hint = t("menu.footer_filter")

    def render(self) -> RenderableType:
        head: Callable[[], RenderableType] = lambda: Text(  # noqa: E731
            t("explore.list_title", shown=f"{len(self._lines):,}", total=f"{self._total:,}"),
            "bold",
        )
        if not self._lines:
            return Group(head(), Text(t("inspect.no_rows")))
        return Fit(head(), self._list.render)

    async def handle_key(self, key: MenuKey | str) -> ScreenResult:
        if key == MenuKey.ESC:
            return "pop"
        if key in (MenuKey.UP, MenuKey.DOWN):
            self._list.handle_key(key)
        return "stay"


def explore_screen(app: AppContext) -> WizardScreen:
    """Pick the source (the clone wizard's own question), then open the explorer on it."""

    async def flow(prompter: MenuPrompter) -> ScreenResult:
        channels = await app.gateway.list_channels()
        if not channels:
            return ("replace", InfoScreen([t("channels.empty")]))
        channel = await wizard.pick_source(prompter, channels)
        return ("replace", ExploreScreen(app, channel))

    return WizardScreen(t("menu.item_explore"), flow)
