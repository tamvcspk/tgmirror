"""Wizard steps for ``tgmirror clone`` (docs/02-cli-ux.md, steps 1-3).

Prompts only: these functions receive channels that were already fetched, ask, and return the
same values the ``--src``/``--dst``/``--dst-new``/filter flags produce. Validation and every rule
live in ``engine/endpoints.py`` and ``filters/``, so both paths end up in the same place (skill
``cli-wizard``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tgmirror.cli.errors import describe
from tgmirror.core.gateway import CaptionMode, ChannelInfo, MediaKind
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
    for attempt in range(1, MAX_TITLE_ATTEMPTS + 1):
        title = await prompter.text(t("clone.prompt_title"))
        about = await prompter.text(t("clone.prompt_about"))
        try:
            return validate_new_channel(NewChannelSpec(title, about))
        except InvalidChannelTitle as exc:
            if attempt == MAX_TITLE_ATTEMPTS:
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


async def pick_filters(prompter: Prompter, *, can_keep: bool = False) -> FilterSpec | None:
    """Step 3: no filter, a few criteria, or a YAML file. A rejected answer asks again.

    ``can_keep`` (the pair was cloned before) adds the first choice, "keep the filter of the
    previous run", answered with ``None`` like giving no filter flag at all.
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
            return await (_ask_file(prompter) if how == "file" else _ask_criteria(prompter))
        except FilterError as exc:
            if attempt == MAX_FILTER_ATTEMPTS:
                raise
            prompter.say(describe(exc))
    raise AssertionError("unreachable")  # pragma: no cover


def _words(text: str) -> list[str]:
    return [w.strip() for w in text.split(",") if w.strip()]


async def _ask_criteria(prompter: Prompter) -> FilterSpec:
    """The same values the flags carry, so ``from_flags`` validates them the same way."""
    media = await prompter.checkbox(
        t("filter.ask_media"), [Choice(kind.value, kind.value) for kind in MediaKind]
    )
    hashtags = _words(await prompter.text(t("filter.ask_hashtags")))
    keywords = _words(await prompter.text(t("filter.ask_contains")))
    since = (await prompter.text(t("filter.ask_since"))).strip()
    until = (await prompter.text(t("filter.ask_until"))).strip()
    min_size = (await prompter.text(t("filter.ask_min_size"))).strip()
    max_size = (await prompter.text(t("filter.ask_max_size"))).strip()
    return from_flags(
        FlagFilters(
            media=",".join(media) or None,
            hashtag=hashtags,
            contains=keywords,
            since=since or None,
            until=until or None,
            min_size=min_size or None,
            max_size=max_size or None,
        )
    )


async def _ask_file(prompter: Prompter) -> FilterSpec:
    return from_file(Path((await prompter.text(t("filter.ask_file"))).strip().strip("\"'")))
