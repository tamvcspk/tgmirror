"""``tgmirror backup``: save a source to a directory on disk, right now (docs/06-lo-trinh.md,
Phase 11a).

Wizard steps 1-4 (source, directory, decision D3, filter), a preview and one confirmation, then
the backup runs in the foreground like ``clone`` — no background process, no schedule. Re-running
with the same directory continues where it left off (delta, by the highest id already in
``messages.jsonl``); the filter can only be set on the first run of a directory
(``engine.backup.FiltersChanged`` otherwise — a backup keeps no record of what an old filter
skipped, so it cannot safely resume with a new one; back up into a new directory instead).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli import wizard
from tgmirror.cli.errors import Declined, UsageProblem, run
from tgmirror.cli.filter_options import (
    AlbumOption,
    ContainsOption,
    ExcludeMediaOption,
    ExcludeRegexOption,
    FilterFileOption,
    FromUserOption,
    HashtagOption,
    MaxSizeOption,
    MediaOption,
    MinSizeOption,
    RegexOption,
    SinceOption,
    TopicOption,
    UntilOption,
    collect,
)
from tgmirror.cli.interrupt import stop_on_interrupt
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.core.gateway import ChannelInfo, ChatKind, TelegramGateway, TopicInfo
from tgmirror.engine import preview
from tgmirror.engine.backup import BackupWriter, WrongSource, begin_backup
from tgmirror.engine.backupdir import BackupManifest, read_manifest
from tgmirror.engine.endpoints import SourceRestricted, find_channel
from tgmirror.engine.runner import RunControl
from tgmirror.engine.runs import check_runnable
from tgmirror.filters.model import FilterSpec
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store, utc_now
from tgmirror.store.runs import RunStatus
from tgmirror.ui.messages import t
from tgmirror.ui.progress import BackupLineReporter
from tgmirror.ui.prompts import Prompter, run_steps
from tgmirror.ui.tables import channel_label

ADMIN_ACK_FLAG = "--yes-i-administer-this-channel"
EXIT_INTERRUPTED = 130


def backup(
    ctx: typer.Context,
    src: Annotated[
        str | None,
        typer.Argument(help='Source: "@username" (quoted in PowerShell), id or exact title.'),
    ] = None,
    directory: Annotated[
        Path | None,
        typer.Argument(help="Directory to back up into (created if missing; delta on a re-run)."),
    ] = None,
    admin_ack: Annotated[
        bool,
        typer.Option(
            "--yes-i-administer-this-channel",
            help="Source restricts saving content: say that you own it (also through another "
            "account) and may copy it. You take full responsibility for that: tgmirror cannot "
            "check it.",
        ),
    ] = False,
    media: MediaOption = None,
    hashtag: HashtagOption = None,
    contains: ContainsOption = None,
    regex: RegexOption = None,
    exclude_regex: ExcludeRegexOption = None,
    exclude_media: ExcludeMediaOption = None,
    since: SinceOption = None,
    until: UntilOption = None,
    min_size: MinSizeOption = None,
    max_size: MaxSizeOption = None,
    album: AlbumOption = None,
    filter_file: FilterFileOption = None,
    from_user: FromUserOption = None,
    topic: TopicOption = None,
    pushdown: Annotated[
        bool,
        typer.Option(
            "--pushdown/--no-pushdown",
            help="Let Telegram narrow the reading by filter (default). --no-pushdown reads "
            "everything and filters here: slower, for checking that nothing is missed.",
        ),
    ] = True,
    preview_flag: Annotated[
        bool | None,
        typer.Option(
            "--preview/--no-preview",
            help="Show how many of the first messages match (default: on a terminal, when "
            "a filter is set and --yes is not given).",
        ),
    ] = None,
    force_takeover: Annotated[
        bool,
        typer.Option(
            "--force-takeover",
            help="Back up even if another process seems to hold this directory (only if dead).",
        ),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Save every message of a source into a directory; Ctrl+C stops it, saving progress.

    Without --src/--dir it asks (wizard); with them it never prompts (except a question of its
    own, see below). Running it again on the same directory copies only what is newer, with the
    same filter (a new one is refused: back up into a new directory to change it).

    Keys while it runs: p pause, r resume, q stop.

    A source that restricts saving content needs your own statement,
    --yes-i-administer-this-channel, for which you take full responsibility (an admin account can
    answer the question instead; any other account needs the flag).

    Example: tgmirror backup "@my_channel" ./backups/my_channel --media video --since 2024-01-01
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        filters = collect(
            media=media,
            hashtag=hashtag,
            contains=contains,
            regex=regex,
            exclude_regex=exclude_regex,
            exclude_media=exclude_media,
            since=since,
            until=until,
            min_size=min_size,
            max_size=max_size,
            album=album,
            filter_file=filter_file,
            from_user=from_user,
            topic=topic,
        )  # a bad filter is a usage error before anything is asked or written
        options = BackupOptions(
            src=src,
            dir=directory,
            admin_ack=admin_ack,
            filters=filters,
            pushdown=pushdown,
            preview=preview_flag,
            yes=yes,
            force_takeover=force_takeover,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            if not channels:
                typer.echo(t("channels.empty"))
                raise typer.Exit(1)
            async with opened_store(rt) as store:
                flow = BackupFlow(rt, store, conn.gateway, channels, options)
                await run_steps(rt.prompter, flow.steps())
                ready = flow.ready()
                backup_row, manifest = await begin_backup(
                    store,
                    conn.gateway,
                    ready.source,
                    ready.directory,
                    filters_json=ready.filters_json,
                    protected_ack=ready.protected_ack,
                    force=options.force_takeover,
                )
                typer.echo(
                    t(
                        "backup.start",
                        id=backup_row.id,
                        src=channel_label(ready.source),
                        dir=ready.directory,
                    )
                )
                await execute(
                    rt,
                    store,
                    conn.gateway,
                    backup_row,
                    ready.directory,
                    manifest,
                    pushdown=pushdown,
                )

    run(rt, command())


@dataclass(frozen=True, slots=True)
class BackupOptions:
    """The flags of ``tgmirror backup`` (filters already parsed). Defaults are what an empty
    wizard invocation passes: nothing given, so every step asks."""

    src: str | None = None
    dir: Path | None = None
    admin_ack: bool = False
    filters: FilterSpec | None = None
    pushdown: bool = True
    preview: bool | None = None
    yes: bool = False
    force_takeover: bool = False


@dataclass(frozen=True, slots=True)
class BackupReady:
    """What the flow settled on: source, directory and what to pass ``begin_backup``."""

    source: ChannelInfo
    directory: Path
    filters_json: str | None
    protected_ack: bool


class BackupFlow:
    """Everything ``tgmirror backup`` does before anything is written: wizard steps 1-4 and the
    one confirmation, as a list of steps (``steps()``) over shared state — mirrors
    ``cli/commands/clone.py::CloneFlow``, minus what backup has no equivalent of (a destination
    channel, a strategy/mode choice, ``--fresh``).
    """

    def __init__(
        self,
        rt: Runtime,
        store: Store,
        gateway: TelegramGateway,
        channels: Sequence[ChannelInfo],
        options: BackupOptions,
        *,
        prompter: Prompter | None = None,
        interactive: bool | None = None,
        echo: Callable[..., None] = typer.echo,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._channels = channels
        self._o = o = options
        self._prompter = prompter or rt.prompter
        self._interactive = rt.interactive if interactive is None else interactive
        self._echo = echo
        # the wizard asks the rest only if it also asked for source and directory
        self._asked = self._interactive and not o.yes and (o.src is None or o.dir is None)
        self._source: ChannelInfo | None = None
        self._dir: Path | None = None
        self._existing: BackupManifest | None = None
        self._take_responsibility = o.admin_ack
        self._filters = o.filters

    def steps(self) -> list[Callable[[], Awaitable[None]]]:
        return [
            self._pick_source,
            self._pick_dir,
            self._check_existing,
            self._confirm_d3,
            self._pick_filters,
            self._confirm,
        ]

    def ready(self) -> BackupReady:
        assert self._source is not None and self._dir is not None, "run every step first"
        filters_json = None if self._filters is None else self._filters.to_json()
        return BackupReady(self._source, self._dir, filters_json, self._take_responsibility)

    async def _pick_source(self) -> None:
        if self._o.src is not None:
            self._source = find_channel(self._channels, self._o.src)
        elif self._interactive:
            self._source = await wizard.pick_source(self._prompter, self._channels)
        else:
            raise UsageProblem("err.missing_flag", flag="SRC")

    async def _pick_dir(self) -> None:
        if self._o.dir is not None:
            given = self._o.dir
            self._dir = await asyncio.to_thread(given.resolve)
        elif self._interactive:
            self._dir = await wizard.ask_backup_dir(self._prompter)
        else:
            raise UsageProblem("err.missing_flag", flag="DIR")

    async def _check_existing(self) -> None:
        """Fail before asking anything else, the same way ``CloneFlow._read_history`` does: a
        directory that cannot be backed up into right now, or already holds a different source."""
        assert self._dir is not None and self._source is not None
        self._existing = read_manifest(self._dir)
        if self._existing is not None and self._existing.src_id != self._source.id:
            raise WrongSource(self._dir, self._existing.src_title)
        if (last := await self._store.latest_backup(str(self._dir))) is not None:
            check_runnable(last, utc_now())

    async def _confirm_d3(self) -> None:
        """Decision D3: a backup always downloads (there is no server-side "copy" for a
        directory), so a source that restricts saving content needs the same statement strategy B
        does — the flag, or (interactively) the question that stands in for it, exactly as
        ``CloneFlow`` asks it. Refuses right here, before the filter question or anything else,
        the same way ``plan_endpoints`` does for ``clone`` — not deferred to ``begin_backup``,
        which still re-checks independently (hard rule 1's setup exception) because the source may
        have changed since, and because the flags-only path never runs this step at all."""
        source = self._source
        assert source is not None
        if not source.noforwards:
            return
        already_acked = self._existing is not None and self._existing.protected_ack
        if self._take_responsibility or already_acked:
            self._take_responsibility = True
            return
        if source.is_admin:
            if not self._interactive:
                raise UsageProblem("err.needs_admin_ack", title=source.title)
            question = t("clone.confirm_protected", title=source.title)
            if not await self._prompter.confirm(question, False):
                raise Declined
            self._take_responsibility = True
            return
        if self._interactive:
            question = t("clone.confirm_unadministered", title=source.title, flag=ADMIN_ACK_FLAG)
            typed = await self._prompter.text(question)
            if typed.strip() == ADMIN_ACK_FLAG:
                self._take_responsibility = True
                return
        raise SourceRestricted(source)

    async def _pick_filters(self) -> None:
        if self._o.filters is not None:
            return  # explicit flag: begin_backup checks it against any existing manifest itself
        if self._existing is not None:
            self._filters = None  # nothing given, directory already has one: keep it
            if self._asked:
                self._echo(t("backup.filter_kept"))
            return
        if self._asked:
            assert self._source is not None
            topics: Sequence[TopicInfo] = ()
            if self._source.kind is ChatKind.FORUM:
                topics = await self._gateway.list_topics(self._source.id)
            self._filters = await wizard.pick_filters(self._prompter, can_keep=False, topics=topics)

    async def _confirm(self) -> None:
        """Wizard step 5 (the preview, when shown) and the one question before it starts."""
        assert self._source is not None
        spec = self._effective_filters()
        if spec is not None:
            await self._preview(self._source, spec)
        await self._confirm_start()

    def _effective_filters(self) -> FilterSpec | None:
        if self._filters is not None:
            return self._filters
        if self._existing is not None:
            return FilterSpec.from_json(self._existing.filters_json)
        return None

    async def _preview(self, source: ChannelInfo, spec: FilterSpec) -> None:
        o = self._o
        shown = (
            o.preview
            if o.preview is not None
            else (self._interactive and not o.yes and not spec.is_empty)
        )
        if not shown:
            return
        result = await preview.sample(self._gateway, source.id, spec, pushdown=o.pushdown)
        if result.scanned == 0:
            self._echo(t("backup.preview_empty"))
        else:
            self._echo(t("backup.preview", matched=result.matched, scanned=result.scanned))
            for text in result.examples:
                self._echo(t("clone.preview_example", text=text))

    async def _confirm_start(self) -> None:
        if self._o.yes:
            return
        if not self._interactive:
            return  # nothing hard to reverse here (unlike creating a channel): no --yes required
        assert self._source is not None and self._dir is not None
        question = t("backup.confirm_start", src=channel_label(self._source), dir=self._dir)
        if not await self._prompter.confirm(question):
            raise Declined


async def execute(
    rt: Runtime,
    store: Store,
    gateway: TelegramGateway,
    backup_row: Backup,
    directory: Path,
    manifest: BackupManifest,
    *,
    pushdown: bool = True,
) -> None:
    """Carry out an opened backup in the foreground; print progress and the result."""
    control = RunControl()
    limits = rt.config().limits
    with (
        stop_on_interrupt(control, lambda: typer.echo(t("run.stopping"), err=True)) as interrupt,
        rt.keys(control) as listening,
    ):
        reporter = BackupLineReporter(typer.echo)
        writer = BackupWriter(store, gateway, limits, reporter=reporter, control=control)
        if listening:
            typer.echo(t("run.keys_hint"))
        final = await writer.run(backup_row, directory, manifest, pushdown=pushdown)
    typer.echo(
        t(
            "backup.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            cursor=final.cursor_to,
        )
    )
    if final.skipped_filter:
        typer.echo(t("run.skipped", count=final.skipped_filter))
    if final.status is RunStatus.STOPPED:
        typer.echo(t("backup.continue_hint", dir=directory))
    if interrupt.hit:
        raise typer.Exit(EXIT_INTERRUPTED)
