"""Wizard steps for ``tgmirror new`` (docs/02-cli-ux.md, steps 1-2).

Prompts only: these functions receive channels that were already fetched, ask, and return the
same values the ``--src``/``--dst``/``--dst-new`` flags produce. Validation and every rule live in
``engine/endpoints.py``, so both paths end up in the same place (skill ``cli-wizard``).
"""

from collections.abc import Sequence

from tgmirror.cli.errors import describe
from tgmirror.core.gateway import ChannelInfo
from tgmirror.engine.endpoints import (
    InvalidChannelTitle,
    NewChannelSpec,
    eligible_destinations,
    validate_new_channel,
)
from tgmirror.ui.messages import t
from tgmirror.ui.prompts import Choice, Prompter
from tgmirror.ui.tables import channel_label

MAX_TITLE_ATTEMPTS = 3


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
