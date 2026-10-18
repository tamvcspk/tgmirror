"""The model behind the "Khám phá" screen (docs/07-inspect-transform.md 4.5, phase 18b): narrow a
source step by step, watching the tables change.

Pure and synchronous over the units of an index. A *step* is one row of one table the person
picked: ``include`` adds its predicate to the one rule every message must satisfy (AND — exactly
the shape the filter flags make, so a result can always be said as a command), ``exclude`` adds a
rule of its own that wins. The current filter is rebuilt from the steps (``spec_of``) and judged by
the same ``Matcher`` as everything else; ``Backspace`` is dropping the last step.

Not every row can become a filter: a hidden forward name, a channel post (no sender) or a size
bucket has no predicate. Those raise ``NotFilterable`` with the reason, and nothing changes.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from tgmirror.core.gateway import Unit
from tgmirror.engine.inspect import NONE, Inspector, Report, Row, Table
from tgmirror.engine.transform import TopicRules
from tgmirror.filters.matcher import Matcher
from tgmirror.filters.model import FilterSpec

# the four a screen shows side by side: three fixed, and one that cycles through the rest
FIXED = ("media", "month", "hashtag")
CYCLE = ("forward", "domain", "topic", "sender", "route")
TABLES = (*FIXED, *CYCLE)


class NotFilterable(Exception):
    """A row that cannot be turned into a filter step; ``reason`` is a message key."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Step:
    kind: str  # "include" | "exclude"
    by: str  # the table the row came from
    key: str  # the row's key

    @property
    def field(self) -> str:
        """What this step constrains: a second include on the same field replaces the first."""
        return {"media": "media", "month": "date", "hashtag": "hashtag", "domain": "link"}.get(
            self.by, self.by
        )

    @property
    def label(self) -> str:
        return ("-" if self.kind == "exclude" else "") + self.key


def predicate_of(by: str, key: str) -> dict[str, Any]:
    """The rule predicate (or, for a month, the ``date`` range) a row of table ``by`` stands for.
    Raises ``NotFilterable``."""
    match by:
        case "media":
            return {"media": [key]}
        case "hashtag":
            return {"hashtag": [key]}
        case "domain":
            return {"link": [key]}
        case "month":
            try:
                year, month = (int(part) for part in key.split("-"))
                start = date(year, month, 1)
                end = date(year + (month == 12), month % 12 + 1, 1)
            except ValueError:
                raise NotFilterable("explore.not_filterable") from None
            return {"date": {"from": start.isoformat(), "to": end.isoformat()}}
        case "forward":
            if key == NONE:
                return {"forwarded": False}
            if key.lstrip("-").isdigit():
                return {"forwarded_from": [int(key)]}
            raise NotFilterable("explore.hidden_forward")
        case "topic":
            if key.isdigit():
                return {"topic": [int(key)]}
        case "sender":
            if key.lstrip("-").isdigit():
                return {"from_user": [int(key)]}
            raise NotFilterable("explore.channel_post")
        case "route":  # the outcome of the rules, not something a message has
            raise NotFilterable("explore.route_not_filterable")
    raise NotFilterable("explore.not_filterable")


def spec_of(steps: Sequence[Step]) -> FilterSpec:
    """The filter the steps say. Raises ``FilterError`` for one the model refuses."""
    include: dict[str, Any] = {}
    exclude: list[dict[str, Any]] = []
    when: dict[str, Any] | None = None
    for step in steps:
        predicate = predicate_of(step.by, step.key)
        if step.kind == "exclude":
            if "date" in predicate:
                raise NotFilterable("explore.exclude_month")
            exclude.append(predicate)
        elif "date" in predicate:
            when = predicate["date"]
        else:
            include.update(predicate)
    data: dict[str, Any] = {}
    if include:
        data["include"] = [include]
    if exclude:
        data["exclude"] = exclude
    if when is not None:
        data["date"] = when
    return FilterSpec.from_data(data)


class Explorer:
    """The units of a source and the steps narrowing them. ``report`` is for the selection."""

    def __init__(
        self,
        units: Sequence[Unit],
        *,
        forum: bool = False,
        topic_titles: Mapping[int, str] | None = None,
        rules: TopicRules | None = None,
    ) -> None:
        self._units = units
        self._forum = forum
        self._titles = dict(topic_titles or {})
        self._rules = rules if rules is not None and rules.routing else None
        self.steps: list[Step] = []
        self._reports: list[Report] = [self._tally(FilterSpec())]  # one per prefix of ``steps``

    @property
    def report(self) -> Report:
        return self._reports[-1]

    @property
    def spec(self) -> FilterSpec:
        return spec_of(self.steps)

    @property
    def tables(self) -> list[str]:
        """The tables worth showing: ``topic`` only for a forum, ``route`` only once the routing
        rules of a transform are loaded."""
        return [
            by
            for by in TABLES
            if (by != "topic" or self._forum) and (by != "route" or self._rules is not None)
        ]

    def set_rules(self, rules: TopicRules | None) -> None:
        """Load (or, with ``None`` or rules that route nothing, drop) the routing rules the
        ``route`` table goes by; every count is made again."""
        self._rules = rules if rules is not None and rules.routing else None
        self._reports = [self._tally(FilterSpec())]
        for n in range(1, len(self.steps) + 1):
            self._reports.append(self._tally(spec_of(self.steps[:n])))

    def rows(self, by: str) -> tuple[Row, ...]:
        table: Table = next(tab for tab in self.report.tables if tab.by == by)
        return table.rows

    def add(self, kind: str, by: str, key: str) -> Step:
        """Narrow by a row. A second include of the same kind of thing replaces the first (two
        any-of lists cannot be ANDed in one rule). Raises ``NotFilterable`` and changes nothing."""
        step = Step(kind, by, key)
        kept = [
            s for s in self.steps if not (s.kind == kind == "include" and s.field == step.field)
        ]
        steps = [*kept, step]
        spec = spec_of(steps)  # may raise: nothing has changed yet
        replaced = len(kept) != len(self.steps)
        self.steps = steps
        report = self._tally(spec)
        if replaced:  # the earlier reports described a different prefix: start the cache over
            self._reports = [self._tally(FilterSpec())]
            for n in range(1, len(steps)):
                self._reports.append(self._tally(spec_of(steps[:n])))
        self._reports.append(report)
        return step

    def pop(self) -> Step | None:
        """Drop the last step; ``None`` when there is none."""
        if not self.steps:
            return None
        self._reports.pop()
        return self.steps.pop()

    def _tally(self, spec: FilterSpec) -> Report:
        inspector = Inspector(
            None if spec.is_empty else Matcher(spec),
            self.tables,
            forum=self._forum,
            topic_titles=self._titles,
            rules=self._rules,
        )
        for unit in self._units:
            inspector.add(unit)
        return inspector.report(top=0)
