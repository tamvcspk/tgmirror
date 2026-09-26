"""``tgmirror restore``: rebuild a channel from a backup directory (phase 11b).

Wizard steps 1-4 (directory, destination, decision D3, filter), a preview and one confirmation,
then it runs in the foreground like ``clone``/``run`` — the same ``Runner``, forced onto strategy B
(there is no server-side copy from a directory), reading through a ``BackupReader`` instead of the
gateway. Running it again on the same pair (or `tgmirror run`) continues the delta, exactly like any
other clone.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli import wizard
from tgmirror.cli.commands.run import execute
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
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.core.gateway import ChannelInfo, TelegramGateway, TopicInfo
from tgmirror.engine import preview
from tgmirror.engine.backup_reader import BackupReader
from tgmirror.engine.backupdir import BackupManifest, read_manifest
from tgmirror.engine.endpoints import (
    NewChannelSpec,
    Plan,
    eligible_destinations,
    find_channel,
    materialize,
    plan_endpoints,
)
from tgmirror.engine.runs import RunRequest, begin_run, check_options, check_runnable
from tgmirror.filters.model import FilterSpec
from tgmirror.store.db import Store, utc_now
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Choice, Prompter, run_steps
from tgmirror.ui.tables import channel_label

ADMIN_ACK_FLAG = "--yes-i-administer-this-channel"


def restore(
    ctx: typer.Context,
    directory: Annotated[
        Path | None, typer.Argument(help="Backup directory made by `tgmirror backup`.")
    ] = None,
    dst: Annotated[
        str | None,
        typer.Option(
            "--dst",
            help="Existing destination: a channel or group you administer and can post to.",
        ),
    ] = None,
    dst_new: Annotated[
        bool,
        typer.Option(
            "--dst-new",
            help="Create a new destination channel, named after the backup's source "
            "(--about overrides the description, which a backup never records).",
        ),
    ] = False,
    about: Annotated[str, typer.Option("--about", help="Description of the new channel.")] = "",
    admin_ack: Annotated[
        bool,
        typer.Option(
            "--yes-i-administer-this-channel",
            help="The backed-up source restricted saving content: say that you own it (also "
            "through another account) and may restore it. You take full responsibility for "
            "that: tgmirror cannot check it. Asked again even though the backup already "
            "recorded an answer, for consistency.",
        ),
    ] = False,
    caption: Annotated[
        str | None,
        typer.Option(
            "--caption",
            help="keep (default), strip-links (drop links/mentions that point at the source), "
            "append (add --caption-text) or none.",
        ),
    ] = None,
    caption_text: Annotated[
        str | None, typer.Option("--caption-text", help="Text for --caption append.")
    ] = None,
    ignore_unsupported: Annotated[
        bool,
        typer.Option(
            "--ignore-unsupported",
            help="Leave out a game/invoice/unanswered-quiz message instead of stopping the run.",
        ),
    ] = False,
    placeholder: Annotated[
        bool,
        typer.Option(
            "--placeholder",
            help="Post a text placeholder for a game/invoice/unanswered-quiz message instead of "
            "stopping the run (or leaving it out with --ignore-unsupported).",
        ),
    ] = False,
    topic_as_hashtag: Annotated[
        bool | None,
        typer.Option(
            "--topic-as-hashtag/--no-topic-as-hashtag",
            help="An existing, non-forum destination cannot hold the backup's topics: keep each "
            "one's name as a hashtag instead of dropping it silently (default: ask, or yes with "
            "no terminal).",
        ),
    ] = None,
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
            help="Parsed for parity with clone/backup; makes no difference here, since the whole "
            "backup is already local (nothing to narrow before it is read).",
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
            help="Run even if another process seems to hold this restore (only if it is dead).",
        ),
    ] = False,
    wait: Annotated[
        bool,
        typer.Option(
            "--wait", help="Sit out a FloodWait of any length (see `tgmirror run --help`)."
        ),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Rebuild a channel from a directory `tgmirror backup` made, into a destination (existing, or
    newly created and named after the backup's source).

    Without --dir/--dst/--dst-new it asks (wizard); with them it never prompts (except the
    decision-D3 question below, and a new channel needing --yes with no terminal). Ctrl+C stops
    it, saving progress; `tgmirror run` continues it, same as any other clone.

    Always downloads and re-uploads (there is no server-side copy from a directory); polls are
    always re-created fresh (a backup never keeps votes).

    A backed-up source that restricted saving content needs your own statement,
    --yes-i-administer-this-channel, asked again even though the backup already recorded one, for
    consistency (an admin account cannot be re-verified here — the original channel may be gone).

    Example: tgmirror restore ./backups/my_channel --dst-new --yes
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if dst is not None and dst_new:
            raise UsageProblem("err.conflicting_flags", flags="--dst / --dst-new")
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
        options = RestoreOptions(
            dir=directory,
            dst=dst,
            dst_new=dst_new,
            about=about,
            admin_ack=admin_ack,
            caption=caption,
            caption_text=caption_text,
            ignore_unsupported=ignore_unsupported,
            placeholder=placeholder,
            topic_as_hashtag=topic_as_hashtag,
            filters=filters,
            pushdown=pushdown,
            preview=preview_flag,
            yes=yes,
            force_takeover=force_takeover,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                flow = RestoreFlow(rt, store, conn.gateway, channels, options)
                await run_steps(rt.prompter, flow.steps())
                ready = flow.ready()

                endpoints = await materialize(conn.gateway, ready.plan)
                key = "restore.dst_created" if endpoints.created else "restore.dst"
                typer.echo(t(key, channel=channel_label(endpoints.dst)))
                started = await begin_run(
                    store, conn.gateway, endpoints.src, endpoints.dst, ready.request
                )
                await execute(
                    rt, store, conn.gateway, started, wait=wait, reader_override=ready.reader
                )

    run(rt, command())


@dataclass(frozen=True, slots=True)
class RestoreOptions:
    """The flags of ``tgmirror restore`` (filters already parsed). Defaults are what an empty
    wizard invocation passes: nothing given, so every step asks."""

    dir: Path | None = None
    dst: str | None = None
    dst_new: bool = False
    about: str = ""
    admin_ack: bool = False
    caption: str | None = None
    caption_text: str | None = None
    ignore_unsupported: bool = False
    placeholder: bool = False
    topic_as_hashtag: bool | None = None
    filters: FilterSpec | None = None
    pushdown: bool = True
    preview: bool | None = None
    yes: bool = False
    force_takeover: bool = False


@dataclass(frozen=True, slots=True)
class RestoreReady:
    """What the flow settled on: the endpoints to materialize, the request to start, and the
    reader ``execute`` must hand ``Runner`` instead of a live gateway read."""

    plan: Plan
    request: RunRequest
    reader: BackupReader


class RestoreFlow:
    """Everything ``tgmirror restore`` does before anything is written: wizard steps 1-4 and the
    one confirmation, as a list of steps (``steps()``) over shared state — mirrors
    ``cli/commands/backup.py::BackupFlow``/``cli/commands/clone.py::CloneFlow``, minus what
    restore has no equivalent of (a mode/strategy choice: always reupload; a "keep the filter"
    choice: a restore's filter narrows what is read out of the directory, chosen fresh every time).
    """

    def __init__(
        self,
        rt: Runtime,
        store: Store,
        gateway: TelegramGateway,
        channels: Sequence[ChannelInfo],
        options: RestoreOptions,
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
        # the wizard asks the rest only if it also asked for the directory and the destination
        no_dst = o.dst is None and not o.dst_new
        self._asked = self._interactive and not o.yes and (o.dir is None or no_dst)
        self._dir: Path | None = None
        self._manifest: BackupManifest | None = None
        self._reader: BackupReader | None = None
        self._source: ChannelInfo | None = None
        self._plan: Plan | None = None
        self._topic_as_hashtag = False
        self._filters = o.filters

    def steps(self) -> list[Callable[[], Awaitable[None]]]:
        return [
            self._pick_dir,
            self._pick_destination,
            self._confirm_d3,
            self._pick_filters,
            self._confirm,
        ]

    def ready(self) -> RestoreReady:
        assert self._plan is not None and self._reader is not None and self._manifest is not None
        request = RunRequest(
            mode="reupload",
            from_backup=str(self._dir),
            filters_json=None if self._filters is None else self._filters.to_json(),
            caption=self._o.caption or "keep",
            caption_text=self._o.caption_text or "",
            reset_polls=True,  # a backup never keeps votes: restoring a poll is always fresh
            ignore_unsupported=self._o.ignore_unsupported,
            placeholder=self._o.placeholder,
            protected_ack=self._manifest.src_noforwards,
            topic_as_hashtag=self._topic_as_hashtag,
        )
        check_options(request)
        return RestoreReady(self._plan, request, self._reader)

    async def _pick_dir(self) -> None:
        if self._o.dir is not None:
            self._dir = self._o.dir.resolve()
        elif self._interactive:
            self._dir = await wizard.ask_restore_dir(self._prompter)
        else:
            raise UsageProblem("err.missing_flag", flag="DIR")
        manifest = read_manifest(self._dir)
        if manifest is None:
            raise UsageProblem("err.not_a_backup", dir=self._dir)
        # a backup still writing (or waiting out a flood) into this directory must settle first
        if (last := await self._store.latest_backup(str(self._dir))) is not None:
            check_runnable(last, utc_now())
        self._manifest = manifest
        self._reader = BackupReader(self._dir, manifest)

    async def _pick_destination(self) -> None:
        manifest = self._manifest
        assert manifest is not None
        source = ChannelInfo(
            manifest.src_id,
            manifest.src_title,
            manifest.src_kind,
            noforwards=manifest.src_noforwards,
            is_admin=True,
        )
        self._source = source
        o = self._o
        destination: ChannelInfo | NewChannelSpec
        if o.dst is not None:
            destination = find_channel(self._channels, o.dst)
        elif o.dst_new:
            destination = NewChannelSpec(manifest.src_title, o.about)
        elif self._interactive:
            destination = await self._ask_destination(source)
        else:
            raise UsageProblem("err.missing_flag", flag="--dst or --dst-new")
        self._plan = plan_endpoints(source, destination, take_responsibility=True)
        if (
            isinstance(self._plan.dst, ChannelInfo)
            and (last := await self._store.latest_run(source.id, self._plan.dst.id)) is not None
        ):
            check_runnable(last, utc_now())
        loses_topics = "topic_loss" in self._plan.warnings
        topic_hashtag = o.topic_as_hashtag
        if topic_hashtag is None:
            topic_hashtag = loses_topics
            if loses_topics and self._asked:
                topic_hashtag = await wizard.pick_topic_as_hashtag(self._prompter)
        self._topic_as_hashtag = bool(topic_hashtag) and loses_topics
        for code in self._plan.warnings:
            if code == "topic_loss" and self._topic_as_hashtag:
                continue
            if code in ("noforwards_admin", "noforwards_unadministered"):
                continue  # restore's own D3 step (below) covers this
            self._echo(t(f"warn.{code}"))

    async def _ask_destination(self, source: ChannelInfo) -> ChannelInfo | NewChannelSpec:
        """Existing candidates alongside "create new" — typing a name for it (``wizard.
        ask_new_channel``, the same title-then-about prompt ``clone`` uses), not auto-picking
        the backup's source title (only the flags path, ``--dst-new``, still does that: there is
        no name to type there)."""
        candidates = eligible_destinations(source, self._channels)
        choices: list[Choice[ChannelInfo | None]] = [Choice(t("clone.create_new"), None)]
        choices += [Choice(channel_label(c), c) for c in candidates]
        picked = await self._prompter.select(t("restore.pick_destination"), choices)
        return picked if picked is not None else await wizard.ask_new_channel(self._prompter)

    async def _confirm_d3(self) -> None:
        """Decision D3, re-asked (docs/06-lo-trinh.md, "Đã chốt" item 1): a backed-up source that
        restricted saving content needs the statement again, even though ``backup.json`` already
        recorded one, for consistency ("không bao giờ âm thầm"). There is no live channel left to
        test admin status against, so — unlike ``clone``'s D3 step — there is only the flag/typed
        path, never a plain yes/no."""
        manifest = self._manifest
        assert manifest is not None
        if not manifest.src_noforwards or self._o.admin_ack:
            return
        if self._interactive:
            question = t(
                "clone.confirm_unadministered", title=manifest.src_title, flag=ADMIN_ACK_FLAG
            )
            typed = await self._prompter.text(question)
            if typed.strip() == ADMIN_ACK_FLAG:
                return
            raise Declined
        raise UsageProblem("err.needs_admin_ack", title=manifest.src_title)

    async def _pick_filters(self) -> None:
        if self._filters is not None:
            return
        if self._asked:
            manifest = self._manifest
            assert manifest is not None
            topics: Sequence[TopicInfo] = [TopicInfo(t.id, t.title) for t in manifest.topics]
            self._filters = await wizard.pick_filters(self._prompter, can_keep=False, topics=topics)

    async def _confirm(self) -> None:
        """Wizard step 5 (the preview, when shown) and the one question before it starts."""
        manifest, reader = self._manifest, self._reader
        assert manifest is not None and reader is not None
        if self._filters is not None:
            await self._preview(manifest.src_id, self._filters, reader)
        await self._confirm_start()

    async def _preview(self, src_id: int, spec: FilterSpec, reader: BackupReader) -> None:
        o = self._o
        shown = (
            o.preview
            if o.preview is not None
            else (self._interactive and not o.yes and not spec.is_empty)
        )
        if not shown:
            return
        result = await preview.sample(reader, src_id, spec, pushdown=o.pushdown)
        if result.scanned == 0:
            self._echo(t("restore.preview_empty"))
        else:
            self._echo(t("restore.preview", matched=result.matched, scanned=result.scanned))
            for text in result.examples:
                self._echo(t("clone.preview_example", text=text))

    async def _confirm_start(self) -> None:
        plan = self._plan
        assert plan is not None
        if self._o.yes:
            return
        if not self._interactive:
            if isinstance(plan.dst, NewChannelSpec):
                raise UsageProblem("err.needs_yes", flag="--yes")
            return
        target = (
            t("clone.dst_will_be_created", title=plan.dst.title)
            if isinstance(plan.dst, NewChannelSpec)
            else channel_label(plan.dst)
        )
        question = t("restore.confirm_start", src=channel_label(plan.src), dst=target)
        if not await self._prompter.confirm(question):
            raise Declined
