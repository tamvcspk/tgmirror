"""``BackupReader``: a ``MessageReader`` over a backup directory (phase 11b, ``tgmirror restore``).

Everything a restore's ``Runner`` needs to read is already on disk (``engine/backupdir.py``'s
``messages.jsonl`` + ``media/``), so there is nothing to pace or retry here (hard rule 1 has
nothing to apply to: there is no Telegram call on this side of a restore at all). The whole
directory is loaded once into memory — small next to a real Telegram read, and consistent with how
``backupdir.iter_records``/``last_id`` already work.

Pure engine code (no Telethon import, hard rule 8): the file each unit will be sent from is handed
to ``core.telethon_gateway`` as a ``FromBackup`` handle, described the same neutral way
``messages.jsonl`` already describes it, rather than as a live Telethon message.
"""

import re
from collections.abc import AsyncIterator, Sequence
from html import unescape
from pathlib import Path

from tgmirror.core.gateway import (
    NO_FILTER,
    ExportedMessage,
    FromBackup,
    MediaKind,
    OnTransfer,
    Prepared,
    ServerFilter,
    SrcMessage,
    TopicInfo,
    Unit,
)
from tgmirror.engine import backupdir

_TAG = re.compile(r"<[^>]+>")
_HASHTAG = re.compile(r"#(\w+)")


def _plain_text(text_html: str) -> str:
    """An approximation of the original plain text, for filtering only (decision 9, phase 11b):
    a backup keeps Telethon's HTML, not the plain text/entities a live read gives ``SrcMessage``.
    """
    return unescape(_TAG.sub("", text_html))


def _hashtags(text: str) -> tuple[str, ...]:
    """Hashtags found in the plain text (decision 9, phase 11b): live ``SrcMessage.hashtags``
    comes from Telegram's own entities, which a backup does not keep separately; a restored
    source's hashtag filter is therefore an approximation, not exact."""
    return tuple(f"#{m.casefold()}" for m in _HASHTAG.findall(text))


def _src_message(message: ExportedMessage) -> SrcMessage:
    media = message.media
    text = _plain_text(message.text_html)
    title = None
    quiz_unanswered = False
    if media is not None:
        title = media.poll_question if media.kind is MediaKind.POLL else media.title
        # A quiz needs a known correct answer to be reconstructed at all (Telegram requires one);
        # a backup only has one when this account had already seen it (``poll_correct_option``).
        # A plain poll's ``poll_correct_option`` (which option this account picked) is not needed
        # to resend it, so it never blocks restoring one.
        quiz_unanswered = (
            media.kind is MediaKind.POLL and media.poll_quiz and (media.poll_correct_option is None)
        )
    return SrcMessage(
        id=message.id,
        date=message.date,
        text=text,
        media=media.kind if media is not None else MediaKind.TEXT,
        grouped_id=message.grouped_id,
        is_service=False,  # a backup never records service messages (planner drops them first)
        hashtags=_hashtags(text),
        size=media.size if media is not None else None,
        duration=media.duration if media is not None else None,
        mime=media.mime if media is not None else None,
        views=message.views,
        quiz_unanswered=quiz_unanswered,
        title=title,
        topic_id=message.topic_id,
        from_user_id=message.from_user_id,
    )


def _matches(message: ExportedMessage, *, min_id: int, filters: ServerFilter) -> bool:
    """The narrowing ``BackupReader`` actually does: id/date bounds only, never ``media``/
    ``search`` (decision 8, phase 11b) — a safe superset per ``ServerFilter``'s own contract, left
    for the client-side ``Matcher`` to finish, since there is no paging or album-margin heuristic
    to earn here (every message is already local)."""
    if message.id <= min_id:
        return False
    if filters.max_id is not None and message.id > filters.max_id:
        return False
    if filters.since is not None and message.date < filters.since:
        return False
    return not (filters.until is not None and message.date >= filters.until)


class BackupReader:
    """Reads a backup directory as if it were a source channel, for ``tgmirror restore``.

    ``src`` arguments are ignored beyond identifying the pair (a ``BackupReader`` only ever knows
    one directory); callers pass ``manifest.src_id`` by convention (``engine/runs.py``).
    """

    def __init__(self, directory: Path, manifest: backupdir.BackupManifest) -> None:
        self._media_dir = backupdir.media_dir(directory)
        self._manifest = manifest
        records = backupdir.iter_records(directory)
        self._records = {r.id: r for r in records}
        self._ordered = tuple(sorted(self._records))

    async def iter_messages(
        self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER
    ) -> AsyncIterator[SrcMessage]:
        for msg_id in self._ordered:
            record = self._records[msg_id]
            if _matches(record, min_id=min_id, filters=filters):
                yield _src_message(record)

    async def count(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int:
        return sum(
            1 for i in self._ordered if _matches(self._records[i], min_id=min_id, filters=filters)
        )

    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]:
        found = sorted(i for i in ids if i in self._records)
        return [_src_message(self._records[i]) for i in found]

    async def list_topics(self, src: int) -> list[TopicInfo]:
        return [TopicInfo(t.id, t.title) for t in self._manifest.topics]

    async def fetch(self, src: int, unit: Unit) -> Prepared:
        return self._prepared(unit)

    async def prepare(
        self, src: int, unit: Unit, tmp: Path, on_transfer: OnTransfer | None = None
    ) -> Prepared:
        return self._prepared(unit)

    async def export_unit(
        self, src: int, unit: Unit, media_dir: Path, on_transfer: OnTransfer | None = None
    ) -> list[ExportedMessage]:
        raise NotImplementedError("a restore never backs up: BackupReader is read-only")

    def _prepared(self, unit: Unit) -> Prepared:
        """Both ``fetch`` and ``prepare`` are the same for a backup: nothing is downloaded, since
        the files are already on disk, and ``files=()`` means nothing is ever deleted by the
        engine's cleanup (``engine/reupload.py``'s ``Pipeline.finish``/``send_unit_by_reference``
        only ever unlink what is listed there — decision 1, phase 11b)."""
        exported = tuple(self._records[m.id] for m in unit.messages)
        handle = FromBackup(exported, self._media_dir, self._manifest.src_id)
        return Prepared(unit, files=(), handle=handle)
