"""SQL for ``topic_map``: a forum pair's source topic id -> destination topic id (phase 8).

General (source topic id 1) is never a row here: it is hard-mapped to destination topic 1
(``engine/topics.py``), the same on every forum, so nothing needs to be looked up or created for it.
"""

import aiosqlite


async def get(db: aiosqlite.Connection, mirror_id: int, src_topic_id: int) -> int | None:
    cur = await db.execute(
        "SELECT dst_topic_id FROM topic_map WHERE mirror_id = ? AND src_topic_id = ?",
        (mirror_id, src_topic_id),
    )
    row = await cur.fetchone()
    return None if row is None else int(row[0])


async def save(
    db: aiosqlite.Connection, mirror_id: int, src_topic_id: int, dst_topic_id: int, title: str
) -> None:
    await db.execute(
        "INSERT INTO topic_map(mirror_id, src_topic_id, dst_topic_id, title) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(mirror_id, src_topic_id) DO UPDATE SET "
        "  dst_topic_id = excluded.dst_topic_id, title = excluded.title",
        (mirror_id, src_topic_id, dst_topic_id, title),
    )


async def all_of(db: aiosqlite.Connection, mirror_id: int) -> dict[int, int]:
    cur = await db.execute(
        "SELECT src_topic_id, dst_topic_id FROM topic_map WHERE mirror_id = ?", (mirror_id,)
    )
    return {int(r[0]): int(r[1]) for r in await cur.fetchall()}
