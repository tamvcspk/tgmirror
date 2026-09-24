"""``tgmirror config get|set``: view or change ``[limits]`` in ``config.toml`` (no Telegram
connection needed). See docs/02-cli-ux.md, "Config"."""

import json
from typing import Annotated

import typer

from tgmirror.cli.errors import run
from tgmirror.cli.runtime import Runtime
from tgmirror.core.config import LIMIT_KEYS, format_limit, set_limit
from tgmirror.core.errors import ConfigError
from tgmirror.ui.messages import t

config_app = typer.Typer(help="View or change config.toml.")


def _as_json_value(value: object) -> object:
    return list(value) if isinstance(value, tuple) else value


@config_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        get(ctx, None, False)


@config_app.command("get")
def get(
    ctx: typer.Context,
    key: Annotated[
        str | None, typer.Argument(help="One [limits] key; omit to show everything.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show the paths tgmirror uses and every [limits] key with its current value.

    Example: tgmirror config get         (or: tgmirror config get batch_size)
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        limits = rt.config().limits

        if key is not None:
            if key not in LIMIT_KEYS:
                raise ConfigError(f"unknown config key {key!r}; choices: {', '.join(LIMIT_KEYS)}")
            value = getattr(limits, key)
            if as_json:
                typer.echo(json.dumps({key: _as_json_value(value)}, ensure_ascii=False))
            else:
                typer.echo(format_limit(value))
            return

        if as_json:
            record: dict[str, object] = {
                "paths": {
                    "config_file": str(rt.paths.config_file),
                    "db_path": str(rt.paths.db_path),
                    "sessions_dir": str(rt.paths.sessions_dir),
                },
                "limits": {k: _as_json_value(getattr(limits, k)) for k in LIMIT_KEYS},
            }
            typer.echo(json.dumps(record, ensure_ascii=False, indent=2))
            return

        typer.echo(t("config.paths_title"))
        typer.echo(t("config.path_line", label=t("config.path_config"), path=rt.paths.config_file))
        typer.echo(t("config.path_line", label=t("config.path_db"), path=rt.paths.db_path))
        typer.echo(
            t("config.path_line", label=t("config.path_sessions"), path=rt.paths.sessions_dir)
        )
        typer.echo("")
        typer.echo(t("config.limits_title"))
        for k in LIMIT_KEYS:
            typer.echo(f"  {k} = {format_limit(getattr(limits, k))}")

    run(rt, command())


@config_app.command("set")
def set_(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="A [limits] key, e.g. batch_size.")],
    value: Annotated[str, typer.Argument(help="The new value.")],
) -> None:
    """Change one [limits] key in config.toml; validated (including cross-field rules like
    max_delay >= min_delay) before anything is written.

    Example: tgmirror config set batch_size 30
    """
    rt: Runtime = ctx.obj

    async def command() -> None:
        new_limits = set_limit(rt.paths, key, value)
        typer.echo(t("config.saved", name=key, value=format_limit(getattr(new_limits, key))))

    run(rt, command())
