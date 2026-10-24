"""``tgmirror topics``: a forum's topics, and changing them in place (phases 8 and 20b).

``tgmirror topics FORUM`` lists them (rule 1's exception: a one-shot read the user started, like
``channels``). The other verbs change the forum, so they follow decision D10:

* ``split`` / ``merge`` move messages between topics. Telegram cannot move a message, so every
  unit is downloaded and uploaded again into the target topic and only then is its original
  deleted — a ``move`` operation, ``delete``'s plan -> confirm -> apply (``DeleteFlow``): the ids
  are frozen and shown, the person types the number back (or ``--yes --expect N``), the optional
  safety-net backup of the originals comes before anything changes, decision D3 applies (it is a
  download). ``topics move --resume O<n>`` carries one on.
* ``delete`` removes a topic with every message in it: an ordinary ``delete`` operation of the
  topic's messages that also deletes the topic once it is empty.
* ``rename`` / ``close`` / ``reopen`` touch no message: one write each, after a Yes/No (``--yes``
  without a terminal).

``tgmirror topics split|merge|...`` and ``tgmirror topics FORUM`` share the name: a first word
that is not a verb is read as the forum to list (``TopicsGroup``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from typer.core import TyperGroup

from tgmirror.cli.commands.delete import (
    BackupPlan,
    DeleteFlow,
    DeleteOptions,
    back_up,
    execute,
)
from tgmirror.cli.errors import Declined, UsageProblem, run
from tgmirror.cli.refs import SOURCE_HELP, SourceCommand
from tgmirror.cli.runtime import Runtime, authorized, opened_store
from tgmirror.cli.transform_options import (
    TopicPickOption,
    TopicRegexOption,
    TopicSkipOption,
    TransformOption,
    resolve,
    topic_flags,
)
from tgmirror.core.gateway import ChannelInfo, ChatKind, TelegramGateway, TopicInfo
from tgmirror.engine.endpoints import find_channel
from tgmirror.engine.topicops import (
    TopicError,
    change_topic,
    drop_topic,
    find_topic,
    plan_merge,
    plan_split,
    plan_topic_delete,
    topic_named,
)
from tgmirror.engine.transform import Transform
from tgmirror.store.db import Store
from tgmirror.store.ops import Op
from tgmirror.ui.lines import delete_plan_lines, move_plan_lines
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Choice
from tgmirror.ui.tables import channel_label, print_topics, topics_json


class TopicsGroup(TyperGroup):
    """``tgmirror topics FORUM`` lists, as it did before the verbs came: a first word that is not
    a verb (or a marked id such as ``-1002428805566``) is the forum of the ``list`` command. The
    word is put in front of ``list`` before the group reads the line, so a ``--`` still reaches
    the command that knows what to do with it."""

    def parse_args(self, ctx: typer.Context, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and args[0] not in ctx.help_option_names:
            args = ["list", *args]
        return super().parse_args(ctx, args)


topics_app = typer.Typer(
    cls=TopicsGroup,
    help="List a forum's topics, and split, merge, rename, close or delete them in place.",
    no_args_is_help=True,
)

ForumArgument = Annotated[str | None, typer.Argument(help="The forum. " + SOURCE_HELP)]
TopicArgument = Annotated[
    str | None, typer.Argument(help='The topic: its title (any case) or its id, "General" is 1.')
]


@topics_app.command("list", cls=SourceCommand)
def list_topics(
    ctx: typer.Context,
    src: Annotated[str, typer.Argument(help="The forum. " + SOURCE_HELP)],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List a forum's topics (id, title, closed).

    Example: tgmirror topics "@my_forum"
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            channel = find_channel(channels, src)
            if channel.kind is not ChatKind.FORUM:
                raise UsageProblem("err.not_a_forum", title=channel.title)
            found = await conn.gateway.list_topics(channel.id)
        if as_json:
            typer.echo(topics_json(found))
        else:
            print_topics(Console(), found)

    run(rt, command())


# ---- choosing a forum and its topics -----------------------------------------------


async def pick_forum(rt: Runtime, channels: Sequence[ChannelInfo], ref: str | None) -> ChannelInfo:
    """The forum named, or (at a terminal) chosen among those the account administers."""
    if ref is not None:
        chosen = find_channel(channels, ref)
        if chosen.kind is not ChatKind.FORUM:
            raise UsageProblem("err.not_a_forum", title=chosen.title)
        return chosen
    if not rt.interactive:
        raise UsageProblem("err.missing_flag", flag="FORUM")
    forums = [c for c in channels if c.kind is ChatKind.FORUM and c.is_admin]
    if not forums:
        typer.echo(t("topics.no_forums"))
        raise typer.Exit(1)
    return await rt.prompter.select(
        t("topics.pick_forum"), [Choice(channel_label(c), c) for c in forums]
    )


def _topic_choices(
    topics: Sequence[TopicInfo], skip: Sequence[int] = ()
) -> list[Choice[TopicInfo]]:
    return [
        Choice(f"{topic.title} (id {topic.id})", topic) for topic in topics if topic.id not in skip
    ]


async def pick_topic(
    rt: Runtime,
    gateway: TelegramGateway,
    forum: ChannelInfo,
    name: str | None,
    *,
    message: str = "topics.pick_topic",
) -> TopicInfo:
    """The topic named, or (at a terminal) chosen from the forum's."""
    topics = await gateway.list_topics(forum.id)
    if name is not None:
        return _named(topics, name)
    if not rt.interactive:
        raise UsageProblem("topics.needs_topic")
    return await rt.prompter.select(t(message), _topic_choices(topics))


def _named(topics: Sequence[TopicInfo], name: str) -> TopicInfo:
    try:
        return topic_named(topics, name)
    except TopicError as exc:
        raise UsageProblem(exc.key, **exc.params) from exc


# ---- split and merge: a move -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MoveOptions(DeleteOptions):
    """``DeleteOptions`` plus what a move chooses."""

    notify: bool = False


class MoveFlow(DeleteFlow):
    """``DeleteFlow`` with a ``move`` plan: the forum and the topics are asked for (or given), the
    plan is a split or a merge, the typed-back count and the backup question are ``delete``'s, and
    decision D3 applies always (a move downloads)."""

    _kind = "move"

    def __init__(
        self,
        *args: object,
        how: str = "split",
        names: Sequence[str] = (),
        transform: Transform | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._how = how
        self._names = list(names)
        self._transform = transform or Transform()

    async def _pick_chat(self) -> ChannelInfo:
        return await pick_forum(self._rt, self._channels, self._o.src)

    async def planned(self) -> Op | None:
        o = self._o
        assert isinstance(o, MoveOptions)
        forum = await self._pick_chat()
        limits = self._rt.config().limits
        try:
            if self._how == "split":
                topic = await pick_topic(self._rt, self._gateway, forum, self._first_name())
                plan = await plan_split(
                    self._store,
                    self._gateway,
                    forum,
                    topic,
                    self._transform,
                    limits,
                    silent=not o.notify,
                )
                nothing = t("move.nothing_split", topic=topic.title)
            else:
                names = await self._merge_names(forum)
                plan = await plan_merge(
                    self._store, self._gateway, forum, names, limits, silent=not o.notify
                )
                nothing = t("move.nothing_merge")
        except TopicError as exc:
            raise UsageProblem(exc.key, **exc.params) from exc
        if plan.op is None:
            self._echo(nothing)
            return None
        for line in move_plan_lines(plan.op, plan.summary):
            self._echo(line)
        return plan.op

    def _first_name(self) -> str | None:
        return self._names[0] if self._names else None

    async def _merge_names(self, forum: ChannelInfo) -> list[str]:
        """Topics to merge as typed, or (at a terminal) the target and then the topics merged into
        it — in the forum's order, which is the order their messages follow."""
        if self._names:
            return self._names
        if not self._rt.interactive:
            raise UsageProblem("topics.needs_topic")
        topics = await self._gateway.list_topics(forum.id)
        target = await self._rt.prompter.select(
            t("topics.pick_merge_target"), _topic_choices(topics)
        )
        others = await self._rt.prompter.checkbox(
            t("topics.pick_merge_others"),
            _topic_choices(topics, skip=[target.id]),
        )
        return [str(target.id), *(str(topic.id) for topic in others)]


def _move_options(
    *,
    forum: str | None,
    resume: str | None,
    expect: int | None,
    backup_to: Path | None,
    no_backup: bool,
    admin_ack: bool,
    force_takeover: bool,
    yes: bool,
    notify: bool,
) -> MoveOptions:
    if backup_to is not None and no_backup:
        raise UsageProblem("err.conflicting_flags", flags="--backup-to / --no-backup")
    return MoveOptions(
        src=forum,
        resume=resume,
        expect=expect,
        backup_to=backup_to,
        no_backup=no_backup,
        admin_ack=admin_ack,
        force_takeover=force_takeover,
        yes=yes,
        notify=notify,
    )


async def _carry_out(
    rt: Runtime,
    store: Store,
    gateway: TelegramGateway,
    flow: DeleteFlow,
    op: Op,
    *,
    force_takeover: bool,
) -> None:
    """Confirm, then (D3, safety net) apply: what ``repost`` does after its plan."""
    if not await flow.confirmed(op):
        return
    ack = await flow.acknowledged(op)
    backup: BackupPlan | None = await flow.backup_plan(op)
    if backup is not None:
        await back_up(rt, store, gateway, op, backup, force_takeover=force_takeover)
    await execute(rt, store, gateway, op, force_takeover=force_takeover, ack=ack)


NotifyOption = Annotated[
    bool,
    typer.Option(
        "--notify",
        help="Notify the forum's followers of every moved message (by default the posts are "
        "silent: moving many messages would be a flood of notifications).",
    ),
]
ExpectOption = Annotated[
    int | None,
    typer.Option(
        "--expect",
        min=0,
        help="With --yes: the number of messages the plan must hold. A different number moves "
        "nothing.",
    ),
]
BackupToOption = Annotated[
    Path | None,
    typer.Option(
        "--backup-to",
        help="Back the messages up into this directory before anything is posted or deleted "
        "(a plan that is not backed up completely is not applied).",
    ),
]
NoBackupOption = Annotated[
    bool, typer.Option("--no-backup", help="No backup, and do not ask about one.")
]
AckOption = Annotated[
    bool,
    typer.Option(
        "--yes-i-administer-this-channel",
        help="The forum restricts saving content: say that you own it and may copy it (moving "
        "downloads and uploads again). You take full responsibility for that: tgmirror cannot "
        "check it.",
    ),
]
TakeoverOption = Annotated[
    bool,
    typer.Option(
        "--force-takeover",
        help="Carry on even if another process seems to hold this forum (only if dead).",
    ),
]
YesOption = Annotated[
    bool, typer.Option("--yes", "-y", help="Confirm without typing the count; needs --expect N.")
]


@topics_app.command("split", cls=SourceCommand)
def split(
    ctx: typer.Context,
    forum: ForumArgument = None,
    topic: TopicArgument = None,
    transform_file: TransformOption = None,
    hashtag_to_topic: Annotated[
        bool,
        typer.Option(
            "--hashtag-to-topic",
            help="Route by hashtag: a message goes to the topic named after its first hashtag "
            "(add rules with `topics:` in a --transform file).",
        ),
    ] = False,
    topic_skip: TopicSkipOption = None,
    topic_regex: TopicRegexOption = None,
    topic_pick: TopicPickOption = None,
    notify: NotifyOption = False,
    expect: ExpectOption = None,
    backup_to: BackupToOption = None,
    no_backup: NoBackupOption = False,
    admin_ack: AckOption = False,
    force_takeover: TakeoverOption = False,
    yes: YesOption = False,
) -> None:
    """Move the messages of a topic that routing rules send elsewhere into their own topics.

    The rules are those of a transform (`topics:` with `route`, or --hashtag-to-topic): a message
    of TOPIC that a rule names another topic for is posted again in that topic (made when there is
    none) in its original order, and deleted from TOPIC; the rest stay. Telegram cannot move a
    message, so each one is downloaded and uploaded again (new id, new file id, no views or
    comments) and its original deleted only after that. It plans first and you type the number of
    messages back (or --yes --expect N); at a terminal it asks about a backup. Carry on with
    `tgmirror topics move --resume O<n>`.

    Example: tgmirror topics split "@my_forum" "Misc" --hashtag-to-topic --topic-skip "#ads"
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        options = _move_options(
            forum=forum,
            resume=None,
            expect=expect,
            backup_to=backup_to,
            no_backup=no_backup,
            admin_ack=admin_ack,
            force_takeover=force_takeover,
            yes=yes,
            notify=notify,
        )
        transform = await resolve(
            transform_file,
            None,
            topics=topic_flags(
                hashtag_to_topic=hashtag_to_topic,
                topic_skip=topic_skip,
                topic_regex=topic_regex,
                topic_pick=topic_pick,
            ),
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                flow = MoveFlow(
                    rt,
                    store,
                    conn.gateway,
                    channels,
                    options,
                    how="split",
                    names=[topic] if topic is not None else [],
                    transform=transform,
                )
                op = await flow.planned()
                if op is not None:
                    await _carry_out(
                        rt, store, conn.gateway, flow, op, force_takeover=force_takeover
                    )

    run(rt, command())


@topics_app.command("merge", cls=SourceCommand)
def merge(
    ctx: typer.Context,
    forum: ForumArgument = None,
    topics: Annotated[
        list[str] | None,
        typer.Argument(
            help="The topic that receives, then the topics merged into it (title or id), in the "
            "order their messages follow."
        ),
    ] = None,
    notify: NotifyOption = False,
    expect: ExpectOption = None,
    backup_to: BackupToOption = None,
    no_backup: NoBackupOption = False,
    admin_ack: AckOption = False,
    force_takeover: TakeoverOption = False,
    yes: YesOption = False,
) -> None:
    """Merge topics: the later ones' messages follow the first one's, then those topics go.

    Every message of each later topic is posted again at the end of the first topic (topic after
    topic, each in its own order) and deleted from its topic; a later topic that is empty by then
    is deleted (one that still holds a message is kept). Telegram cannot move a message, so each
    one is downloaded and uploaded again (new id, new file id, no views or comments). It plans
    first and you type the number of messages back (or --yes --expect N). Carry on with
    `tgmirror topics move --resume O<n>`.

    Example: tgmirror topics merge "@my_forum" "Announcements" "News" "Old news"
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        options = _move_options(
            forum=forum,
            resume=None,
            expect=expect,
            backup_to=backup_to,
            no_backup=no_backup,
            admin_ack=admin_ack,
            force_takeover=force_takeover,
            yes=yes,
            notify=notify,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                flow = MoveFlow(
                    rt, store, conn.gateway, channels, options, how="merge", names=topics or []
                )
                op = await flow.planned()
                if op is not None:
                    await _carry_out(
                        rt, store, conn.gateway, flow, op, force_takeover=force_takeover
                    )

    run(rt, command())


@topics_app.command("move")
def move(
    ctx: typer.Context,
    resume: Annotated[
        str,
        typer.Option(
            "--resume",
            metavar="O<N>",
            help="The split or merge to carry on with (its number is in `tgmirror history`), or "
            "a plan that was only saved.",
        ),
    ],
    notify: NotifyOption = False,
    expect: ExpectOption = None,
    backup_to: BackupToOption = None,
    no_backup: NoBackupOption = False,
    admin_ack: AckOption = False,
    force_takeover: TakeoverOption = False,
    yes: YesOption = False,
) -> None:
    """Carry on with a split or merge that stopped, or apply one whose plan was only saved.

    Example: tgmirror topics move --resume O14 --yes --expect 120
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        options = _move_options(
            forum=None,
            resume=resume,
            expect=expect,
            backup_to=backup_to,
            no_backup=no_backup,
            admin_ack=admin_ack,
            force_takeover=force_takeover,
            yes=yes,
            notify=notify,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                flow = MoveFlow(rt, store, conn.gateway, channels, options)
                op = await flow.resumed()
                await _carry_out(rt, store, conn.gateway, flow, op, force_takeover=force_takeover)

    run(rt, command())


# ---- delete a topic ----------------------------------------------------------------


class TopicDeleteFlow(DeleteFlow):
    """``delete``'s flow for the messages of one topic (its sentences are ``delete.*``): the plan is
    the topic's messages and, when they are gone, the topic goes too."""

    def __init__(self, *args: object, name: str | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._name = name

    async def _pick_chat(self) -> ChannelInfo:
        return await pick_forum(self._rt, self._channels, self._o.src)

    async def planned(self) -> Op | None:
        forum = await self._pick_chat()
        limits = self._rt.config().limits
        topic = await pick_topic(self._rt, self._gateway, forum, self._name)
        try:
            topic, plan = await plan_topic_delete(
                self._store, self._gateway, forum, str(topic.id), limits
            )
        except TopicError as exc:
            raise UsageProblem(exc.key, **exc.params) from exc
        if plan.op is None:  # nothing in it: only the empty topic to remove
            await self._drop_empty(forum, topic)
            return None
        self._echo(t("topics.delete_note", name=topic.title, count=plan.op.planned))
        for line in delete_plan_lines(plan.op, plan.summary):
            self._echo(line)
        return plan.op

    async def _drop_empty(self, forum: ChannelInfo, topic: TopicInfo) -> None:
        question = t("topics.confirm_drop_empty", name=topic.title, chat=forum.title)
        await _confirm_small(self._rt, self._o.yes, question)
        try:
            await drop_topic(self._store, self._gateway, forum, topic, self._rt.config().limits)
        except TopicError as exc:
            raise UsageProblem(exc.key, **exc.params) from exc
        self._echo(t("topics.done_drop", name=topic.title))

    async def backup_plan(self, op: Op) -> BackupPlan | None:
        return await super().backup_plan(op)


@topics_app.command("delete", cls=SourceCommand)
def delete_topic(
    ctx: typer.Context,
    forum: ForumArgument = None,
    topic: TopicArgument = None,
    expect: ExpectOption = None,
    backup_to: BackupToOption = None,
    no_backup: NoBackupOption = False,
    admin_ack: AckOption = False,
    force_takeover: TakeoverOption = False,
    yes: YesOption = False,
) -> None:
    """Delete a topic together with every message in it. This cannot be undone.

    It plans first (the topic's messages are frozen and shown), you type the number of messages
    back (or --yes --expect N), and at a terminal it asks about a backup; then the messages are
    deleted and the topic with them. General cannot be deleted. Carry on with
    `tgmirror delete --resume O<n>`.

    Example: tgmirror topics delete "@my_forum" "Old news" --backup-to ./before
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        if backup_to is not None and no_backup:
            raise UsageProblem("err.conflicting_flags", flags="--backup-to / --no-backup")
        options = DeleteOptions(
            src=forum,
            expect=expect,
            backup_to=backup_to,
            no_backup=no_backup,
            admin_ack=admin_ack,
            force_takeover=force_takeover,
            yes=yes,
            all_messages=True,
        )
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            async with opened_store(rt) as store:
                flow = TopicDeleteFlow(rt, store, conn.gateway, channels, options, name=topic)
                op = await flow.planned()
                if op is None or not await flow.confirmed(op):
                    return
                if (backup := await flow.backup_plan(op)) is not None:
                    await back_up(
                        rt, store, conn.gateway, op, backup, force_takeover=force_takeover
                    )
                await execute(rt, store, conn.gateway, op, force_takeover=force_takeover)

    run(rt, command())


# ---- rename, close, reopen: no message changes -------------------------------------


async def _confirm_small(rt: Runtime, yes: bool, question: str) -> None:
    """A change that touches no message: a Yes/No at a terminal, ``--yes`` without one."""
    if yes:
        return
    if not rt.interactive:
        raise UsageProblem("topics.needs_yes")
    if not await rt.prompter.confirm(question, False):
        typer.echo(t("topics.declined"))
        raise Declined


async def _change(
    rt: Runtime,
    forum_ref: str | None,
    topic_ref: str | None,
    *,
    yes: bool,
    title: str | None = None,
    closed: bool | None = None,
) -> None:
    async with authorized(rt) as conn:
        channels = await conn.gateway.list_channels()
        forum = await pick_forum(rt, channels, forum_ref)
        topic = await pick_topic(rt, conn.gateway, forum, topic_ref)
        async with opened_store(rt) as store:
            try:
                # Telegram's list of topics lags behind a change by a few seconds: the topic
                # picked above is the one that is acted on, not a second reading of the list
                forum, _ = await find_topic(
                    store, conn.gateway, forum, str(topic.id), rt.config().limits
                )
                if title is not None:
                    key, done = "topics.confirm_rename", "topics.done_rename"
                elif closed:
                    key, done = "topics.confirm_close", "topics.done_close"
                else:
                    key, done = "topics.confirm_reopen", "topics.done_reopen"
                question = t(
                    key, name=topic.title, old=topic.title, new=title or "", chat=forum.title
                )
                await _confirm_small(rt, yes, question)
                await change_topic(
                    store,
                    conn.gateway,
                    forum,
                    topic,
                    rt.config().limits,
                    title=title,
                    closed=closed,
                )
            except TopicError as exc:
                raise UsageProblem(exc.key, **exc.params) from exc
        typer.echo(t(done, name=topic.title, old=topic.title, new=title or ""))


@topics_app.command("rename", cls=SourceCommand)
def rename(
    ctx: typer.Context,
    forum: ForumArgument = None,
    topic: TopicArgument = None,
    new_title: Annotated[str | None, typer.Argument(help="The new title.")] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask (needed without a terminal).")
    ] = False,
) -> None:
    """Rename a topic. No message changes.

    Example: tgmirror topics rename "@my_forum" "Old name" "New name"
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        title = new_title
        if title is None:
            if not rt.interactive:
                raise UsageProblem("err.missing_flag", flag="NEW_TITLE")
            title = (await rt.prompter.text(t("topics.pick_new_title"))).strip()
        if not title.strip():
            raise UsageProblem("err.missing_flag", flag="NEW_TITLE")
        await _change(rt, forum, topic, yes=yes, title=title.strip())

    run(rt, command())


@topics_app.command("close", cls=SourceCommand)
def close(
    ctx: typer.Context,
    forum: ForumArgument = None,
    topic: TopicArgument = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask (needed without a terminal).")
    ] = False,
) -> None:
    """Close a topic: members can no longer post in it (administrators can). No message changes.

    Example: tgmirror topics close "@my_forum" "Old news"
    """
    rt: Runtime = ctx.obj
    run(rt, _change(rt, forum, topic, yes=yes, closed=True))


@topics_app.command("reopen", cls=SourceCommand)
def reopen(
    ctx: typer.Context,
    forum: ForumArgument = None,
    topic: TopicArgument = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask (needed without a terminal).")
    ] = False,
) -> None:
    """Open a closed topic again. No message changes.

    Example: tgmirror topics reopen "@my_forum" "Old news"
    """
    rt: Runtime = ctx.obj
    run(rt, _change(rt, forum, topic, yes=yes, closed=False))
