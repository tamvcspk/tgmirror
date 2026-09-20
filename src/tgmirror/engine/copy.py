"""Strategy A: server-side copy (forward without author). No download, no upload."""

from tgmirror.core.gateway import TelegramGateway
from tgmirror.engine.batcher import Batch
from tgmirror.store.msgmap import MessageResult

NOT_COPIED = "not_copied"  # Telegram made no message for this id: deleted at the source, say


async def copy_batch(
    gateway: TelegramGateway, src: int, dst: int, batch: Batch
) -> list[MessageResult]:
    """One ``copy_messages`` call for the whole batch; one result per source message.

    ``PerMessage`` (Telegram refused the ids, nothing created) and every other gateway error
    propagate: the runner decides what they mean for the run.
    """
    ids = batch.ids
    copied = await gateway.copy_messages(src, dst, ids)
    if len(copied) != len(ids):
        raise ValueError(f"copy_messages returned {len(copied)} results for {len(ids)} ids")
    return [
        MessageResult(src_id, dst_id)
        if dst_id is not None
        else MessageResult(src_id, None, NOT_COPIED)
        for src_id, dst_id in zip(ids, copied, strict=True)
    ]
