"""``--transform`` of ``clone``/``restore`` (phase 19, docs/07-inspect-transform.md): declared
once, like ``filter_options`` and ``selection_options``, so the commands that apply a transform
offer the same flag and the same rules.

The transform can also arrive in the ``transform:`` section of a ``--selection`` file. Both at
once is a conflict, not a merge (two files, no way to say which is the base). The ``topics`` flags
(``--hashtag-to-topic``, ``--topic-map``, ``--topic-default``, ``--strip-routing-hashtag``,
and the ones that tune hashtag routing: ``--topic-skip``, ``--topic-regex``, ``--topic-pick``,
``--topic-limit``) are
not a second file: they go on top of whichever one there is (or of nothing), and so do
``--keep-replies``, ``--keep-forward-header`` and ``--rewrite-links``.
"""

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated

import typer

from tgmirror.cli.errors import UsageProblem
from tgmirror.engine import transform as tf
from tgmirror.engine.transform import Transform, TransformError

TransformOption = Annotated[
    Path | None,
    typer.Option(
        "--transform",
        help="A transform file: a list of operations applied to captions (strip_links, "
        "remove_hashtags, remove_mentions, replace, append, ...; docs/07-inspect-transform.md). "
        "The messages it changes are sent again (by file id when the source allows saving "
        "content, otherwise downloaded and re-uploaded). A --caption flag runs after the file's "
        "operations.",
        rich_help_panel="Transform",
    ),
]


HashtagToTopicOption = Annotated[
    bool,
    typer.Option(
        "--hashtag-to-topic",
        help="Send each message to a forum topic named after its first hashtag (created as it is "
        "met, at most 30 new ones per run; a message without a hashtag goes to General). The "
        "destination must be a forum: --dst-new makes one. Add rules, names and limits with "
        "`topics:` in a --transform file; the flag then fills in the hashtags the file leaves "
        "out.",
        rich_help_panel="Transform",
    ),
]

TopicMapOption = Annotated[
    list[str] | None,
    typer.Option(
        "--topic-map",
        help='Rename a source topic in the destination: "Old name=New name" (or a topic id: '
        '"12=New name"); several source topics given one name are merged. Repeat for more. '
        "Adds to the `topic_map` of a --transform file and wins for a topic both name. It "
        "renames, it does not select: use --topic to take only some topics.",
        rich_help_panel="Transform",
    ),
]

TopicDefaultOption = Annotated[
    str | None,
    typer.Option(
        "--topic-default",
        metavar="NAME",
        help="With hashtag routing: the topic for a message no hashtag rule matched and that "
        "sits in no source topic (without it: General). Replaces `default` of a --transform "
        "file.",
        rich_help_panel="Transform",
    ),
]

StripRoutingHashtagOption = Annotated[
    bool,
    typer.Option(
        "--strip-routing-hashtag",
        help="With hashtag routing: take the hashtag that routed a message out of its caption. "
        "Those messages are sent again (by file id, or downloaded and re-uploaded when the "
        "source restricts saving content) instead of forwarded.",
        rich_help_panel="Transform",
    ),
]


TopicSkipOption = Annotated[
    list[str] | None,
    typer.Option(
        "--topic-skip",
        metavar="#TAG",
        help="With hashtag routing: a hashtag that names no topic (an advertiser's, say). Repeat "
        "for more. Adds to the `skip` of the file's rule `from: hashtags, to: "
        '"{value|tag_title}"`.',
        rich_help_panel="Transform",
    ),
]

TopicRegexOption = Annotated[
    str | None,
    typer.Option(
        "--topic-regex",
        metavar="REGEX",
        help="With hashtag routing: only a hashtag the pattern finds names a topic (write it for "
        "the hashtag in lower case, with its #: '^#(?!yy)' leaves out those starting yy). "
        "Replaces the `regex` of the file's hashtag rule.",
        rich_help_panel="Transform",
    ),
]

TopicPickOption = Annotated[
    str | None,
    typer.Option(
        "--topic-pick",
        metavar="first|last",
        help="With hashtag routing, when a message has several hashtags: the one that names its "
        "topic (default first). Replaces the `pick` of the file's hashtag rule.",
        rich_help_panel="Transform",
    ),
]

TopicLimitOption = Annotated[
    int | None,
    typer.Option(
        "--topic-limit",
        min=1,
        max=1000,
        help="At most this many new topics are created in one run by a rule whose name comes from "
        "the message (default 30). Replaces `limit` of a --transform file.",
        rich_help_panel="Transform",
    ),
]


KeepRepliesOption = Annotated[
    bool,
    typer.Option(
        "--keep-replies",
        help="A message that answers another one answers the copy of it in the destination. "
        "Those messages are sent again (by file id, or downloaded and re-uploaded when the "
        "source restricts saving content) instead of forwarded, since a forward carries no "
        "reply. One whose original was not copied is sent without its reply.",
        rich_help_panel="Transform",
    ),
]


KeepForwardHeaderOption = Annotated[
    bool,
    typer.Option(
        "--keep-forward-header",
        help='Forward with the "Forwarded from" header on, as a plain forward does (default: '
        "without it). Only on what is forwarded, so it does not go with --mode reupload, "
        "--caption, --keep-replies or --strip-routing-hashtag: those send a message again, "
        "which has no header.",
        rich_help_panel="Transform",
    ),
]


RewriteLinksOption = Annotated[
    bool,
    typer.Option(
        "--rewrite-links",
        help="A link to a message of the source (t.me/<source>/<id>) becomes a link to its copy in "
        "the destination. One whose message has no copy yet stays as it is, and the result line "
        "counts those. The messages carrying such a link are sent again (by file id, or "
        "downloaded and re-uploaded when the source restricts saving content).",
        rich_help_panel="Transform",
    ),
]


@dataclass(frozen=True, slots=True)
class TopicFlags:
    """The flags of one command line that go on top of a transform, gathered so a command passes
    them on as one value (named for the ``topics`` flags it began with; ``keep_replies`` is the
    first that is not about topics). All off by default, which leaves a transform exactly as its
    file wrote it."""

    hashtag_to_topic: bool = False
    topic_map: tuple[str, ...] = ()
    topic_default: str | None = None
    strip_routing_hashtag: bool = False
    keep_replies: bool = False
    keep_forward_header: bool = False
    rewrite_links: bool = False
    topic_skip: tuple[str, ...] = ()
    topic_regex: str | None = None
    topic_pick: str | None = None
    topic_limit: int | None = None


def topic_flags(
    *,
    hashtag_to_topic: bool = False,
    topic_map: list[str] | None = None,
    topic_default: str | None = None,
    strip_routing_hashtag: bool = False,
    keep_replies: bool = False,
    keep_forward_header: bool = False,
    rewrite_links: bool = False,
    topic_skip: list[str] | None = None,
    topic_regex: str | None = None,
    topic_pick: str | None = None,
    topic_limit: int | None = None,
) -> TopicFlags:
    return TopicFlags(
        hashtag_to_topic,
        tuple(topic_map or ()),
        topic_default,
        strip_routing_hashtag,
        keep_replies,
        keep_forward_header,
        rewrite_links,
        tuple(topic_skip or ()),
        topic_regex,
        topic_pick,
        topic_limit,
    )


async def resolve(
    flag: Path | None,
    from_selection: Transform | None = None,
    *,
    topics: TopicFlags | None = None,
) -> Transform:
    """The transform to use: the ``--transform`` file, or the one a selection file carried, with
    the ``topics`` flags on top of either. Reads a file, so it is awaited like the other flag
    parsing steps that touch the disk."""
    given = from_selection is not None and not from_selection.is_empty
    if flag is None:
        loaded = from_selection if from_selection is not None else Transform()
    elif given:
        raise UsageProblem(
            "err.conflicting_flags", flags="--transform / --selection (its transform)"
        )
    else:
        loaded = await asyncio.to_thread(_load, flag)
    return with_topic_flags(loaded, topics or TopicFlags())


async def resolve_typed(text: str) -> Transform:
    """The transform file a person typed at a prompt (the full-screen menu has no flag to give)."""
    path = await asyncio.to_thread(lambda: Path(text.strip()).expanduser())
    return await resolve(path)


def with_topic_flags(base: Transform, flags: TopicFlags) -> Transform:
    """``base`` with the ``topics`` flags on top: ``--hashtag-to-topic`` adds the rule ``auto:
    all`` stands for, ``--topic-map`` adds renames, ``--topic-default`` and
    ``--strip-routing-hashtag`` set their part of the routing (so they need routing, from the
    file or ``--hashtag-to-topic``); ``--topic-skip``, ``--topic-regex`` and ``--topic-pick`` tune
    the hashtag rule that ``--hashtag-to-topic`` adds or the file already has, and
    ``--topic-limit`` replaces the limit. Raises ``TransformError``."""
    rules = base.topics
    tuned = bool(flags.topic_skip or flags.topic_regex is not None or flags.topic_pick)
    if flags.hashtag_to_topic or tuned:
        rules = rules.with_auto_all(
            skip=flags.topic_skip,
            pattern=flags.topic_regex,
            pick=flags.topic_pick,
            add=flags.hashtag_to_topic,
        )
    rules = rules.layered(
        renames=_renames_of(flags.topic_map),
        default=flags.topic_default,
        strip=flags.strip_routing_hashtag,
        limit=flags.topic_limit,
    )
    changed = base if rules == base.topics else replace(base, topics=rules)
    if flags.keep_replies:
        changed = replace(changed, keep_replies=True)
    if flags.keep_forward_header:
        changed = replace(changed, keep_forward_header=True)
    if flags.rewrite_links:
        changed = replace(changed, rewrite_links=True)
    return changed


def _renames_of(items: tuple[str, ...]) -> list[tuple[int | str, str]]:
    """``"Old name=New name"`` -> (title, name); ``"12=New name"`` -> (id, name). Split at the
    first ``=``: a title with one in it goes in a transform file instead."""
    renames: list[tuple[int | str, str]] = []
    for item in items:
        old, sign, new = item.partition("=")
        old, new = old.strip(), new.strip()
        if not sign or not old or not new:
            raise TransformError(f'--topic-map {item!r}: write it as "Old name=New name"')
        renames.append((int(old) if old.isdecimal() else old, new))
    return renames


def _load(path: Path) -> Transform:
    loaded = tf.load(path.expanduser())
    if loaded.is_empty:  # a file given by name that does nothing is a mistake, not a no-op
        raise TransformError(
            f"{path}: nothing in it changes a message (no `caption:` operation, no `topics:` "
            "rule or name, no `reply: keep`, no `forward_header: keep`, no `links: rewrite`) — "
            "a key left with nothing under it is the usual cause"
        )
    return loaded
