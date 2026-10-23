"""``tgmirror delete``: remove messages from a chat you administer, in place (docs/07-inspect-
transform.md section 6, docs/06-lo-trinh.md Phase 20, decision D10).

The only command that destroys something on Telegram, so it never does it at once:

1. **Plan** — read the chat with the filter and freeze the ids (``engine.ops.plan_delete``); print
   what the plan holds: how many, by kind, by month, a sample of each end.
2. **Confirm** — at a terminal, type the number of messages back (not a Yes/No); without one,
   ``--yes`` together with ``--expect N``, N being the number in the plan, so a filter that matches
   more than the script's author thought deletes nothing. With neither, the plan stays saved as
   ``O<n>`` and nothing is deleted.
3. **Apply** — in the foreground like ``clone``: each batch is read again and judged against the
   filter once more, written ahead as ``pending``, deleted, marked ``done``. ``p``/``r``/``q`` and
   Ctrl+C work; ``--resume O<n>`` carries on (or applies a plan that was only saved).

An empty filter means *every* message, so it needs ``--all`` (or, at a terminal, a question).
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli import wizard
from tgmirror.cli.commands.backup import execute as run_backup
from tgmirror.cli.errors import EXIT_INTERRUPTED, Declined, UsageProblem, run
from tgmirror.cli.filter_options import (
    AlbumOption,
    ContainsOption,
    ExcludeMediaOption,
    ExcludeRegexOption,
    FilterFileOption,
    ForwardedFromOption,
    ForwardedOption,
    FromUserOption,
    HashtagOption,
    HasLinkOption,
    LinkDomainOption,
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
from tgmirror.cli.refs import SOURCE_HELP
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.cli.selection_options import (
    ExactOption,
    SelectionOption,
    check_args,
    for_channel,
    narrowed,
)
from tgmirror.core.gateway import ChannelInfo, ChatKind, TelegramGateway, TopicInfo
from tgmirror.engine import selection
from tgmirror.engine.endpoints import find_channel
from tgmirror.engine.ops import (
    OpRunner,
    begin_op,
    begin_op_backup,
    plan_delete,
    resolve_op,
    settle_op_backup,
)
from tgmirror.engine.runner import RunControl
from tgmirror.filters.model import FilterSpec
from tgmirror.store.db import Store
from tgmirror.store.ops import Op, OpStatus
from tgmirror.ui.lines import (
    backup_start_line,
    delete_backup_done_line,
    delete_backup_stopped_line,
    delete_plan_lines,
    delete_result_lines,
    delete_start_line,
)
from tgmirror.ui.messages import t
from tgmirror.ui.progress import OpLineReporter
from tgmirror.ui.prompts import Choice, Prompter
from tgmirror.ui.tables import channel_label


def delete(
    ctx: typer.Context,
    src: Annotated[
        str | None,
        typer.Argument(help=SOURCE_HELP),
    ] = None,
    all_messages: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Allow an empty filter, which selects EVERY message of the chat.",
        ),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume",
            metavar="O<N>",
            help="Carry on with an earlier delete (its number is in `tgmirror history`), or apply "
            "a plan that was only saved. Not with a source or a filter.",
        ),
    ] = None,
    expect: Annotated[
        int | None,
        typer.Option(
            "--expect",
            min=0,
            help="With --yes: the number of messages the plan must hold. A different number "
            "deletes nothing.",
        ),
    ] = None,
    backup_to: Annotated[
        Path | None,
        typer.Option(
            "--backup-to",
            help="Back the planned messages up into this directory before deleting anything "
            "(a plan that is not backed up completely is not applied).",
        ),
    ] = None,
    no_backup: Annotated[
        bool,
        typer.Option(
            "--no-backup", help="Delete without a backup, and do not ask whether to make one."
        ),
    ] = False,
    admin_ack: Annotated[
        bool,
        typer.Option(
            "--yes-i-administer-this-channel",
            help="For the backup of a chat that restricts saving content: say that you own it "
            "and may copy it. You take full responsibility for that: tgmirror cannot check it.",
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
    forwarded: ForwardedOption = None,
    forwarded_from: ForwardedFromOption = None,
    has_link: HasLinkOption = False,
    link_domain: LinkDomainOption = None,
    selection_file: SelectionOption = None,
    exact: ExactOption = False,
    pushdown: Annotated[
        bool,
        typer.Option(
            "--pushdown/--no-pushdown",
            help="Let Telegram narrow the reading by filter (default). --no-pushdown reads "
            "everything and filters here: slower, for checking that nothing is missed.",
        ),
    ] = True,
    force_takeover: Annotated[
        bool,
        typer.Option(
            "--force-takeover",
            help="Carry on even if another process seems to hold this chat (only if dead).",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm without typing the count; needs --expect N."),
    ] = False,
) -> None:
    """Delete the messages a filter selects from a chat you administer. This cannot be undone.

    It plans first and shows what it would delete; then you type the number of messages back to
    confirm. Without a terminal, give --yes --expect N (N from the plan); without them it only
    saves the plan, which `--resume O<n> --yes --expect N` applies later. Deleting is not
    reversible on Telegram: back the chat up first (`tgmirror backup`) if you may want it back.

    Each message is read again just before it is deleted: one that changed so that it no longer
    matches the filter is left alone. Albums are deleted whole.

    At a terminal it asks whether to back the messages up first; without one, `--backup-to DIR`
    does (or `--no-backup` says no). Nothing is deleted unless the backup finished.

    Keys while it runs: p pause, r resume, q stop.

    Example: tgmirror delete "@my_channel" --hashtag "#ads" --since 2024-01-01
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
            forwarded=forwarded,
            forwarded_from=forwarded_from,
            has_link=has_link,
            link_domain=link_domain,
        )  # a bad filter is a usage error before anything is asked or read
        check_args(selection_file, exact, filter_file)
        if resume is not None and (
            src is not None or filters is not None or selection_file is not None or all_messages
        ):
            raise UsageProblem("err.conflicting_flags", flags="--resume / SRC / filter / --all")
        if backup_to is not None and no_backup:
            raise UsageProblem("err.conflicting_flags", flags="--backup-to / --no-backup")
        options = DeleteOptions(
            src=src,
            all_messages=all_messages,
            resume=resume,
            expect=expect,
            backup_to=backup_to,
            no_backup=no_backup,
            admin_ack=admin_ack,
            filters=filters,
            pushdown=pushdown,
            force_takeover=force_takeover,
            yes=yes,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                flow = DeleteFlow(rt, store, conn.gateway, channels, options)
                if resume is not None:
                    op = await flow.resumed()
                else:
                    if selection_file is not None:
                        chosen, named, _ = await asyncio.to_thread(
                            for_channel,
                            rt.paths,
                            channels,
                            src=src,
                            selection=selection_file,
                            exact=exact,
                        )
                        options = replace(options, filters=narrowed(chosen, filters), src=named)
                        flow = DeleteFlow(rt, store, conn.gateway, channels, options)
                    planned = await flow.planned()
                    if planned is None:
                        return
                    op = planned
                if not await flow.confirmed(op):
                    return
                if (backup := await flow.backup_plan(op)) is not None:
                    await back_up(
                        rt, store, conn.gateway, op, backup, force_takeover=force_takeover
                    )
                await execute(rt, store, conn.gateway, op, force_takeover=force_takeover)

    run(rt, command())


@dataclass(frozen=True, slots=True)
class DeleteOptions:
    """The flags of ``tgmirror delete`` (filters already parsed)."""

    src: str | None = None
    all_messages: bool = False
    resume: str | None = None
    expect: int | None = None
    backup_to: Path | None = None
    no_backup: bool = False
    admin_ack: bool = False
    filters: FilterSpec | None = None
    pushdown: bool = True
    force_takeover: bool = False
    yes: bool = False


@dataclass(frozen=True, slots=True)
class BackupPlan:
    """Where the plan is backed up before it is applied, and whether the person said (D3) that the
    chat's restriction on saving content is theirs to lift."""

    directory: Path
    ack: bool


class DeleteFlow:
    """What ``tgmirror delete`` does before anything is deleted: pick the chat and the filter,
    plan, and obtain the confirmation. Flags and the wizard answer the same questions (rule 7)."""

    def __init__(
        self,
        rt: Runtime,
        store: Store,
        gateway: TelegramGateway,
        channels: Sequence[ChannelInfo],
        options: DeleteOptions,
        *,
        prompter: Prompter | None = None,
        interactive: bool | None = None,
        echo: Callable[..., None] = typer.echo,
    ) -> None:
        self._echo = echo
        self._rt = rt
        self._store = store
        self._gateway = gateway
        self._channels = channels
        self._o = options
        self._prompter = prompter or rt.prompter
        self._interactive = rt.interactive if interactive is None else interactive
        self._selections_dir = rt.paths.selections_dir

    # ---- 1. the plan -----------------------------------------------------------------------

    async def resumed(self) -> Op:
        """The operation ``--resume`` names. A saved plan needs the confirmation; one that was
        started already has it."""
        assert self._o.resume is not None
        op = await resolve_op(self._store, self._o.resume)
        if op.status in (OpStatus.DONE, OpStatus.CANCELLED):
            raise UsageProblem("delete.finished", id=op.id, status=t(f"status.{op.status}"))
        # the chat must still be reachable before anything is asked
        find_channel(self._channels, str(op.chat_id))
        return op

    async def planned(self) -> Op | None:
        """Pick the chat and the filter, plan, show the plan. ``None``: nothing to delete."""
        chat = await self._pick_chat()
        spec = await self._pick_filter(chat)
        plan = await plan_delete(
            self._store,
            self._gateway,
            chat,
            spec,
            self._rt.config().limits,
            pushdown=self._o.pushdown,
        )
        if plan.op is None:
            self._echo(t("delete.nothing", chat=chat.title))
            return None
        for line in delete_plan_lines(plan.op, plan.summary):
            self._echo(line)
        return plan.op

    async def _pick_chat(self) -> ChannelInfo:
        if self._o.src is not None:
            return find_channel(self._channels, self._o.src)
        if not self._interactive:
            raise UsageProblem("err.missing_flag", flag="SRC")
        administered = [c for c in self._channels if c.is_admin]
        if not administered:
            typer.echo(t("delete.no_admin_channels"))
            raise typer.Exit(1)
        choices = [Choice(channel_label(c), c) for c in administered]
        return await self._prompter.select(t("delete.pick_source"), choices)

    async def _pick_filter(self, chat: ChannelInfo) -> FilterSpec:
        """The filter from flags or a selection, else (at a terminal) the wizard's question. A
        filter that selects everything is only accepted with ``--all`` or an explicit yes."""
        o = self._o
        spec = o.filters
        asked = spec is None and self._interactive and not o.yes
        if asked:
            topics: Sequence[TopicInfo] = ()
            if chat.kind is ChatKind.FORUM:
                topics = await self._gateway.list_topics(chat.id)
            last = await asyncio.to_thread(selection.last_discovery, self._selections_dir, chat.id)
            spec = await wizard.pick_filters(
                self._prompter, can_keep=False, topics=topics, last=last
            )
        spec = spec if spec is not None else FilterSpec()
        if (
            spec.is_empty
            and not o.all_messages
            and not (
                asked
                and await self._prompter.confirm(t("delete.confirm_all", chat=chat.title), False)
            )
        ):
            raise UsageProblem("delete.needs_filter")
        return spec

    # ---- 2. the confirmation ---------------------------------------------------------------

    async def confirmed(self, op: Op) -> bool:
        """Obtain the confirmation of a saved plan; a plan already started has it. With neither a
        terminal nor ``--yes`` the plan stays saved and nothing is deleted (``needs_yes``)."""
        o = self._o
        if op.status is not OpStatus.PLANNED:
            return True  # confirmed when it was first applied
        if o.expect is not None and o.expect != op.planned:
            await self._store.cancel_op(op.id)
            raise UsageProblem("delete.expect_mismatch", count=op.planned, expected=o.expect)
        if o.yes:
            if o.expect is None:
                await self._store.cancel_op(op.id)
                raise UsageProblem("delete.needs_expect", count=op.planned)
            return True
        if not self._interactive:
            if o.backup_to is not None:  # the wish for a backup is part of the saved plan
                resolved = await asyncio.to_thread(o.backup_to.expanduser().resolve)
                await self._store.set_op_options(op.id, backup_dir=str(resolved))
            raise UsageProblem("delete.needs_yes", id=op.id, count=op.planned)
        question = t("delete.type_count", count=op.planned, chat=op.chat_title)
        if (await self._prompter.text(question)).strip() != str(op.planned):
            await self._store.cancel_op(op.id)
            self._echo(t("delete.cancelled", id=op.id))
            raise Declined
        return True

    # ---- 3. the safety net -----------------------------------------------------------------

    async def backup_plan(self, op: Op) -> BackupPlan | None:
        """Whether, and where, the plan is backed up before anything is deleted (docs/07 section
        6.3 point 3). At a terminal it asks (default yes); without one only ``--backup-to`` does,
        and ``--no-backup`` never. A plan whose backup is already complete needs no second one;
        one an earlier attempt left unfinished carries on in the same directory."""
        o = self._o
        if op.status is not OpStatus.PLANNED or op.backup_done or o.no_backup:
            return None  # already started or backed up, or the person said no
        directory = o.backup_to
        if directory is None and op.backup_dir is not None:
            directory = Path(op.backup_dir)
        if directory is None:
            if not (self._interactive and not o.yes):
                return None
            if not await self._prompter.confirm(t("delete.confirm_backup", count=op.planned)):
                return None
            directory = await self._ask_backup_dir(op)
        resolved = await asyncio.to_thread(directory.expanduser().resolve)
        if op.backup_dir != str(resolved):
            # remembered at once: a plan whose backup was asked for is never applied without one,
            # even if this attempt stops at D3 below and `--resume` comes without the flag
            await self._store.set_op_options(op.id, backup_dir=str(resolved))
        return BackupPlan(resolved, await self._acknowledged(op))

    async def _ask_backup_dir(self, op: Op) -> Path:
        suggested = self._rt.paths.data_dir / "safety" / f"O{op.id}"
        if await self._prompter.confirm(t("delete.confirm_backup_dir", dir=suggested)):
            return suggested
        return await wizard.ask_backup_dir(self._prompter)

    async def _acknowledged(self, op: Op) -> bool:
        """Decision D3 for the backup, as ``BackupFlow._confirm_d3``: a chat that restricts saving
        content needs the person's statement. The account administers it (the plan needed that),
        so a question can stand in for the flag at a terminal."""
        o = self._o
        chat = find_channel(self._channels, str(op.chat_id))
        if not chat.noforwards or o.admin_ack:
            return o.admin_ack
        if not (self._interactive and not o.yes):
            raise UsageProblem("err.needs_admin_ack", title=chat.title)
        if not await self._prompter.confirm(t("clone.confirm_protected", title=chat.title), False):
            raise Declined
        return True


async def back_up(
    rt: Runtime,
    store: Store,
    gateway: TelegramGateway,
    op: Op,
    plan: BackupPlan,
    *,
    force_takeover: bool = False,
) -> None:
    """Save the plan's messages before anything is deleted. Goes on only if the backup finished;
    otherwise the plan stays as it was and the command ends (exit 1)."""
    started = await begin_op_backup(
        store, gateway, op, plan.directory, ack=plan.ack, force=force_takeover
    )
    typer.echo(backup_start_line(started.backup.id, op.chat_title, str(plan.directory)))
    final = await run_backup(
        rt,
        store,
        gateway,
        started.backup,
        started.directory,
        started.manifest,
        only_ids=started.ids,
    )
    if not await settle_op_backup(store, op, final):
        typer.echo(delete_backup_stopped_line(op, final))
        raise typer.Exit(1)
    typer.echo(delete_backup_done_line(str(plan.directory)))


async def execute(
    rt: Runtime,
    store: Store,
    gateway: TelegramGateway,
    op: Op,
    *,
    force_takeover: bool = False,
) -> None:
    """Carry out a confirmed operation in the foreground; print progress and the result."""
    control = RunControl()
    limits = rt.config().limits
    running = await begin_op(store, op.id, force=force_takeover)
    typer.echo(delete_start_line(running))
    with (
        stop_on_interrupt(control, lambda: typer.echo(t("run.stopping"), err=True)) as interrupt,
        rt.keys(control) as listening,
    ):
        runner = OpRunner(
            store, gateway, limits, reporter=OpLineReporter(typer.echo), control=control
        )
        if listening:
            typer.echo(t("run.keys_hint"))
        final = await runner.run(running)
    for line in delete_result_lines(final):
        typer.echo(line)
    if interrupt.hit:
        raise typer.Exit(EXIT_INTERRUPTED)
