"""Wizard steps for ``tgmirror clone`` (docs/02-cli-ux.md, steps 1-3).

Prompts only: these functions receive channels that were already fetched, ask, and return the
same values the ``--src``/``--dst``/``--dst-new``/filter flags produce. Validation and every rule
live in ``engine/endpoints.py`` and ``filters/``, so both paths end up in the same place (skill
``cli-wizard``).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from tgmirror.cli.errors import describe
from tgmirror.core.gateway import CaptionMode, ChannelInfo, MediaKind, TopicInfo
from tgmirror.engine.endpoints import (
    InvalidChannelTitle,
    NewChannelSpec,
    eligible_destinations,
    validate_new_channel,
)
from tgmirror.filters.model import FilterError, FilterSpec
from tgmirror.filters.parser import FlagFilters, from_file, from_flags
from tgmirror.store.runs import Run
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Choice, Prompter
from tgmirror.ui.tables import channel_label

MAX_TITLE_ATTEMPTS = 3
MAX_FILTER_ATTEMPTS = 3

T = TypeVar("T")


async def pick_source(prompter: Prompter, channels: Sequence[ChannelInfo]) -> ChannelInfo:
    choices = [Choice(channel_label(c) + (" 🔒" if c.noforwards else ""), c) for c in channels]
    return await prompter.select(t("clone.pick_source"), choices)


async def pick_destination(
    prompter: Prompter, src: ChannelInfo, channels: Sequence[ChannelInfo]
) -> ChannelInfo | NewChannelSpec:
    """An eligible existing chat, or a new channel described by a ``NewChannelSpec``."""
    candidates = eligible_destinations(src, channels)
    if not candidates:
        prompter.say(t("clone.no_candidates"))
        return await ask_new_channel(prompter)

    choices: list[Choice[ChannelInfo | None]] = [Choice(t("clone.create_new"), None)]
    choices += [Choice(channel_label(c), c) for c in candidates]
    picked = await prompter.select(t("clone.pick_destination"), choices)
    return picked if picked is not None else await ask_new_channel(prompter)


async def ask_new_channel(prompter: Prompter) -> NewChannelSpec:
    """The title, then the description, each asked again on its own when it is refused (a
    typo in the title does not cost the description already typed, nor the other way round)."""
    spec = await _ask_valid(
        prompter, "clone.prompt_title", lambda v: validate_new_channel(NewChannelSpec(v, ""))
    )
    return await _ask_valid(
        prompter,
        "clone.prompt_about",
        lambda v: validate_new_channel(NewChannelSpec(spec.title, v)),
    )


async def _ask_valid(
    prompter: Prompter,
    key: str,
    check: Callable[[str], T],
    *,
    attempts: int = MAX_TITLE_ATTEMPTS,
    errors: tuple[type[Exception], ...] = (InvalidChannelTitle,),
) -> T:
    """Ask ``key`` until ``check`` accepts the answer; the last refusal propagates."""
    for attempt in range(1, attempts + 1):
        answer = await prompter.text(t(key))
        try:
            return check(answer)
        except errors as exc:
            if attempt == attempts:
                raise
            prompter.say(describe(exc))
    raise AssertionError("unreachable")  # pragma: no cover


async def pick_run(prompter: Prompter, pairs: Sequence[Run]) -> Run:
    """``tgmirror run`` with no run number and more than one pair in history: which to continue.

    Only called when there is a real choice (``cli/commands/run.py``); with a single pair, or
    without a terminal, that one pair is picked without asking, same as before this step existed.
    """
    choices = [
        Choice(
            t(
                "run.pick_pair_line",
                id=p.id,
                src=p.src_title,
                dst=p.dst_title,
                mode=p.mode,
                status=t(f"status.{p.status}"),
            ),
            p,
        )
        for p in pairs
    ]
    return await prompter.select(t("run.pick_pair"), choices)


async def pick_resume(prompter: Prompter, copied: int) -> bool:
    """For a pair with progress: ``True`` to start from scratch, ``False`` to continue."""
    return await prompter.select(
        t("clone.pick_resume", count=copied),
        [
            Choice(t("clone.resume_continue"), False),
            Choice(t("clone.resume_fresh"), True),
        ],
    )


async def pick_topic_as_hashtag(prompter: Prompter) -> bool:
    """Phase 8: the source is a forum, the destination is not, and the run can rewrite text —
    keep each topic's name as a hashtag instead of dropping it silently. Default yes."""
    return await prompter.confirm(t("clone.topic_as_hashtag"), True)


@dataclass(frozen=True, slots=True)
class StrategyChoice:
    """Step 4: the values ``--mode``, ``--caption``, ``--caption-text`` and the strategy B flags
    carry."""

    mode: str = "auto"
    caption: str = "keep"
    caption_text: str = ""
    reset_polls: bool = False
    ignore_unsupported: bool = False
    placeholder: bool = False


async def pick_strategy(prompter: Prompter, *, protected: bool) -> StrategyChoice:
    """Step 4: how to copy, then (only if asked to) the captions and what cannot be copied.

    The mode is always asked (``auto``, ``copy`` or ``reupload``, the values of ``--mode``). A
    source that forbids saving its content (``protected``) has one way only, download and send
    again, so the mode is not asked, only what goes with it.

    The details (captions; with ``reupload`` also polls and what cannot be copied) sit behind one
    yes/no question, default no, so an ordinary clone answers two quick questions. ``copy`` has no
    details: a forward cannot change a caption.
    """
    if protected:
        prompter.say(t("options.protected"))
        mode = "reupload"
    else:
        mode = await prompter.select(
            t("options.pick_mode"),
            [
                Choice(t("options.mode_auto"), "auto"),
                Choice(t("options.mode_copy"), "copy"),
                Choice(t("options.mode_reupload"), "reupload"),
            ],
        )
        if mode == "copy":
            return StrategyChoice(mode)
        question = t(
            "options.customise_reupload" if mode == "reupload" else "options.customise_auto"
        )
        if not await prompter.confirm(question, False):
            return StrategyChoice(mode)
    caption = await prompter.select(
        t("options.pick_caption"), [Choice(t(f"options.caption_{m}"), m.value) for m in CaptionMode]
    )
    text = ""
    if caption == CaptionMode.APPEND:
        text = (await prompter.text(t("options.ask_caption_text"))).strip()
    flags: list[str] = []
    if mode == "reupload":
        flags = await prompter.checkbox(
            t("options.pick_flags"),
            [
                Choice(t("options.flag_reset_polls"), "reset_polls"),
                Choice(t("options.flag_ignore_unsupported"), "ignore_unsupported"),
                Choice(t("options.flag_placeholder"), "placeholder"),
            ],
        )
    return StrategyChoice(
        mode,
        caption,
        text,
        reset_polls="reset_polls" in flags,
        ignore_unsupported="ignore_unsupported" in flags,
        placeholder="placeholder" in flags,
    )


async def pick_filters(
    prompter: Prompter, *, can_keep: bool = False, topics: Sequence[TopicInfo] = ()
) -> FilterSpec | None:
    """Step 3: no filter, a few criteria, or a YAML file. A rejected answer asks again.

    ``can_keep`` (the pair was cloned before) adds the first choice, "keep the filter of the
    previous run", answered with ``None`` like giving no filter flag at all. ``topics`` (already
    fetched by the caller when the source is a forum, phase 8) adds a topic checkbox to the
    criteria step.
    """
    choices = [
        Choice(t("filter.none"), "none"),
        Choice(t("filter.criteria"), "criteria"),
        Choice(t("filter.file"), "file"),
    ]
    if can_keep:
        choices.insert(0, Choice(t("filter.keep"), "keep"))
    how = await prompter.select(t("filter.pick"), choices)
    if how == "keep":
        return None
    if how == "none":
        return FilterSpec()
    for attempt in range(1, MAX_FILTER_ATTEMPTS + 1):
        try:
            return await (
                _ask_file(prompter) if how == "file" else _ask_criteria(prompter, topics=topics)
            )
        except FilterError as exc:
            if attempt == MAX_FILTER_ATTEMPTS:
                raise
            prompter.say(describe(exc))
    raise AssertionError("unreachable")  # pragma: no cover


def _words(text: str) -> list[str]:
    return [w.strip() for w in text.split(",") if w.strip()]


def _optional(text: str) -> str | None:
    return text.strip() or None


async def _ask_criteria(prompter: Prompter, *, topics: Sequence[TopicInfo] = ()) -> FilterSpec:
    """The same values the flags carry, so ``from_flags`` validates them the same way."""
    media = await prompter.checkbox(
        t("filter.ask_media"), [Choice(kind.value, kind.value) for kind in MediaKind]
    )

    async def ask(key: str, field: str, value: Callable[[str], object]) -> Any:
        """One answer, checked on its own right away (``from_flags`` with only that field), so a
        typo is asked again alone instead of starting every criterion over."""

        def check(answer: str) -> object:
            parsed = value(answer)
            from_flags(FlagFilters(**{field: parsed}))  # type: ignore[arg-type]
            return parsed

        return await _ask_valid(
            prompter, key, check, attempts=MAX_FILTER_ATTEMPTS, errors=(FilterError,)
        )

    hashtags = await ask("filter.ask_hashtags", "hashtag", _words)
    keywords = await ask("filter.ask_contains", "contains", _words)
    since = await ask("filter.ask_since", "since", _optional)
    until = await ask("filter.ask_until", "until", _optional)
    min_size = await ask("filter.ask_min_size", "min_size", _optional)
    max_size = await ask("filter.ask_max_size", "max_size", _optional)
    picked_topics: list[int] = []
    if topics:
        picked_topics = await prompter.checkbox(
            t("filter.ask_topics"), [Choice(topic.title, topic.id) for topic in topics]
        )
    return from_flags(
        FlagFilters(
            media=",".join(media) or None,
            hashtag=hashtags,
            contains=keywords,
            since=since,
            until=until,
            min_size=min_size,
            max_size=max_size,
            topic=picked_topics,
        )
    )


async def _ask_file(prompter: Prompter) -> FilterSpec:
    return from_file(Path((await prompter.text(t("filter.ask_file"))).strip().strip("\"'")))
