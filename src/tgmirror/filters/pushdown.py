"""Turn the part of a filter Telegram can evaluate into a read plan (docs/03-filters.md).

Pushdown is an optimisation, never the source of truth: it may only *narrow safely* (return a
superset of the matches) and the client matcher always runs again on the result. What it must never
do is split an album (hard rule 4), which is why id bounds get a margin and why content narrowing
(``media``/``search``) asks the planner to complete the albums it returns.
"""

from dataclasses import dataclass

from tgmirror.core.gateway import ALBUM_MARGIN, NO_FILTER, MediaKind, ServerFilter
from tgmirror.filters.model import FilterSpec

# Media kinds whose Telegram filter is a superset of our own classification. ``document`` is left
# out on purpose: our DOCUMENT also holds attachments that are no document for Telegram (dice,
# stories, ...), so filtering them away would lose matches.
PUSHABLE_MEDIA = frozenset(
    {
        MediaKind.PHOTO,
        MediaKind.VIDEO,
        MediaKind.AUDIO,
        MediaKind.VOICE,
        MediaKind.GIF,
        MediaKind.VIDEO_NOTE,
    }
)


@dataclass(frozen=True, slots=True)
class ReadPlan:
    min_id: int  # read messages with id > min_id
    server: ServerFilter
    complete_albums: bool  # the server dropped album members: read the rest of each album


def plan_read(
    spec: FilterSpec, cursor: int, *, pushdown: bool = True, content: bool = True
) -> ReadPlan:
    """How to read the source for ``spec``, after ``cursor``.

    ``pushdown=False`` reads everything after the cursor (a full scan, for comparing or debugging).
    ``content=False`` keeps only the range bounds (ids, dates): a preview wants an unbiased sample.
    """
    if not pushdown:
        return ReadPlan(cursor, NO_FILTER, complete_albums=False)

    min_id, max_id = cursor, None
    if spec.ids is not None:
        if spec.ids.from_ is not None:
            min_id = max(cursor, spec.ids.from_ - 1 - ALBUM_MARGIN)
        if spec.ids.to is not None:
            max_id = spec.ids.to + ALBUM_MARGIN
    since = spec.when.from_ if spec.when else None
    until = spec.when.to if spec.when else None

    media = search = None
    if content and len(spec.include) == 1:  # with several rules (OR) nothing can be pushed down
        rule = spec.include[0]
        if rule.media is not None and len(rule.media) == 1 and rule.media[0] in PUSHABLE_MEDIA:
            media = rule.media[0]
        # One hashtag is a word for Telegram's search. ``contains`` is not pushed: it means
        # "substring" for us, while the search matches words.
        if rule.hashtag is not None and len(rule.hashtag) == 1:
            search = rule.hashtag[0]

    server = ServerFilter(media=media, search=search, since=since, until=until, max_id=max_id)
    return ReadPlan(min_id, server, complete_albums=media is not None or search is not None)
