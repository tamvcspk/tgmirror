"""Rendering of channel lists: a Rich table for people, JSON for scripts."""

import json
from collections.abc import Sequence
from dataclasses import asdict

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from tgmirror.core.gateway import ChannelInfo
from tgmirror.ui.messages import t


def channel_label(info: ChannelInfo) -> str:
    """One-line description used in prompts and summaries."""
    label = f"[{t(f'kind.{info.kind}')}] {info.title}"
    if info.username:
        label += f" @{info.username}"
    return f"{label} ({info.id})"


def channels_json(channels: Sequence[ChannelInfo]) -> str:
    return json.dumps([asdict(c) for c in channels], ensure_ascii=False, indent=2)


def print_channels(console: Console, channels: Sequence[ChannelInfo]) -> None:
    if not channels:
        console.print(t("channels.empty"))
        return
    # Compact on purpose: it has to stay readable at 80 columns, and the username rides along
    # under the title instead of taking a column of its own.
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column(t("col.kind"))
    table.add_column(t("col.title"), overflow="fold")
    table.add_column(t("col.id"), no_wrap=True)
    table.add_column(t("col.members"), justify="right")
    table.add_column(t("col.noforwards"), justify="center")
    table.add_column(t("col.post"))
    for c in channels:
        # Text, not str: titles may contain [brackets] that Rich would read as markup
        title = Text(c.title)
        if c.username:
            title.append(f"\n@{c.username}", style="dim")
        table.add_row(
            t(f"kind.{c.kind}"),
            title,
            str(c.id),
            f"{c.participants:,}" if c.participants is not None else "?",
            t("yes") if c.noforwards else "",
            t("admin") if c.is_admin and c.can_post else (t("yes") if c.can_post else t("no")),
        )
    console.print(table)
    console.print(t("channels.count", count=len(channels)))
