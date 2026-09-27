"""Pure functions building the start/result lines of a run or a backup (Phase 15b, L2,
docs/06-lo-trinh.md): the same wording either way it is shown, so the classic CLI
(``cli/commands/run.py``/``backup.py``'s ``execute``, printed one by one with ``typer.echo``) and
the full-screen menu (``ui/menu/run_screen.py``/``backup_screen.py``, appended to a screen's running
list of lines) never drift apart from writing the same thing twice.
"""

from tgmirror.store.backups import Backup
from tgmirror.store.runs import FilterChange, Run, RunStatus, StartedRun
from tgmirror.ui.messages import t


def run_start_lines(started: StartedRun, failed_count: int | None) -> list[str]:
    """Decision D3's "you take full responsibility" line is not part of this: the classic CLI
    sends it to stderr (a warning, not progress) while the menu has no separate stream to send it
    to, so each caller prints/appends it itself before these lines (``execute``/``RunScreen``)."""
    current = started.run
    lines: list[str] = []
    if (retry_of := current.options.retry_of) is not None:  # no source cursor/filter to talk about
        lines.append(
            t(
                "run.retry_start",
                id=current.id,
                of=retry_of,
                count=failed_count or 0,
                src=current.src_title,
                dst=current.dst_title,
            )
        )
        return lines
    lines.append(
        t(
            "run.start",
            id=current.id,
            src=current.src_title,
            dst=current.dst_title,
            cursor=current.cursor_from,
        )
    )
    if started.forgot is not None:
        lines.append(t("run.fresh_started", count=started.forgot))
    elif started.filters is FilterChange.CHANGED:
        lines.append(t("run.filter_changed"))
    if started.filters is FilterChange.SAME and current.filters_json != "{}":
        lines.append(t("run.filter_reused"))
    return lines


def run_result_lines(final: Run, retry_of: int | None) -> list[str]:
    lines = [
        t(
            "run.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            failed=final.failed,
        )
    ]
    if final.skipped_filter:
        lines.append(t("run.skipped", count=final.skipped_filter))
    if final.skipped_unsupported:
        lines.append(t("run.unsupported_total", count=final.skipped_unsupported, id=final.id))
    if final.gone:
        lines.append(t("retry.gone", count=final.gone))
    if final.failed:
        key = "retry.still_failing" if retry_of is not None else "run.retry_hint"
        lines.append(t(key, count=final.failed, id=final.id))
    if final.status is RunStatus.STOPPED:
        if retry_of is not None:  # `run` would start a delta: the messages left are retry's job
            lines.append(t("run.retry_continue_hint", of=retry_of))
        else:
            lines.append(t("run.continue_hint", id=final.id))
    return lines


def backup_start_line(backup_id: int, src: str, directory: str) -> str:
    """``src`` is whatever label the caller already has: the classic CLI shows the fuller
    ``channel_label(...)`` (it has the ``ChannelInfo`` on hand before ``begin_backup`` runs), the
    menu shows the plain ``Backup.src_title`` — this only shares the one wording, not the label."""
    return t("backup.start", id=backup_id, src=src, dir=directory)


def backup_result_lines(final: Backup) -> list[str]:
    lines = [
        t(
            "backup.result",
            id=final.id,
            status=t(f"status.{final.status}"),
            done=final.done,
            cursor=final.cursor_to,
        )
    ]
    if final.skipped_filter:
        lines.append(t("run.skipped", count=final.skipped_filter))
    if final.gone:
        lines.append(t("retry.gone", count=final.gone))
    if final.status is RunStatus.STOPPED:
        lines.append(t("backup.continue_hint", dir=final.dir))
    return lines
