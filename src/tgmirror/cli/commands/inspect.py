"""``tgmirror inspect``: what a source holds and what a filter selects (phase 18a,
docs/07-inspect-transform.md). Read-only: it never writes to Telegram and never downloads media,
so it needs no decision-D3 statement even for a source that restricts saving content.

The source is a channel/group/forum the account joined, or a backup directory (then nothing goes
over the network at all). A channel is read through ``FloodGuard`` like a run's source is: a scan
of a big channel is thousands of read requests, not a one-shot call (hard rule 1).
"""

import asyncio
import csv
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, TextIO

import typer
from rich.console import Console
from rich.text import Text

from tgmirror.cli import wizard
from tgmirror.cli.errors import EXIT_INTERRUPTED, UsageProblem, run
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
from tgmirror.cli.transform_options import (
    HashtagToTopicOption,
    KeepForwardHeaderOption,
    KeepRepliesOption,
    RewriteLinksOption,
    StripRoutingHashtagOption,
    TopicDefaultOption,
    TopicMapOption,
    TransformOption,
    topic_flags,
)
from tgmirror.cli.transform_options import resolve as resolve_transform
from tgmirror.core.config import Limits
from tgmirror.core.errors import FloodWait
from tgmirror.core.gateway import (
    CaptionMode,
    ChannelInfo,
    ChatKind,
    MediaCounters,
    MessageReader,
    Unit,
)
from tgmirror.engine import index as indexing
from tgmirror.engine import inspect as engine
from tgmirror.engine import selection
from tgmirror.engine.backup_reader import BackupReader
from tgmirror.engine.backupdir import manifest_path, read_manifest
from tgmirror.engine.endpoints import find_channel
from tgmirror.engine.flood import Interrupted, inspect_guard
from tgmirror.engine.preflight import KINDS, PlanOptions, Preflight, plan_to_dict
from tgmirror.engine.runner import RunControl
from tgmirror.engine.runs import RunRequest, check_options
from tgmirror.engine.transform import Transform
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec
from tgmirror.store.index import MetadataIndex
from tgmirror.ui.messages import t
from tgmirror.ui.tables import (
    channel_label,
    print_listing,
    print_plan,
    print_quick,
    print_report,
    quick_rows,
)

PROGRESS_INTERVAL = 5.0  # seconds between two "reading..." lines
NAP_STEP = 1.0  # a FloodWait sleep looks for Ctrl+C this often
DEFAULT_LIST_LIMIT = 50


@dataclass(frozen=True, slots=True)
class Source:
    """What is being inspected: the reader, and what the report needs to describe it."""

    reader: MessageReader
    id: int
    label: str
    username: str | None
    forum: bool
    topic_titles: Mapping[int, str]
    protected: bool = False  # the source restricts saving content (decision D3)
    title: str = ""
    backup: str | None = None  # the directory, when the source is a backup
    feed: "IndexFeed | None" = None  # a channel read through its index (phase 18b)


@dataclass(frozen=True, slots=True)
class IndexFeed:
    """How a ``Source`` whose reader is an index keeps it up to date: the index, the live reader
    (behind ``FloodGuard``) to read the newer messages through, and what the index says about the
    source. ``live`` is ``None`` for ``--offline``: the index is used as it is."""

    index: MetadataIndex
    source: indexing.IndexedSource
    live: MessageReader | None


class _Notices:
    """``FloodGuard``'s notifier: a flood during a scan is said on stderr, never in the report."""

    def notice(self, code: str, **params: object) -> None:
        typer.echo(t(f"inspect.{code}", **params), err=True)


def _classify(src: str | None) -> tuple[str, Path | None]:
    """``backup`` (a directory holding ``backup.json``), ``not_a_backup`` (a directory that
    is clearly meant as a path but holds none) or ``channel`` (anything else is a chat reference:
    a title may well also be the name of a folder here)."""
    if src is None:
        return "channel", None
    candidate = Path(src).expanduser()
    try:
        if not candidate.is_dir():
            return "channel", None
        if manifest_path(candidate).exists():
            return "backup", candidate
    except OSError:
        return "channel", None
    looks_like_path = "/" in src or "\\" in src or src.startswith((".", "~"))
    return ("not_a_backup", candidate) if looks_like_path else ("channel", None)


def _validated_by(by: list[str] | None) -> tuple[str, ...]:
    chosen = tuple(dict.fromkeys(by or engine.DEFAULT_BY))
    for value in chosen:
        if value not in engine.BY_CHOICES:
            raise UsageProblem(
                "inspect.by_unknown", value=value, choices=", ".join(engine.BY_CHOICES)
            )
    return chosen


def inspect_command(
    ctx: typer.Context,
    src: Annotated[
        str | None,
        typer.Argument(help="A backup directory (read offline), or " + SOURCE_HELP),
    ] = None,
    by: Annotated[
        list[str] | None,
        typer.Option(
            "--by",
            help="A table to show; repeat for several: " + ", ".join(engine.BY_CHOICES) + ". "
            "Default: " + ", ".join(engine.DEFAULT_BY) + ".",
        ),
    ] = None,
    top: Annotated[
        int,
        typer.Option(
            "--top", min=0, help="Rows per ranking table, the rest as 'other' (0: all of them)."
        ),
    ] = engine.DEFAULT_TOP,
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
    list_messages: Annotated[
        bool, typer.Option("--list", help="Also list the messages the filter selects.")
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", min=0, help="How many messages --list shows (0: all).")
    ] = DEFAULT_LIST_LIMIT,
    as_json: Annotated[
        bool, typer.Option("--json", help="Machine-readable output (the list is included).")
    ] = False,
    csv_path: Annotated[
        Path | None,
        typer.Option(
            "--csv",
            help="Write every selected message to this CSV file (UTF-8 with BOM, for Excel).",
        ),
    ] = None,
    save: Annotated[
        Path | None,
        typer.Option(
            "--save",
            help="Save the filter as a selection file (YAML) that clone/backup/restore take with "
            "--selection. The last scan of each source is also kept as @last.",
        ),
    ] = None,
    freeze: Annotated[
        bool,
        typer.Option(
            "--freeze",
            help="With --save: also keep the ids of the selected messages (a .ids file beside "
            "it, one id a line, editable), for --selection ... --exact.",
        ),
    ] = False,
    plan: Annotated[
        str | None,
        typer.Option(
            "--plan",
            help="Pre-flight: what clone, backup or restore (a backup directory as the source) "
            "would do with the selection: how its units travel, how much moves, how long the "
            "limiter keeps it. Takes the flags below like the real command.",
        ),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="With --plan clone: auto (default), copy, reupload."),
    ] = None,
    caption: Annotated[
        str | None,
        typer.Option("--caption", help="With --plan: keep (default), strip-links, append, none."),
    ] = None,
    caption_text: Annotated[
        str | None, typer.Option("--caption-text", help="Text for --caption append.")
    ] = None,
    transform_file: TransformOption = None,
    hashtag_to_topic: HashtagToTopicOption = False,
    topic_map: TopicMapOption = None,
    topic_default: TopicDefaultOption = None,
    strip_routing_hashtag: StripRoutingHashtagOption = False,
    keep_replies: KeepRepliesOption = False,
    rewrite_links: RewriteLinksOption = False,
    keep_forward_header: KeepForwardHeaderOption = False,
    reset_polls: Annotated[
        bool, typer.Option("--reset-polls", help="With --plan: polls are re-created.")
    ] = False,
    ignore_unsupported: Annotated[
        bool,
        typer.Option("--ignore-unsupported", help="With --plan: leave out what cannot be copied."),
    ] = False,
    placeholder: Annotated[
        bool,
        typer.Option("--placeholder", help="With --plan: like --ignore-unsupported, plus a note."),
    ] = False,
    topic_as_hashtag: Annotated[
        bool,
        typer.Option(
            "--topic-as-hashtag",
            help="With --plan clone: a forum source into a destination without topics.",
        ),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Read the whole source again into its local index (catches messages edited or "
            "deleted since; by default only the messages newer than the index are read).",
        ),
    ] = False,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Report from the local index as it is: no account, no Telegram. The source must "
            "have been inspected before (an id, @username or title the index knows).",
        ),
    ] = False,
    no_index: Annotated[
        bool,
        typer.Option(
            "--no-index",
            help="Read the source live and keep nothing (the way inspect worked before the "
            "local index).",
        ),
    ] = False,
    quick: Annotated[
        bool,
        typer.Option(
            "--quick",
            help="Telegram's own counters (photos, videos, documents, links, ...) in one "
            "request, without reading the source: for a first look at a big or unknown channel. "
            "Whole source, or one forum topic with --topic ID; no other filter.",
        ),
    ] = False,
) -> None:
    """Count what a source holds and what a filter selects, by media, month, hashtag, ...

    Reads the whole source (a channel of 100,000 messages: ~1000 pages, about 18 minutes) and
    writes nothing to Telegram. Ctrl+C stops the scan and prints what was read so far.

    Filters are the same as `clone`/`backup`/`restore`, so a report and a copy of the same flags
    agree on what is selected.

    With --plan the report ends with what a clone/backup/restore of the selection would do.

    Example: tgmirror inspect "@my_channel" --media video --since 2024-01-01 --by month
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        quick_topic: int | None = None
        if quick:
            quick_topic = _quick_scope(
                topic,
                others=[
                    media,
                    hashtag,
                    contains,
                    regex,
                    exclude_regex,
                    exclude_media,
                    since,
                    until,
                    min_size,
                    max_size,
                    album,
                    filter_file,
                    from_user,
                    forwarded is not None,
                    forwarded_from,
                    has_link,
                    link_domain,
                    by,
                    save,
                    plan,
                    csv_path,
                    mode,
                    caption,
                    caption_text,
                    transform_file,
                    topic_map,
                    topic_default,
                    list_messages,
                    freeze,
                    offline,
                    refresh,
                    no_index,
                    hashtag_to_topic,
                    strip_routing_hashtag,
                    keep_replies,
                    rewrite_links,
                    keep_forward_header,
                    reset_polls,
                    ignore_unsupported,
                    placeholder,
                    topic_as_hashtag,
                ],  # fmt: skip
            )
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
        )  # a bad filter is a usage error before anything is read
        transform = await resolve_transform(  # a bad file: usage error, nothing read
            transform_file,
            topics=topic_flags(
                hashtag_to_topic=hashtag_to_topic,
                topic_map=topic_map,
                topic_default=topic_default,
                strip_routing_hashtag=strip_routing_hashtag,
                keep_replies=keep_replies,
                rewrite_links=rewrite_links,
                keep_forward_header=keep_forward_header,
            ),
        )
        tables = _validated_by(by)
        if freeze and save is None:
            raise UsageProblem("inspect.freeze_needs_save")
        if offline and (refresh or no_index):
            raise UsageProblem(
                "err.conflicting_flags",
                flags="--offline / " + ("--refresh" if refresh else "--no-index"),
            )
        if refresh and no_index:
            raise UsageProblem("err.conflicting_flags", flags="--refresh / --no-index")
        kind, directory = await asyncio.to_thread(_classify, src)
        if kind == "backup" and (refresh or no_index):
            raise UsageProblem("inspect.index_not_for_backup")
        if kind == "backup" and quick:
            raise UsageProblem("inspect.quick_backup")
        limits = rt.config().limits
        planned = _plan_options(
            plan,
            from_backup=kind == "backup",
            mode=mode,
            caption=caption,
            caption_text=caption_text,
            transform=transform,
            reset_polls=reset_polls,
            ignore_unsupported=ignore_unsupported,
            placeholder=placeholder,
            topic_as_hashtag=topic_as_hashtag,
        )
        job = _Job(
            filters=filters,
            by=tables,
            top=top,
            list_messages=list_messages,
            limit=limit,
            as_json=as_json,
            csv_path=csv_path,
            plan=planned,
            limits=limits,
            save=save,
            freeze=freeze,
            selections_dir=rt.paths.selections_dir,
            index_dir=rt.paths.index_dir,
            refresh=refresh,
            offline=offline,
            use_index=not no_index,
        )
        if offline and kind != "backup":
            if src is None:
                raise UsageProblem("err.missing_flag", flag="SRC")
            await _inspect_offline(src, job)
            return
        if kind == "backup":
            assert directory is not None
            await _inspect_backup(directory, job)
            return
        if kind == "not_a_backup":
            raise UsageProblem("err.not_a_backup", dir=directory)
        async with authorized(rt) as conn:
            channels = await conn.gateway.list_channels()
            if src is not None:
                channel = find_channel(channels, src)
            elif rt.interactive:
                channel = await wizard.pick_source(rt.prompter, channels)
            else:
                raise UsageProblem("err.missing_flag", flag="SRC")
            async with opened_store(rt) as store:
                control = RunControl()

                async def nap(seconds: float) -> None:
                    remaining = seconds
                    while remaining > 0:
                        step = min(remaining, NAP_STEP)
                        await asyncio.sleep(step)
                        remaining -= step
                        if control.stop_requested:
                            raise Interrupted

                guard = await inspect_guard(store, limits, notifier=_Notices(), nap=nap)
                reader = guard.reader(conn.gateway)
                if quick:
                    if quick_topic is not None:  # a topic Telegram does not have fails server-side
                        await _check_topic(reader, channel, quick_topic)
                    counters = await reader.media_counters(channel.id, quick_topic)
                    _print_quick(counters, channel_label(channel), job.as_json)
                    return
                titles: dict[int, str] = {}
                if channel.kind is ChatKind.FORUM:
                    titles = {tp.id: tp.title for tp in await reader.list_topics(channel.id)}
                source = Source(
                    reader=reader,
                    id=channel.id,
                    label=channel_label(channel),
                    username=channel.username,
                    forum=channel.kind is ChatKind.FORUM,
                    topic_titles=titles,
                    protected=channel.noforwards,
                    title=channel.title,
                )
                if not job.use_index:
                    await _inspect(source, job, control)
                    return
                known = indexing.IndexedSource(
                    id=channel.id,
                    title=channel.title,
                    username=channel.username,
                    kind=channel.kind,
                    noforwards=channel.noforwards,
                    topics=dict(titles),
                )
                index = await MetadataIndex.open(indexing.path_of(job.index_dir, channel.id))
                try:
                    feed = IndexFeed(index, known, reader)
                    await _inspect(
                        replace(source, reader=indexing.IndexReader(index, known), feed=feed),
                        job,
                        control,
                    )
                finally:
                    await index.close()

    run(rt, command())


def _quick_scope(topic: list[int] | None, *, others: list[object]) -> int | None:
    """The one topic ``--quick`` counts (or ``None``: the whole source). The counters answer for
    a chat or a topic and nothing else, so any flag that asks for more is refused rather than
    quietly not applied."""
    if (topic is not None and len(topic) != 1) or any(v not in (None, False) for v in others):
        raise UsageProblem("inspect.quick_alone")
    return topic[0] if topic else None


async def _check_topic(reader: MessageReader, channel: ChannelInfo, topic: int) -> None:
    """``--quick --topic N`` asks the server for one topic's counters; an id that is no topic of
    this source made Telegram answer with an internal error six times in a row, which says
    nothing to the person. One cheap read says it plainly instead."""
    if channel.kind is not ChatKind.FORUM:
        raise UsageProblem("inspect.quick_topic_not_forum", src=channel.title)
    known = {tp.id for tp in await reader.list_topics(channel.id)}
    if topic != 1 and topic not in known:
        raise UsageProblem("inspect.quick_topic_unknown", topic=topic, src=channel.title)


def _print_quick(counters: MediaCounters, label: str, as_json: bool) -> None:
    if as_json:
        data = {"source": label, "topic": counters.topic_id, "counts": dict(quick_rows(counters))}
        typer.echo(_dumps({"quick": data}))
        return
    print_quick(Console(), counters, source=label)


def _plan_options(
    plan: str | None,
    *,
    from_backup: bool,
    mode: str | None,
    caption: str | None,
    caption_text: str | None,
    transform: Transform,
    reset_polls: bool,
    ignore_unsupported: bool,
    placeholder: bool,
    topic_as_hashtag: bool,
) -> PlanOptions | None:
    """The ``--plan`` flags as ``PlanOptions``, checked like the real command checks them (the
    same ``check_options``), before anything is read."""
    given = (
        mode is not None
        or caption is not None
        or caption_text is not None
        or not transform.is_empty
        or reset_polls
        or ignore_unsupported
        or placeholder
        or topic_as_hashtag
    )
    if plan is None:
        if given:
            raise UsageProblem("inspect.plan_flags_need_plan")
        return None
    if plan not in KINDS:
        raise UsageProblem("inspect.plan_unknown", value=plan)
    if plan == "restore" and not from_backup:
        raise UsageProblem("inspect.plan_restore_needs_backup")
    if plan != "restore" and from_backup:
        raise UsageProblem("inspect.plan_needs_channel", kind=plan)
    if plan == "backup" and given:
        raise UsageProblem("inspect.plan_backup_options")
    # a restore always sends again: for the check it is a reupload run, as ``restore`` makes it
    request = RunRequest(
        mode="reupload" if plan == "restore" else (mode or "auto"),
        caption=caption or "keep",
        caption_text=caption_text or "",
        transform_json=transform.to_json(),
        reset_polls=reset_polls,
        ignore_unsupported=ignore_unsupported,
        placeholder=placeholder,
        topic_as_hashtag=topic_as_hashtag,
    )
    check_options(request)
    return PlanOptions(
        kind=plan,
        mode=request.mode,
        caption=CaptionMode(request.caption),
        caption_text=request.caption_text,
        transform=transform,
        topic_hashtag=topic_as_hashtag,
        reset_polls=reset_polls,
        ignore_unsupported=ignore_unsupported,
        placeholder=placeholder,
    )


@dataclass(frozen=True, slots=True)
class _Job:
    filters: FilterSpec | None
    by: tuple[str, ...]
    top: int
    list_messages: bool
    limit: int
    as_json: bool
    csv_path: Path | None
    plan: PlanOptions | None
    limits: Limits
    save: Path | None
    freeze: bool
    selections_dir: Path
    index_dir: Path
    refresh: bool = False  # read the whole source again into the index
    offline: bool = False  # use the index as it is
    use_index: bool = True  # False: ``--no-index``, the live scan of phase 18a


async def _inspect_offline(ref: str, job: _Job) -> None:
    """A channel from its local index alone: no account, no limiter, no Telegram."""
    found = await indexing.find(job.index_dir, ref)
    if found is None:
        raise UsageProblem("inspect.offline_no_index", ref=ref)
    index, known = found
    try:
        source = Source(
            reader=indexing.IndexReader(index, known),
            id=known.id,
            label=f"{known.title} ({known.id})" if known.title else str(known.id),
            username=known.username,
            forum=known.kind is ChatKind.FORUM,
            topic_titles=known.topics,
            protected=known.noforwards,
            title=known.title,
            feed=IndexFeed(index, known, None),
        )
        await _inspect(source, job, RunControl())
    finally:
        await index.close()


async def _inspect_backup(directory: Path, job: _Job) -> None:
    """A backup directory: read from disk, no account, no store, no limiter."""
    manifest = await asyncio.to_thread(read_manifest, directory)
    assert manifest is not None  # _classify checked
    source = Source(
        reader=await asyncio.to_thread(BackupReader, directory, manifest),
        id=manifest.src_id,
        label=f"{manifest.src_title} (backup {directory})",
        username=None,
        forum=manifest.src_kind is ChatKind.FORUM,
        topic_titles={tp.id: tp.title for tp in manifest.topics},
        protected=manifest.src_noforwards,
        title=manifest.src_title,
        backup=str(directory),
    )
    await _inspect(source, job, RunControl())


async def _inspect(source: Source, job: _Job, control: RunControl) -> None:
    """Scan, then print: shared by a channel and a backup directory."""
    shown: list[dict[str, Any]] = []
    selected = 0
    writer: csv.DictWriter[str] | None = None
    handle: TextIO | None = None

    def link_of(message_id: int) -> str | None:
        return engine.message_link(source.id, source.username, message_id)

    if job.csv_path is not None:
        handle = await asyncio.to_thread(_open_csv, job.csv_path)
        writer = csv.DictWriter(handle, fieldnames=engine.LIST_COLUMNS)
        writer.writeheader()
    want_rows = job.list_messages or job.as_json

    preflight = None
    if job.plan is not None:
        options = replace(
            job.plan, by_reference=not source.protected, topic_titles=source.topic_titles
        )
        preflight = Preflight(options, job.limits)

    chosen_ids: list[int] = []

    def on_selected(unit: Unit) -> None:
        nonlocal selected
        chosen_ids.extend(unit.ids)
        if preflight is not None:
            preflight.add(unit)
        for m in unit.messages:
            selected += 1
            if writer is None and not want_rows:
                continue
            row = engine.describe_message(m, link=link_of(m.id))
            if writer is not None:
                writer.writerow({k: "" if v is None else v for k, v in row.items()})
            if want_rows and (job.limit == 0 or len(shown) < job.limit or job.as_json):
                shown.append(row)

    inspector = engine.Inspector(
        None if job.filters is None or job.filters.is_empty else Matcher(job.filters),
        job.by,
        forum=source.forum,
        topic_titles=source.topic_titles,
        on_selected=on_selected,
    )

    last_line = [time.monotonic()]  # the first line comes after one interval, not at once
    emit: Callable[[str], None] = lambda text: typer.echo(text, err=True)  # noqa: E731

    def progress(state: engine.Inspector) -> None:
        now = time.monotonic()
        if now - last_line[0] >= PROGRESS_INTERVAL:
            last_line[0] = now
            emit(
                t("inspect.progress", messages=f"{state.scanned_messages:,}", last_id=state.last_id)
            )

    complete = True
    flood: FloodWait | None = None
    interrupted = False
    index_line = [time.monotonic()]

    def indexing_progress(read: int, last_id: int) -> None:
        now = time.monotonic()
        if now - index_line[0] >= PROGRESS_INTERVAL:
            index_line[0] = now
            emit(t("inspect.indexing", messages=f"{read:,}", last_id=last_id))

    async def bring_up_to_date() -> bool:
        """Read what the index does not have yet. A stop or a FloodWait too long to sit out ends
        it early; what was read is kept and the report is made from that."""
        nonlocal flood
        feed = source.feed
        if feed is None or feed.live is None:
            return True
        try:
            updated = await indexing.update(
                feed.index,
                feed.live,
                feed.source,
                refresh=job.refresh,
                stop=lambda: control.stop_requested,
                on_progress=indexing_progress,
            )
        except Interrupted:
            return False
        except FloodWait as exc:
            flood = exc
            return False
        return updated.complete

    try:
        with stop_on_interrupt(control, lambda: emit(t("inspect.stopping"))) as interrupt:
            try:
                fresh = await bring_up_to_date()
                # the index is local: a stop that ended the update must not also cut the report
                local = source.feed is not None
                scanned = await engine.scan(
                    source.reader,
                    source.id,
                    inspector,
                    stop=(lambda: False) if local else (lambda: control.stop_requested),
                    on_progress=progress,
                )
                complete = fresh and scanned
            except Interrupted:
                complete = False
            except FloodWait as exc:  # a wait too long to sit out: show what was read, then fail
                complete, flood = False, exc
            interrupted = interrupt.hit
    finally:
        if handle is not None:
            handle.close()

    report = inspector.report(top=job.top, complete=complete)
    spec = job.filters if job.filters is not None else FilterSpec()
    saved: list[str] = []
    if complete and flood is None and not interrupted:  # a partial scan is no selection
        saved = await asyncio.to_thread(_write_selections, source, job, spec, chosen_ids, report)
    next_commands = _next_commands(source, spec, job)
    state = await indexing.info(source.feed.index) if source.feed is not None else None
    if job.as_json:
        data = engine.report_to_dict(report)
        data["source"] = {"id": source.id, "label": source.label}
        if state is not None:
            data["index"] = {
                "messages": state.messages,
                "last_id": state.last_id,
                "updated_at": state.updated_at.isoformat(timespec="seconds")
                if state.updated_at
                else None,
                "complete": state.complete,
                "offline": job.offline,
            }
        data["messages"] = shown
        if preflight is not None:
            data["plan"] = plan_to_dict(preflight.plan())
        data["next_commands"] = next_commands
        typer.echo(_dumps(data))
    else:
        console = Console()
        print_report(console, report, source=source.label, forum=source.forum)
        if state is not None:
            console.print(_index_line(state, offline=job.offline))
        if job.list_messages:
            print_listing(console, shown, more=max(selected - len(shown), 0))
        if preflight is not None and job.plan is not None:
            print_plan(
                console,
                preflight.plan(),
                job.plan,
                batch_size=job.limits.batch_size,
                protected=source.protected,
            )
        for line in saved:
            console.print(line)
        console.print()
        console.print(t("inspect.next_title"))
        for line in next_commands:
            console.print(Text("  " + line))
    if job.csv_path is not None:
        typer.echo(t("inspect.csv_written", count=f"{selected:,}", path=job.csv_path), err=True)
    if flood is not None:
        raise flood
    if interrupted or not complete:
        raise typer.Exit(EXIT_INTERRUPTED)


def _index_line(state: indexing.IndexInfo, *, offline: bool) -> str:
    """What the numbers above rest on: the local index and when it was last brought up to date."""
    when = (
        state.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
        if state.updated_at is not None
        else "?"
    )
    key = "inspect.index_offline" if offline else "inspect.index_state"
    line = t(key, messages=f"{state.messages:,}", when=when)
    return line if state.complete else line + " " + t("inspect.index_partial")


def _write_selections(
    source: Source, job: _Job, spec: FilterSpec, ids: list[int], report: engine.Report
) -> list[str]:
    """``@last`` (always, with the ids: ``--exact`` on it must work) and ``--save`` (with the ids
    only when frozen). Returns the lines that say what was written."""
    now = datetime.now(UTC)
    origin = selection.SelectionSource(
        id=source.id, title=source.title or None, backup=source.backup
    )
    everything = selection.Snapshot(
        now, report.last_id, report.selected.messages, tuple(sorted(ids))
    )
    selection.dump(
        selection.Selection(spec, origin, everything),
        selection.last_path(job.selections_dir, source.id),
        now=now,
    )
    lines: list[str] = []
    if job.save is not None:
        snapshot = everything if job.freeze else None
        carried = job.plan.transform if job.plan is not None else Transform()
        selection.dump(selection.Selection(spec, origin, snapshot, carried), job.save, now=now)
        if snapshot is not None and ids:
            lines.append(
                t(
                    "inspect.saved_ids",
                    path=job.save,
                    count=f"{len(ids):,}",
                    ids=job.save.with_suffix(selection.IDS_SUFFIX).name,
                )
            )
        else:
            lines.append(t("inspect.saved", path=job.save))
    return lines


def _next_commands(source: Source, spec: FilterSpec, job: _Job) -> list[str]:
    """The commands that use exactly this filter next (docs/07, "Inspect in sẵn lệnh kế tiếp"):
    the flags when the filter can be said with flags, else the selection file."""
    flags = selection.to_flags(spec)
    if flags is not None:
        tail = [selection.quote(token) for token in flags]
    else:
        ref = str(job.save) if job.save is not None else selection.LAST
        tail = ["--selection", selection.quote(ref)]
    dst = ["--dst-new", '"..."']
    if source.backup is not None:
        head = ["tgmirror", "restore", selection.quote(source.backup)]
        return [" ".join([*head, *tail, *dst])]
    sid = str(source.id)
    return [
        " ".join(["tgmirror", "clone", "--src", sid, *tail, *dst]),
        " ".join(["tgmirror", "backup", sid, "./backup", *tail]),
    ]


def _open_csv(path: Path) -> TextIO:
    return path.open("w", encoding="utf-8-sig", newline="")


def _dumps(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
