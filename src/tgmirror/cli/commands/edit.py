"""``tgmirror edit`` and ``tgmirror undo``: change the text of messages of a chat you administer, in
place, and put it back (docs/07-inspect-transform.md sections 6 and 11.4, docs/06-lo-trinh.md
Phase 20.3, decision D10).

``edit`` is ``delete``'s plan -> confirm -> apply (``DeleteFlow``) with another plan: the
``--transform`` file's ``caption`` operations are worked out for every selected message and only
the messages whose text would change are frozen, with the text before and after (``edit_plan_lines``
shows a few as a diff). There is no backup question: the originals are in the plan, and
``undo O<n>`` is the way back (a plan of its own: it is confirmed the same way). Each message is
read again right before it is edited; one that no longer reads what the plan saw is left alone.

Only what a transform can say about a text is accepted: a transform that also routes to topics,
keeps replies or the forward header, or rewrites links is refused (exit 2) — those are about
copying, and ``topics``/``repost`` are the tools that move messages.
"""

import asyncio
from dataclasses import replace
from typing import Annotated

import typer

from tgmirror.cli.commands.delete import DeleteFlow, DeleteOptions, execute
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
from tgmirror.cli.transform_options import TransformOption, resolve
from tgmirror.core.gateway import CaptionPolicy
from tgmirror.engine.ops import UndoError, plan_edit, plan_undo, resolve_op
from tgmirror.engine.transform import Transform
from tgmirror.store.db import Store
from tgmirror.store.ops import Op, OpKind
from tgmirror.ui.lines import edit_plan_lines, undo_plan_lines
from tgmirror.ui.messages import t


def unsupported_in_edit(transform: Transform) -> str | None:
    """What in ``transform`` an edit in place cannot do (``None``: nothing)."""
    found = []
    if not transform.topics.is_empty:
        found.append("`topics`")
    if transform.keep_replies:
        found.append("`reply: keep`")
    if transform.keep_forward_header:
        found.append("`forward_header: keep`")
    if transform.rewrite_links:
        found.append("`links: rewrite`")
    return ", ".join(found) or None


def policy_of(transform: Transform) -> CaptionPolicy:
    """The caption operations of a transform as the policy the gateway applies to a live text."""
    return CaptionPolicy(ops=transform.caption, text_too=transform.text_too)


class EditFlow(DeleteFlow):
    """``DeleteFlow`` with an edit plan: the same chat and filter questions, the same typed-back
    count (its sentences are ``edit.*``), no safety-net backup."""

    _kind = "edit"

    def __init__(self, *args: object, transform: Transform, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._transform = transform

    async def planned(self) -> Op | None:
        chat = await self._pick_chat()
        spec = await self._pick_filter(chat)
        plan = await plan_edit(
            self._store,
            self._gateway,
            chat,
            spec,
            policy_of(self._transform),
            self._transform.to_json(),
            self._rt.config().limits,
            pushdown=self._o.pushdown,
        )
        if plan.op is None:
            summary = plan.summary
            self._echo(
                t(
                    "edit.nothing",
                    chat=chat.title,
                    unchanged=summary.unchanged,
                    lossy=summary.lossy,
                )
            )
            return None
        for line in edit_plan_lines(plan.op, plan.summary):
            self._echo(line)
        return plan.op

    async def backup_plan(self, op: Op) -> None:  # type: ignore[override]
        return None  # the plan keeps the originals: ``undo`` is the way back


class UndoFlow(DeleteFlow):
    """The confirmation of an ``undo`` (its plan is the edit's own items the other way round)."""

    _kind = "undo"

    async def planned_for(self, edit: Op) -> Op:
        try:
            plan = await plan_undo(self._store, self._gateway, edit)
        except UndoError as exc:
            raise UsageProblem(exc.key, **exc.params) from exc
        for line in undo_plan_lines(plan, edit.id):
            self._echo(line)
        return plan.op

    async def backup_plan(self, op: Op) -> None:  # type: ignore[override]
        return None


def edit(
    ctx: typer.Context,
    src: Annotated[str | None, typer.Argument(help=SOURCE_HELP)] = None,
    transform_file: TransformOption = None,
    all_messages: Annotated[
        bool,
        typer.Option(
            "--all", help="Allow an empty filter, which looks at EVERY message of the chat."
        ),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume",
            metavar="O<N>",
            help="Carry on with an earlier edit (its number is in `tgmirror history`), or apply "
            "a plan that was only saved. Not with a source, a filter or a transform.",
        ),
    ] = None,
    expect: Annotated[
        int | None,
        typer.Option(
            "--expect",
            min=0,
            help="With --yes: the number of messages the plan must hold. A different number "
            "edits nothing.",
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
    """Change the text (captions) of the messages a filter selects in a chat you administer.

    What changes is the `caption` operations of a --transform file (strip_links,
    remove_hashtags, remove_mentions, replace, append, ...; `text_too: true` also takes plain text
    messages). It plans first and shows the text before and after for a few messages; then you
    type the number of messages back to confirm. Without a terminal, give --yes --expect N (N from
    the plan); without them it only saves the plan, which `--resume O<n> --yes --expect N` applies
    later. The originals are kept: `tgmirror undo O<n>` puts them back.

    Each message is read again just before it is edited: one that changed since the plan is left
    alone. Albums are edited message by message (only the captions that change).

    Keys while it runs: p pause, r resume, q stop.

    Example: tgmirror edit "@my_channel" --hashtag "#ads" --transform strip.yaml
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
            or transform_file is not None
        ):
            raise UsageProblem(
                "err.conflicting_flags", flags="--resume / SRC / filter / --all / --transform"
            )
        options = DeleteOptions(
            src=src,
            all_messages=all_messages,
            resume=resume,
            expect=expect,
            filters=filters,
            pushdown=pushdown,
            force_takeover=force_takeover,
            yes=yes,
            no_backup=True,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                if resume is not None:
                    flow = EditFlow(
                        rt, store, conn.gateway, channels, options, transform=Transform()
                    )
                    op = await flow.resumed()
                else:
                    from_selection: Transform | None = None
                    if selection_file is not None:
                        chosen, named, from_selection = await asyncio.to_thread(
                            for_channel,
                            rt.paths,
                            channels,
                            src=src,
                            selection=selection_file,
                            exact=exact,
                            takes_transform=True,
                        )
                        options = replace(options, filters=narrowed(chosen, filters), src=named)
                    transform = await resolve(transform_file, from_selection)
                    if unsupported := unsupported_in_edit(transform):
                        raise UsageProblem("edit.unsupported", what=unsupported)
                    if not transform.caption:
                        raise UsageProblem("edit.needs_transform")
                    flow = EditFlow(rt, store, conn.gateway, channels, options, transform=transform)
                    planned = await flow.planned()
                    if planned is None:
                        return
                    op = planned
                if not await flow.confirmed(op):
                    return
                await execute(rt, store, conn.gateway, op, force_takeover=force_takeover)

    run(rt, command())


def undo(
    ctx: typer.Context,
    ref: Annotated[
        str | None,
        typer.Argument(
            metavar="[O<N>]",
            help="The edit to put back (its number is in `tgmirror history`; default: the latest).",
        ),
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume",
            metavar="O<N>",
            help="Carry on with an earlier undo, or apply an undo plan that was only saved.",
        ),
    ] = None,
    expect: Annotated[
        int | None,
        typer.Option(
            "--expect",
            min=0,
            help="With --yes: the number of messages the plan must hold. A different number "
            "changes nothing.",
        ),
    ] = None,
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
    """Put back the original text of the messages an `edit` changed.

    Like `edit` it plans first and asks for the number of messages to be typed back (or --yes
    --expect N). A message edited by somebody else since is left alone. An edit can be undone once.

    Example: tgmirror undo O12
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if resume is not None and ref is not None:
            raise UsageProblem("err.conflicting_flags", flags="O<N> / --resume")
        options = DeleteOptions(
            resume=resume, expect=expect, force_takeover=force_takeover, yes=yes, no_backup=True
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                flow = UndoFlow(rt, store, conn.gateway, channels, options)
                if resume is not None:
                    op = await flow.resumed()
                else:
                    edit_op = await _edit_to_undo(store, ref)
                    op = await flow.planned_for(edit_op)
                if not await flow.confirmed(op):
                    return
                await execute(rt, store, conn.gateway, op, force_takeover=force_takeover)

    run(rt, command())


async def _edit_to_undo(store: Store, ref: str | None) -> Op:
    """The edit ``ref`` names, or the latest edit that changed something."""
    if ref is not None:
        return await resolve_op(store, ref)
    for op in await store.list_ops(50):
        if op.kind is OpKind.EDIT and op.done:
            return op
    raise UsageProblem("undo.no_edit")
