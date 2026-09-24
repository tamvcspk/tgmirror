"""``tgmirror clone``: choose the source, the destination and the filter, then copy, right now.

Wizard steps 1-4 (source, destination, filter, how to copy), the preview of step 5 and one
confirmation, then the clone runs in the foreground like any other command: no background process,
no schedule, and Ctrl+C ends it with the progress saved. Cloning the same pair again copies only
what is newer (delta) with the filter of the previous run.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Annotated

import typer

from tgmirror.cli import wizard
from tgmirror.cli.commands.run import confirm_fresh, execute
from tgmirror.cli.errors import Declined, UsageProblem, run
from tgmirror.cli.filter_options import (
    AlbumOption,
    ContainsOption,
    ExcludeMediaOption,
    ExcludeRegexOption,
    FilterFileOption,
    HashtagOption,
    MaxSizeOption,
    MediaOption,
    MinSizeOption,
    RegexOption,
    SinceOption,
    UntilOption,
    collect,
)
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.core.gateway import ChannelInfo, TelegramGateway
from tgmirror.engine import preview
from tgmirror.engine.endpoints import (
    Endpoints,
    NewChannelSpec,
    Plan,
    find_channel,
    materialize,
    plan_endpoints,
)
from tgmirror.engine.runs import (
    SUPPORTED_MODES,
    ModeUnsupported,
    RunRequest,
    begin_run,
    check_options,
    check_runnable,
)
from tgmirror.engine.strategy import may_reupload
from tgmirror.filters.model import FilterSpec
from tgmirror.store.db import Store, utc_now
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Prompter, run_steps
from tgmirror.ui.tables import channel_label


def clone(
    ctx: typer.Context,
    src: Annotated[
        str | None,
        typer.Option(
            "--src", help='Source: "@username" (quoted in PowerShell), id (-100...) or exact title.'
        ),
    ] = None,
    dst: Annotated[
        str | None, typer.Option("--dst", help="Existing destination (same kind as the source).")
    ] = None,
    dst_new: Annotated[
        str | None,
        typer.Option("--dst-new", help="Create a new destination channel with this title."),
    ] = None,
    about: Annotated[str, typer.Option("--about", help="Description of the new channel.")] = "",
    mode: Annotated[
        str | None,
        typer.Option(
            "--mode",
            help="auto (default): server-side copy, and re-upload only what needs a new caption. "
            "copy: server-side copy only. reupload: download and send again (slow); the only "
            "way to copy a source that restricts saving content and that you administer.",
        ),
    ] = None,
    caption: Annotated[
        str | None,
        typer.Option(
            "--caption",
            help="keep (default), strip-links (drop links/mentions that point at the source), "
            "append (add --caption-text) or none. Only the captions of media messages change; "
            "each message with a caption is downloaded and sent again.",
        ),
    ] = None,
    caption_text: Annotated[
        str | None, typer.Option("--caption-text", help="Text for --caption append.")
    ] = None,
    reset_polls: Annotated[
        bool,
        typer.Option(
            "--reset-polls",
            help="With --mode reupload: re-create polls and quizzes (they lose all votes). "
            "Without it they are left out, with a warning.",
        ),
    ] = False,
    ignore_unsupported: Annotated[
        bool,
        typer.Option(
            "--ignore-unsupported",
            help="With --mode reupload: leave out what cannot be copied (games, invoices, "
            "unanswered quizzes) instead of stopping at the first one.",
        ),
    ] = False,
    placeholder: Annotated[
        bool,
        typer.Option(
            "--placeholder",
            help="With --mode reupload: like --ignore-unsupported, and post a short note "
            "where each such message was.",
        ),
    ] = False,
    admin_ack: Annotated[
        bool,
        typer.Option(
            "--yes-i-administer-this-channel",
            help="With --mode reupload on a source that restricts saving content: say that you "
            "own it (also through another account) and may copy it. You take full "
            "responsibility for that: tgmirror cannot check it. --yes does not stand in for it.",
        ),
    ] = False,
    batch_size: Annotated[
        int | None,
        typer.Option(
            "--batch-size",
            min=1,
            max=100,
            help="Messages per copy call (default: batch_size from config.toml, 20).",
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
    no_filter: Annotated[
        bool,
        typer.Option(
            "--no-filter",
            help="Drop the filter remembered from an earlier clone of this pair and copy "
            "everything (the source is read again from the start; nothing is copied twice).",
        ),
    ] = False,
    fresh: Annotated[
        bool,
        typer.Option(
            "--fresh",
            help="Start this pair over: forget what it has copied and copy everything from "
            "the start again (the destination may get duplicates unless you emptied it). "
            "Keeps the remembered filter unless you give another or --no-filter. Asks first "
            "when there is something to forget; --yes agrees.",
        ),
    ] = False,
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
    wait: Annotated[
        bool,
        typer.Option(
            "--wait",
            help="Sit out a FloodWait of any length instead of saving progress and exiting "
            "(waits longer than [limits] max_auto_wait normally end the run). "
            "Does not apply to the daily cap.",
        ),
    ] = False,
    force_takeover: Annotated[
        bool,
        typer.Option(
            "--force-takeover",
            help="Run even if another process seems to hold this clone (only if it is dead).",
        ),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Clone a source into a destination (existing, or newly created) and start copying now.

    Without --src/--dst/--dst-new it asks; with them it never prompts (only a new channel needs
    --yes when there is no terminal). Ctrl+C stops it, saving progress; `tgmirror run` continues.
    Cloning the same pair again copies only what is newer, with the same filter unless you give
    another one (then the source is read again from the start; nothing is copied twice).

    To redo a pair from scratch (same destination): --fresh.

    Keys while it runs: p pause, r resume, q stop.

    Example: tgmirror clone --src "@my_channel" --dst-new "My channel (copy)" --yes

    A source that restricts saving content can only be copied with --mode reupload and your own
    statement, --yes-i-administer-this-channel, for which you take full responsibility (an
    admin account can answer the question instead; any other account needs the flag).
    To change captions: --caption strip-links (or append/none).

    With a filter: tgmirror clone --src "@my_channel" --dst-new "Videos" --media video
    --hashtag "#news" --since 2024-01-01 --yes

    In PowerShell keep the quotes around @names: an unquoted @name is dropped by the shell.
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if dst is not None and dst_new is not None:
            raise UsageProblem("err.conflicting_flags", flags="--dst / --dst-new")
        if mode is not None and mode not in SUPPORTED_MODES:  # before anything is created
            raise ModeUnsupported(mode)
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
        )  # a bad filter is a usage error before anything is asked or written
        if no_filter:
            if filters is not None:
                raise UsageProblem("err.conflicting_flags", flags="--no-filter / filter flags")
            filters = FilterSpec()
        options = CloneOptions(
            src=src,
            dst=dst,
            dst_new=dst_new,
            about=about,
            mode=mode,
            caption=caption,
            caption_text=caption_text,
            reset_polls=reset_polls,
            ignore_unsupported=ignore_unsupported,
            placeholder=placeholder,
            admin_ack=admin_ack,
            batch_size=batch_size,
            filters=filters,
            fresh=fresh,
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
                flow = CloneFlow(rt, store, conn.gateway, channels, options)
                await run_steps(rt.prompter, flow.steps())
                ready = flow.ready()

                endpoints = await materialize(conn.gateway, ready.plan)
                for line in endpoint_lines(endpoints):
                    typer.echo(line)
                started = await begin_run(
                    store, conn.gateway, endpoints.src, endpoints.dst, ready.request
                )
                await execute(rt, store, conn.gateway, started, wait=wait)

    run(rt, command())


def endpoint_lines(endpoints: Endpoints) -> list[str]:
    """What is shown once the endpoints exist: the source, and the destination (just created?)."""
    key = "clone.dst_created" if endpoints.created else "clone.dst"
    return [
        t("clone.src", channel=channel_label(endpoints.src)),
        t(key, channel=channel_label(endpoints.dst)),
    ]


@dataclass(frozen=True, slots=True)
class CloneOptions:
    """The flags of ``tgmirror clone`` (filters already parsed). The defaults are what the
    full-screen menu passes: nothing given, so the wizard asks everything."""

    src: str | None = None
    dst: str | None = None
    dst_new: str | None = None
    about: str = ""
    mode: str | None = None
    caption: str | None = None
    caption_text: str | None = None
    reset_polls: bool = False
    ignore_unsupported: bool = False
    placeholder: bool = False
    admin_ack: bool = False
    batch_size: int | None = None
    filters: FilterSpec | None = None
    fresh: bool = False
    pushdown: bool = True
    preview: bool | None = None
    yes: bool = False
    force_takeover: bool = False


@dataclass(frozen=True, slots=True)
class CloneReady:
    """What the flow settled on: the endpoints to materialize and the request to start."""

    plan: Plan
    request: RunRequest


class CloneFlow:
    """Everything ``tgmirror clone`` does before anything is written: wizard steps 1-5 and the
    one confirmation, as a list of steps (``steps()``) over shared state.

    The CLI runs the steps one after the other. The full-screen menu runs them through
    ``ui.prompts.run_steps`` with its own prompter, where Esc goes back one question, across
    steps too: a step is re-run from its start, so each one only assigns its own results (and a
    step that reads Telegram or the store does so again when it is re-run). Output goes through
    ``echo`` (``typer.echo``'s signature); a "no" to a confirmation raises ``Declined``.
    """

    def __init__(
        self,
        rt: Runtime,
        store: Store,
        gateway: TelegramGateway,
        channels: Sequence[ChannelInfo],
        options: CloneOptions,
        *,
        prompter: Prompter | None = None,
        interactive: bool | None = None,
        echo: Callable[..., None] = typer.echo,
    ) -> None:
        self._rt = rt
        self._store = store
        self._gateway = gateway
        self._channels = channels
        self._o = o = options
        self._prompter = prompter or rt.prompter
        self._interactive = rt.interactive if interactive is None else interactive
        self._echo = echo
        # the wizard asks the rest only if it also asked for source and destination
        self._asked = (
            self._interactive and not o.yes and (o.src is None or (o.dst is None and not o.dst_new))
        )
        self._source: ChannelInfo | None = None
        self._plan: Plan | None = None
        self._base: RunRequest | None = None
        self._seen_before = False
        self._copied = 0
        self._fresh = o.fresh
        self._filters = o.filters

    def steps(self) -> list[Callable[[], Awaitable[None]]]:
        return [
            self._pick_source,
            self._pick_destination,
            self._pick_strategy,
            self._read_history,
            self._pick_resume,
            self._pick_filters,
            self._confirm,
        ]

    def ready(self) -> CloneReady:
        assert self._plan is not None and self._base is not None, "run every step first"
        request = replace(
            self._base,
            filters_json=None if self._filters is None else self._filters.to_json(),
            fresh=self._fresh,
        )
        return CloneReady(self._plan, request)

    async def _pick_source(self) -> None:
        if self._o.src is not None:
            self._source = find_channel(self._channels, self._o.src)
        elif self._interactive:
            self._source = await wizard.pick_source(self._prompter, self._channels)
        else:
            raise UsageProblem("err.missing_flag", flag="--src")

    async def _pick_destination(self) -> None:
        o, source = self._o, self._source
        assert source is not None
        destination: ChannelInfo | NewChannelSpec
        if o.dst is not None:
            destination = find_channel(self._channels, o.dst)
        elif o.dst_new is not None:
            destination = NewChannelSpec(o.dst_new, o.about)
        elif self._interactive:
            destination = await wizard.pick_destination(self._prompter, source, self._channels)
        else:
            raise UsageProblem("err.missing_flag", flag="--dst or --dst-new")
        # every refusal happens before any write
        self._plan = plan_endpoints(source, destination, take_responsibility=o.admin_ack)

    async def _pick_strategy(self) -> None:
        o, plan = self._o, self._plan
        assert plan is not None
        given = (
            o.mode is not None
            or o.caption is not None
            or o.caption_text is not None
            or o.reset_polls
            or o.ignore_unsupported
            or o.placeholder
        )
        choice = wizard.StrategyChoice(
            o.mode or "auto",
            o.caption or "keep",
            o.caption_text or "",
            o.reset_polls,
            o.ignore_unsupported,
            o.placeholder,
        )
        if self._asked and not given:
            choice = await wizard.pick_strategy(self._prompter, protected=plan.protected)

        base = RunRequest(
            mode=choice.mode,
            batch_size=o.batch_size or self._rt.config().limits.batch_size,
            pushdown=o.pushdown,
            force=o.force_takeover,
            caption=choice.caption,
            caption_text=choice.caption_text,
            reset_polls=choice.reset_polls,
            ignore_unsupported=choice.ignore_unsupported,
            placeholder=choice.placeholder,
        )
        check_options(base)  # options that contradict each other: exit 2 before any write

        downloads = may_reupload(choice.mode, choice.caption)
        for code in plan.warnings:
            if not (code == "noforwards_admin" and downloads):  # the confirmation says it
                self._echo(t(f"warn.{code}"), err=True)
        if plan.protected and downloads:
            await self._confirm_protected(plan)
            base = replace(base, protected_ack=True)  # a refusal above never gets here
        self._base = base

    async def _confirm_protected(self, plan: Plan) -> None:
        """Decision D3: a source that restricts saving content is copied by download and upload
        only if the user says they may. The flag or the question; ``--yes`` is not enough, because
        this is not about skipping a prompt: it is the user's own statement, and a script must
        make it with the flag that names it."""
        if self._o.admin_ack:
            return
        if not self._interactive:
            raise UsageProblem("err.needs_admin_ack", title=plan.src.title)
        question = t("clone.confirm_protected", title=plan.src.title)
        if not await self._prompter.confirm(question, False):
            raise Declined

    async def _read_history(self) -> None:
        plan = self._plan
        assert plan is not None
        self._seen_before, self._copied = False, 0
        if isinstance(plan.dst, ChannelInfo):  # fail before previewing or asking anything
            src_id, dst_id = plan.src.id, plan.dst.id
            if (last := await self._store.latest_run(src_id, dst_id)) is not None:
                check_runnable(last, utc_now())
            self._seen_before = await self._store.find_mirror(src_id, dst_id) is not None
            self._copied = await self._store.count_copied(src_id, dst_id)

    async def _pick_resume(self) -> None:
        self._fresh = self._o.fresh
        if self._asked and not self._o.fresh and self._copied > 0:
            self._fresh = await wizard.pick_resume(self._prompter, self._copied)

    async def _pick_filters(self) -> None:
        self._filters = self._o.filters
        if self._filters is None and self._asked:
            self._filters = await wizard.pick_filters(self._prompter, can_keep=self._seen_before)

    async def _confirm(self) -> None:
        """Wizard step 5 (the preview, when shown) and the one question before copying."""
        assert self._source is not None
        if self._filters is not None:
            await self._preview(self._source, self._filters)
        await self._confirm_start(forget=self._copied if self._fresh else 0)

    async def _preview(self, source: ChannelInfo, spec: FilterSpec) -> None:
        """Wizard step 5: a sample of what the filter selects.

        Shown when asked for with ``--preview``, or by default on a terminal with a filter and no
        ``--yes``. It runs before anything is created, so declining the confirmation that follows
        leaves nothing behind.
        """
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
            self._echo(t("clone.preview_empty"))
        else:
            self._echo(t("clone.preview", matched=result.matched, scanned=result.scanned))
            for text in result.examples:
                self._echo(t("clone.preview_example", text=text))

    async def _confirm_start(self, forget: int = 0) -> None:
        """The one question before copying starts. ``--yes`` skips it; with no terminal it is not
        asked, except that creating a channel (a write on the account) then needs ``--yes``.

        A fresh start that forgets ``forget`` copied messages puts that in the same question (and
        needs ``--yes`` without a terminal too).
        """
        plan, yes = self._plan, self._o.yes
        assert plan is not None
        if forget:
            await confirm_fresh(
                self._rt,
                forget,
                yes,
                channel_label(plan.src),
                channel_label(plan.dst),
                prompter=self._prompter,
                interactive=self._interactive,
            )
            return
        if yes:
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
        question = t("clone.confirm_start", src=channel_label(plan.src), dst=target)
        if not await self._prompter.confirm(question):
            raise Declined
