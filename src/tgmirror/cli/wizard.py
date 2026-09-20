"""Wizard steps for ``tgmirror new`` (docs/02-cli-ux.md, steps 1-3).

Prompts only: these functions receive channels that were already fetched, ask, and return the
same values the ``--src``/``--dst``/``--dst-new``/filter flags produce. Validation and every rule
live in ``engine/endpoints.py`` and ``filters/``, so both paths end up in the same place (skill
``cli-wizard``).
"""

from collections.abc import Sequence
from pathlib import Path

from tgmirror.cli.errors import describe
from tgmirror.core.gateway import ChannelInfo, MediaKind
from tgmirror.engine.endpoints import (
    InvalidChannelTitle,
    NewChannelSpec,
    eligible_destinations,
    validate_new_channel,
)
from tgmirror.filters.model import FilterError, FilterSpec
from tgmirror.filters.parser import FlagFilters, from_file, from_flags
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Choice, Prompter
from tgmirror.ui.tables import channel_label

MAX_TITLE_ATTEMPTS = 3
MAX_FILTER_ATTEMPTS = 3


async def pick_source(prompter: Prompter, channels: Sequence[ChannelInfo]) -> ChannelInfo:
    choices = [Choice(channel_label(c) + (" 🔒" if c.noforwards else ""), c) for c in channels]
    return await prompter.select(t("new.pick_source"), choices)


async def pick_destination(
    prompter: Prompter, src: ChannelInfo, channels: Sequence[ChannelInfo]
) -> ChannelInfo | NewChannelSpec:
    """An eligible existing chat, or a new channel described by a ``NewChannelSpec``."""
    candidates = eligible_destinations(src, channels)
    if not candidates:
        prompter.say(t("new.no_candidates"))
        return await ask_new_channel(prompter)

    choices: list[Choice[ChannelInfo | None]] = [Choice(t("new.create_new"), None)]
    choices += [Choice(channel_label(c), c) for c in candidates]
    picked = await prompter.select(t("new.pick_destination"), choices)
    return picked if picked is not None else await ask_new_channel(prompter)


async def ask_new_channel(prompter: Prompter) -> NewChannelSpec:
    for attempt in range(1, MAX_TITLE_ATTEMPTS + 1):
        title = await prompter.text(t("new.prompt_title"))
        about = await prompter.text(t("new.prompt_about"))
        try:
            return validate_new_channel(NewChannelSpec(title, about))
        except InvalidChannelTitle as exc:
            if attempt == MAX_TITLE_ATTEMPTS:
                raise
            prompter.say(describe(exc))
    raise AssertionError("unreachable")  # pragma: no cover


async def pick_filters(prompter: Prompter) -> FilterSpec:
    """Step 3: no filter, a few criteria, or a YAML file. A rejected answer asks again."""
    how = await prompter.select(
        t("filter.pick"),
        [
            Choice(t("filter.none"), "none"),
            Choice(t("filter.criteria"), "criteria"),
            Choice(t("filter.file"), "file"),
        ],
    )
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
