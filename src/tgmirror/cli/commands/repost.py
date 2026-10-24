"""``tgmirror repost``: post messages of a chat you administer again, at its end, as new messages
with new file ids (docs/07-inspect-transform.md sections 6 and 11.3, docs/06-lo-trinh.md Phase
20.4, decision D10).

Each unit is downloaded and uploaded again (strategy B), never forwarded or sent by reference: the
point is a message that is new through and through — a new id, a new file id, no "Forwarded from",
no ties to the old views and comments. That makes it a download, so decision D3 applies exactly as
for a backup: a chat that restricts saving content needs ``--yes-i-administer-this-channel`` (or, at
a terminal, the question that stands in for it).

It is ``delete``'s plan -> confirm -> apply (``DeleteFlow``) with another operation. By default the
originals are kept; ``--delete-original`` removes each one after its copy exists (never before: a
unit that cannot be posted again, a poll or a game, is left alone and its original is never
touched). Deleting originals asks for the safety-net backup like ``delete`` does. Posting is silent
by default: reposting many messages on a live channel would notify everybody who follows it.
"""

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli.commands.delete import (
    BackupPlan,
    DeleteFlow,
    DeleteOptions,
    back_up,
    execute,
)
from tgmirror.cli.errors import UsageProblem, run
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
from tgmirror.cli.refs import SOURCE_HELP
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.cli.selection_options import (
    ExactOption,
    SelectionOption,
    check_args,
    for_channel,
    narrowed,
)
from tgmirror.engine.ops import plan_repost
from tgmirror.store.ops import Op
from tgmirror.ui.lines import delete_plan_lines
from tgmirror.ui.messages import t


@dataclass(frozen=True, slots=True)
class RepostOptions(DeleteOptions):
    """``DeleteOptions`` plus what only a repost chooses."""

    delete_original: bool = False
    notify: bool = False


class RepostFlow(DeleteFlow):
    """``DeleteFlow`` with a repost plan: the same chat and filter questions and the same typed-back
    count (its sentences are ``repost.*``); the backup question only when originals are deleted, and
    decision D3 always (a repost downloads)."""

    _kind = "repost"

    async def planned(self) -> Op | None:
        o = self._o
        assert isinstance(o, RepostOptions)
        chat = await self._pick_chat()
        spec = await self._pick_filter(chat)
        plan = await plan_repost(
            self._store,
            self._gateway,
            chat,
            spec,
            self._rt.config().limits,
            delete_original=o.delete_original,
            silent=not o.notify,
            pushdown=o.pushdown,
        )
        if plan.op is None:
            self._echo(t("repost.nothing", chat=chat.title))
            return None
        for line in delete_plan_lines(plan.op, plan.summary):
            self._echo(line)
        return plan.op

    async def confirmed(self, op: Op) -> bool:
        # the question names what happens to the originals: say it in the typed-back sentence
        return await super().confirmed(op)

    async def backup_plan(self, op: Op) -> BackupPlan | None:
        """Only a repost that deletes the originals has something to back up first."""
        return await super().backup_plan(op) if op.delete_original else None


def repost(
    ctx: typer.Context,
    src: Annotated[str | None, typer.Argument(help=SOURCE_HELP)] = None,
    delete_original: Annotated[
        bool,
        typer.Option(
            "--delete-original",
            help="Delete each original once its copy exists. This cannot be undone: it asks "
            "whether to back the messages up first (like `delete`).",
        ),
    ] = False,
    notify: Annotated[
        bool,
        typer.Option(
            "--notify",
            help="Notify the chat's followers of every new message (by default the posts are "
            "silent: reposting many messages would be a flood of notifications).",
        ),
    ] = False,
    all_messages: Annotated[
        bool,
        typer.Option(
            "--all", help="Allow an empty filter, which selects EVERY message of the chat."
        ),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume",
            metavar="O<N>",
            help="Carry on with an earlier repost (its number is in `tgmirror history`), or apply "
            "a plan that was only saved. Not with a source, a filter or --delete-original.",
        ),
    ] = None,
    expect: Annotated[
        int | None,
        typer.Option(
            "--expect",
            min=0,
            help="With --yes: the number of messages the plan must hold. A different number "
            "reposts nothing.",
        ),
    ] = None,
    backup_to: Annotated[
        Path | None,
        typer.Option(
            "--backup-to",
            help="With --delete-original: back the messages up into this directory before "
            "anything is posted or deleted.",
        ),
    ] = None,
    no_backup: Annotated[
        bool,
        typer.Option(
            "--no-backup", help="With --delete-original: no backup, and do not ask about one."
        ),
    ] = False,
    admin_ack: Annotated[
        bool,
        typer.Option(
            "--yes-i-administer-this-channel",
            help="The chat restricts saving content: say that you own it and may copy it (a "
            "repost downloads and uploads again). You take full responsibility for that: "
            "tgmirror cannot check it.",
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
    """Post the messages a filter selects again, at the end of a chat you administer.

    Each message is downloaded and uploaded again: the new one has a new id and a new file id,
    no "Forwarded from" header, and none of the old views, reactions or comments; links and
    replies to the old ids no longer point at it. Posting is silent unless --notify. It plans
    first and shows what it would do; then you type the number of messages back to confirm.
    Without a terminal, give --yes --expect N (N from the plan); without them it only saves the
    plan, which `--resume O<n> --yes --expect N` applies later.

    The originals are kept unless --delete-original, which removes each one after its copy
    exists (it asks about a backup first). A chat that restricts saving content needs
    --yes-i-administer-this-channel (decision D3). Polls, games and invoices cannot be posted
    again: they, and their originals, are left alone.

    Keys while it runs: p pause, r resume, q stop.

    Example: tgmirror repost "@my_channel" --forwarded --hashtag "#news" --delete-original
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
            src is not None
            or filters is not None
            or selection_file is not None
            or all_messages
            or delete_original
        ):
            raise UsageProblem(
                "err.conflicting_flags", flags="--resume / SRC / filter / --all / --delete-original"
            )
        if backup_to is not None and no_backup:
            raise UsageProblem("err.conflicting_flags", flags="--backup-to / --no-backup")
        options = RepostOptions(
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
            delete_original=delete_original,
            notify=notify,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                flow = RepostFlow(rt, store, conn.gateway, channels, options)
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
                        flow = RepostFlow(rt, store, conn.gateway, channels, options)
                    planned = await flow.planned()
                    if planned is None:
                        return
                    op = planned
                if not await flow.confirmed(op):
                    return
                # D3: a repost downloads, so the statement is asked for every time it starts
                ack = await flow.acknowledged(op)
                if (backup := await flow.backup_plan(op)) is not None:
                    await back_up(
                        rt, store, conn.gateway, op, backup, force_takeover=force_takeover
                    )
                await execute(rt, store, conn.gateway, op, force_takeover=force_takeover, ack=ack)

    run(rt, command())
