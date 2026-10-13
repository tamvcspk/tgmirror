"""``--transform`` of ``clone``/``restore`` (phase 19, docs/07-inspect-transform.md): declared
once, like ``filter_options`` and ``selection_options``, so the commands that apply a transform
offer the same flag and the same rules.

The transform can also arrive in the ``transform:`` section of a ``--selection`` file. Both at
once is a conflict, not a merge (two files, no way to say which is the base). The ``topics`` flags
(``--hashtag-to-topic``, ``--topic-map``, ``--topic-default``, ``--strip-routing-hashtag``) are
not a second file: they go on top of whichever one there is (or of nothing).
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


@dataclass(frozen=True, slots=True)
class TopicFlags:
    """The ``topics`` flags of one command line, gathered so a command passes them on as one
    value. All off by default, which leaves a transform exactly as its file wrote it."""

    hashtag_to_topic: bool = False
    topic_map: tuple[str, ...] = ()
    topic_default: str | None = None
    strip_routing_hashtag: bool = False


def topic_flags(
    *,
    hashtag_to_topic: bool = False,
    topic_map: list[str] | None = None,
    topic_default: str | None = None,
    strip_routing_hashtag: bool = False,
) -> TopicFlags:
    return TopicFlags(
        hashtag_to_topic, tuple(topic_map or ()), topic_default, strip_routing_hashtag
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


def with_topic_flags(base: Transform, flags: TopicFlags) -> Transform:
    """``base`` with the ``topics`` flags on top: ``--hashtag-to-topic`` switches ``auto: all``
    on, ``--topic-map`` adds renames, ``--topic-default`` and ``--strip-routing-hashtag`` set
    their part of the routing (so they need routing, from the file or ``--hashtag-to-topic``).
    Raises ``TransformError``."""
    rules = base.topics
    if flags.hashtag_to_topic and not rules.auto_all:
        rules = replace(rules, auto_all=True)
    rules = rules.layered(
        renames=_renames_of(flags.topic_map),
        default=flags.topic_default,
        strip=flags.strip_routing_hashtag,
    )
    return base if rules == base.topics else replace(base, topics=rules)


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
            "rule or name) — a key left with nothing under it is the usual cause"
        )
    return loaded
