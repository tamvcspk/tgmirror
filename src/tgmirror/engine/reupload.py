"""Strategy B: download a unit's media and send it again (docs/01-kien-truc.md).

Slower than a forward, but the only way to change a caption, and the way out when the source
restricts saving content and the user administers it (decision D3). Three parts:

- ``plan_unit`` decides what happens to a unit *before* Telegram is called: send it, leave it out
  (a poll without ``--reset-polls``, or something that cannot be copied and the user said so), or
  stand a text in for it (``--placeholder``). What cannot be copied and the user did not ask to
  skip stops the run: nothing is ever left out silently.
- ``send_unit`` is the write: one call to the gateway per unit, with the result per message.
- ``Pipeline`` downloads the next unit while the current one uploads, inside a ``Window`` that
  bounds how many units and how many bytes sit on disk at once.

Only the download half (``prepare``) runs ahead; sending stays sequential and in order (D4), one
unit at a time on one account.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from tgmirror.core.gateway import (
    CaptionMode,
    CaptionPolicy,
    MediaKind,
    Prepared,
    TelegramGateway,
    Unit,
)
from tgmirror.engine.batcher import Batch
from tgmirror.engine.runs import RunError
from tgmirror.store.msgmap import MessageResult
from tgmirror.store.runs import RunOptions

UNSUPPORTED = "unsupported"  # prefix of ``msg_map.reason`` for what cannot be copied

_KIND_LABEL = {"game": "Game", "invoice": "Hóa đơn", "quiz": "Quiz", "poll": "Poll"}


class UnsupportedMedia(RunError):
    """A message strategy B cannot copy, met without ``--ignore-unsupported``/``--placeholder``."""

    def __init__(self, kind: str, msg_id: int) -> None:
        super().__init__(f"message {msg_id} is a {kind}, which cannot be copied")
        self.kind = kind
        self.msg_id = msg_id


@dataclass(frozen=True, slots=True)
class Options:
    """The parts of a run's options strategy B looks at."""

    caption: CaptionPolicy = CaptionPolicy()
    reset_polls: bool = False
    ignore_unsupported: bool = False
    placeholder: bool = False

    @classmethod
    def of(cls, options: RunOptions) -> "Options":
        return cls(
            CaptionPolicy(CaptionMode(options.caption), options.caption_text),
            options.reset_polls,
            options.ignore_unsupported,
            options.placeholder,
        )


class ActionKind(StrEnum):
    SEND = "send"
    DROP = "drop"  # leave it out: no Telegram call at all
    PLACEHOLDER = "placeholder"  # leave it out, but post a text where it was


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    reason: str | None = None  # ``unsupported:<kind>`` for the two that leave the unit out
    text: str = ""  # the placeholder to post


SEND = Action(ActionKind.SEND)


def placeholder_text(kind: str, title: str | None) -> str:
    label = _KIND_LABEL.get(kind, kind.capitalize())
    what = f"{label}: {title}" if title else label
    return f"[{what} — không thể sao chép]"


def plan_unit(unit: Unit, options: Options) -> Action:
    """What to do with ``unit``. Raises ``UnsupportedMedia`` for something that cannot be copied
    when the run was not told to leave such things out.

    A poll (or quiz) is re-created only with ``--reset-polls``, because it comes back with no votes;
    without it the poll is left out with a warning, which is the user's choice, so it never gets a
    placeholder. A quiz whose right answer this account cannot see, a game and an invoice cannot be
    copied at all.
    """
    first = unit.messages[0]  # none of these can be part of an album
    if first.media is MediaKind.POLL:
        kind = "quiz" if first.quiz_unanswered else "poll"
        if not options.reset_polls:
            return Action(ActionKind.DROP, f"{UNSUPPORTED}:{kind}")
        if first.quiz_unanswered:
            return _cannot(kind, first.id, first.title, options)
        return SEND
    if first.media in (MediaKind.GAME, MediaKind.INVOICE):
        return _cannot(first.media.value, first.id, first.title, options)
    return SEND


def _cannot(kind: str, msg_id: int, title: str | None, options: Options) -> Action:
    reason = f"{UNSUPPORTED}:{kind}"
    if options.placeholder:
        return Action(ActionKind.PLACEHOLDER, reason, placeholder_text(kind, title))
    if options.ignore_unsupported:
        return Action(ActionKind.DROP, reason)
    raise UnsupportedMedia(kind, msg_id)


def left_out(unit: Unit, action: Action, dst_id: int | None = None) -> list[MessageResult]:
    """The results of a unit that was not copied on purpose."""
    assert action.reason is not None
    return [MessageResult(m.id, dst_id, action.reason, skipped=True) for m in unit.messages]


async def send_unit(
    gateway: TelegramGateway,
    dst: int,
    unit: Unit,
    prepared: Prepared | None,
    action: Action,
    options: Options,
) -> list[MessageResult]:
    """Post the unit (or its placeholder) and say what became of each message.

    Errors from the gateway propagate; the runner decides what they mean for the run.
    """
    if action.kind is ActionKind.PLACEHOLDER:
        return left_out(unit, action, await gateway.send_text(dst, action.text))
    assert action.kind is ActionKind.SEND and prepared is not None
    sent = await gateway.send_prepared(dst, prepared, options.caption)
    if len(sent) != len(unit.messages):
        raise ValueError(f"send_prepared gave {len(sent)} ids for {len(unit.messages)} messages")
    return [MessageResult(m.id, new) for m, new in zip(unit.messages, sent, strict=True)]


# ---- downloading ahead --------------------------------------------------------------------------


class Window:
    """How much strategy B may have on disk at once: ``units`` units and ``max_bytes`` bytes.

    A unit is reserved before it is downloaded and released once it has been sent (or given up).
    A unit that alone is bigger than the budget is still let through when nothing else is on disk,
    so a big video is never stuck forever.
    """

    def __init__(self, units: int, max_bytes: int) -> None:
        self._units = units
        self._max_bytes = max_bytes
        self._used_units = 0
        self._used_bytes = 0
        self._cond = asyncio.Condition()

    async def reserve(self, size: int) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: self._fits(size))
            self._used_units += 1
            self._used_bytes += size

    async def release(self, size: int) -> None:
        async with self._cond:
            self._used_units -= 1
            self._used_bytes -= size
            self._cond.notify_all()

    def _fits(self, size: int) -> bool:
        if self._used_units == 0:
            return True
        return self._used_units < self._units and self._used_bytes + size <= self._max_bytes


@dataclass(slots=True)
class Ready:
    """A batch ready to be sent, with everything strategy B fetched for it."""

    batch: Batch
    action: Action | None = None  # strategy B only
    prepared: Prepared | None = None
    rejected: str | None = None  # Telegram refused the fetch (``PerMessage``): the unit failed
    reserved: int | None = None  # bytes held in the window, ``None`` when nothing is


class _End:
    pass


@dataclass(frozen=True, slots=True)
class _Failure:
    error: Exception


class Pipeline:
    """Runs ``make`` on the next batch in the background while the caller sends the current one.

    ``make`` turns a batch into a ``Ready`` (it reserves its room in the window and downloads).
    The caller must ``finish`` every ``Ready`` it gets, whatever happened to it. An error in the
    background is handed over in order, after the batches that came before it.
    """

    def __init__(
        self,
        window: Window,
        make: Callable[[Batch], Awaitable[Ready]],
        *,
        poll_interval: float = 2.0,
    ) -> None:
        self._window = window
        self._make = make
        self._poll = poll_interval

    async def stream(
        self, batches: AsyncIterator[Batch], idle: Callable[[], Awaitable[None]]
    ) -> AsyncIterator[Ready]:
        """The ``Ready``s in order. While waiting for one, ``idle`` is awaited every
        ``poll_interval`` seconds; it raises to end the wait (a stop request)."""
        queue: asyncio.Queue[Ready | _Failure | _End] = asyncio.Queue(maxsize=1)
        producer = asyncio.create_task(self._produce(batches, queue))
        try:
            while True:
                item = await self._next(queue, producer, idle)
                if isinstance(item, _End):
                    return
                if isinstance(item, _Failure):
                    raise item.error
                yield item
        finally:
            producer.cancel()
            await asyncio.wait({producer})  # never raises, whatever the task ended with
            while not queue.empty():
                if isinstance(item := queue.get_nowait(), Ready):
                    await self.finish(item)

    async def _next(
        self,
        queue: "asyncio.Queue[Ready | _Failure | _End]",
        producer: "asyncio.Task[None]",
        idle: Callable[[], Awaitable[None]],
    ) -> "Ready | _Failure | _End":
        """The next item, waiting at most ``poll_interval`` at a time.

        The producer hands over its own errors, so it normally ends by putting ``_End`` or a
        ``_Failure``. If it ends without (killed by something that is not an ``Exception``, or
        cancelled) nobody would ever fill the queue: say so instead of waiting for ever.
        """
        while True:
            getter = asyncio.ensure_future(queue.get())
            await asyncio.wait(
                {getter, producer}, timeout=self._poll, return_when=asyncio.FIRST_COMPLETED
            )
            if not getter.done():
                getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await getter
            if getter.done() and not getter.cancelled():
                return getter.result()
            if producer.done():
                if producer.cancelled():
                    raise RuntimeError("the download task was cancelled")
                raise producer.exception() or RuntimeError("the download task ended early")
            await idle()

    async def finish(self, ready: Ready) -> None:
        """Delete what was downloaded for ``ready`` and give its room back. Safe to repeat."""
        if ready.prepared is not None:
            for path in ready.prepared.files:
                path.unlink(missing_ok=True)
            ready.prepared = None
        if ready.reserved is not None:
            size, ready.reserved = ready.reserved, None
            await self._window.release(size)

    async def _produce(
        self, batches: AsyncIterator[Batch], queue: "asyncio.Queue[Ready | _Failure | _End]"
    ) -> None:
        try:
            async for batch in batches:
                ready = await self._make(batch)
                try:
                    await queue.put(ready)
                except BaseException:  # cancelled while waiting for room: nobody else has it
                    await self.finish(ready)
                    raise
        except Exception as exc:  # handed to the consumer, in order
            await queue.put(_Failure(exc))
        else:
            await queue.put(_End())
