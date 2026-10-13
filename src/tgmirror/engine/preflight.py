"""Pre-flight of a clone, backup or restore (docs/07-inspect-transform.md, phase 18a): what a run
over this selection *would* do, counted from the units themselves, before anything is written.

It asks the same pure functions the real run asks — ``strategy.router`` for which way each unit
goes, ``reupload.plan_unit`` for what cannot be copied — so the numbers cannot drift from what the
runner then does. What it cannot know it says so by leaving it out: how fast the connection is
(``transfer_bytes`` is the volume, not a duration) and what Telegram will answer.

The time is only the pacing the limiter imposes (a floor): a unit sent again also has to move its
bytes, and a FloodWait can add to it.
"""

import math
from dataclasses import dataclass

from tgmirror.core.config import Limits
from tgmirror.core.gateway import CaptionMode, CaptionPolicy, Unit
from tgmirror.engine.reupload import ActionKind, Options, UnsupportedMedia, plan_unit
from tgmirror.engine.strategy import Router, Strategy, router
from tgmirror.engine.transform import Transform

KINDS = ("clone", "backup", "restore")


@dataclass(frozen=True, slots=True)
class PlanOptions:
    """The flags of a run that decide how its units travel (``RunOptions`` without the rest)."""

    kind: str = "clone"  # clone | backup | restore
    mode: str = "auto"
    caption: CaptionMode = CaptionMode.KEEP
    caption_text: str = ""
    transform: Transform = Transform()  # phase 19
    topic_hashtag: bool = False  # a forum source into a destination without topics
    by_reference: bool = True  # the source lets content be saved (not ``noforwards``)
    reset_polls: bool = False
    ignore_unsupported: bool = False
    placeholder: bool = False


@dataclass(frozen=True, slots=True)
class Way:
    """What happens to some units: how they are sent, and how much that is."""

    strategy: str  # a ``Strategy`` value, or "download" (a backup) / "left_out"
    units: int
    messages: int
    bytes: int


@dataclass(frozen=True, slots=True)
class Plan:
    kind: str
    ways: tuple[Way, ...]
    blocked: tuple[tuple[str, int], ...]  # (kind, units) the run would stop at without a flag
    left_out: tuple[tuple[str, int], ...]  # (kind, units) it would leave out on purpose
    write_calls: int
    pacing_seconds: float  # the limiter's floor for those calls
    days: int  # at the daily cap
    transfer_bytes: int  # what is downloaded (and uploaded again, for a reupload)
    largest_file: int
    # phase 19b: units by the destination topic a hashtag rule gives them (``None`` = no rule did:
    # the source topic's own mapping, or General); empty without ``hashtag_to_topic``
    topics: tuple[tuple[str | None, int], ...] = ()


@dataclass(slots=True)
class _Counts:
    units: int = 0
    messages: int = 0
    bytes: int = 0

    def add(self, unit: Unit) -> None:
        self.units += 1
        self.messages += len(unit.messages)
        self.bytes += sum(m.size or 0 for m in unit.messages)


class Preflight:
    """Feed it every selected unit (``add``), then ask for the ``plan``."""

    def __init__(self, options: PlanOptions, limits: Limits) -> None:
        self.options = options
        self._limits = limits
        self._route: Router = router(
            options.mode,
            options.caption,
            by_reference=options.by_reference,
            topic_hashtag=options.topic_hashtag,
            transform=options.transform,
        )
        self._send = Options(
            CaptionPolicy(
                options.caption,
                options.caption_text,
                ops=options.transform.caption,
                text_too=options.transform.text_too,
            ),
            options.reset_polls,
            options.ignore_unsupported,
            options.placeholder,
        )
        self._by: dict[str, _Counts] = {}
        self._topics: dict[str | None, int] = {}
        # forwards go out one batch per stretch of units for the same place: a topic that changes
        # ends a batch (``engine/batcher.py``)
        self._segments: list[int] = []
        self._place: tuple[int | None, str | None] | None = None
        self._blocked: dict[str, int] = {}
        self._left_out: dict[str, int] = {}
        self._largest = 0

    def add(self, unit: Unit) -> None:
        self._largest = max([self._largest, *(m.size or 0 for m in unit.messages)])
        way = self._way(unit)
        self._by.setdefault(way, _Counts()).add(unit)
        title = self._title(unit)
        if self.options.transform.routes_to_topics and way != "left_out":
            self._topics[title] = self._topics.get(title, 0) + 1
        if way == Strategy.COPY.value:
            place = (None, title) if title is not None else (unit.topic_id, None)
            if place != self._place or not self._segments:
                self._segments.append(0)
            self._place = place
            self._segments[-1] += 1

    def _title(self, unit: Unit) -> str | None:
        return self.options.transform.topics.destination(unit)

    def _way(self, unit: Unit) -> str:
        if self.options.kind == "backup":
            return "download"
        strategy = Strategy.REUPLOAD if self.options.kind == "restore" else self._route(unit)
        if strategy is Strategy.COPY or strategy is Strategy.REFERENCE:
            return strategy.value
        # downloaded and sent again: some media cannot be, which the real run decides the same way
        try:
            action = plan_unit(unit, self._send)
        except UnsupportedMedia as exc:
            self._blocked[exc.kind] = self._blocked.get(exc.kind, 0) + 1
            return "left_out"
        if action.kind is ActionKind.SEND:
            return strategy.value
        kind = (action.reason or "unsupported:?").split(":", 1)[-1]
        self._left_out[kind] = self._left_out.get(kind, 0) + 1
        return "left_out"

    def plan(self) -> Plan:
        ways = tuple(
            Way(name, c.units, c.messages, c.bytes) for name, c in sorted(self._by.items())
        )
        sent = [w for w in ways if w.strategy != "left_out"]
        calls = self._write_calls()
        if self.options.kind == "backup":
            calls = 0  # a backup writes nothing to Telegram
        messages = sum(w.messages for w in sent)
        moves = ("download", Strategy.REUPLOAD.value)
        transfer = sum(w.bytes for w in sent if w.strategy in moves)
        return Plan(
            kind=self.options.kind,
            ways=ways,
            blocked=tuple(sorted(self._blocked.items())),
            left_out=tuple(sorted(self._left_out.items())),
            write_calls=calls,
            pacing_seconds=self._pacing(calls),
            days=0
            if self.options.kind == "backup"
            else math.ceil(messages / self._limits.daily_cap),
            transfer_bytes=transfer,
            largest_file=self._largest,
            topics=tuple(sorted(self._topics.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))),
        )

    def _write_calls(self) -> int:
        """A forward call carries up to ``batch_size`` units; any other way is a call per unit."""
        batch = self._limits.batch_size
        calls = sum(math.ceil(units / batch) for units in self._segments)
        for name in (Strategy.REFERENCE.value, Strategy.REUPLOAD.value):
            if name in self._by:
                calls += self._by[name].units
        return calls

    def _pacing(self, calls: int) -> float:
        low, high = self._limits.long_pause_range
        pauses = calls // self._limits.long_pause_every
        return calls * self._limits.min_delay + pauses * (low + high) / 2


def plan_to_dict(plan: Plan) -> dict[str, object]:
    """The plan as plain JSON-able data (``tgmirror inspect --plan ... --json``)."""
    return {
        "kind": plan.kind,
        "ways": [
            {"strategy": w.strategy, "units": w.units, "messages": w.messages, "bytes": w.bytes}
            for w in plan.ways
        ],
        "blocked": dict(plan.blocked),
        "left_out": dict(plan.left_out),
        "write_calls": plan.write_calls,
        "pacing_seconds": plan.pacing_seconds,
        "days": plan.days,
        "transfer_bytes": plan.transfer_bytes,
        "largest_file": plan.largest_file,
        "topics": [{"topic": topic, "units": units} for topic, units in plan.topics],
    }
