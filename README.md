# tgmirror

CLI tool that clones a Telegram channel, group or forum you have joined into another one (existing or newly created; forum topics are mapped topic to topic), using your own Telegram API credentials (MTProto, via [Telethon](https://github.com/LonamiWebs/Telethon)).

> Status: **phase 4 (rate limiting) done on top of phase 3 (filters) and phase 2 (copy, saved jobs, pause/resume). Phase 2 was run by hand on a real account with a channel that allows forwarding (albums and kill-then-resume work; a "Restrict saving content" group you do not administer is refused as designed; an admin-owned restricted source is not tried yet). Filters, including the server-side narrowing, are tested against fakes only: compare a job made with `--no-pushdown` before trusting them on a real channel. The rate limiter (AIMD delay, FloodWait auto-wait, daily cap) is tested against fakes only: its numbers are conservative guesses, not measured Telegram limits, and no real FloodWait has been seen yet**. `login`, `logout`, `whoami`, `channels`, `new` (saves a job), `run` (also `--refilter`), `pause` and `stop` exist. Delta `sync`, reupload and the TUI come in later phases. See [docs/](docs/) for the design and [.claude/skills/](.claude/skills/) for the project skills.

## Goals

- Pick a source (channel, supergroup/group or forum) from what you joined, pick a destination (existing or create new).
- One account, and `sync` is run by hand: there is no background daemon.
- Filter what gets cloned: media types, hashtags, regex, date/ID range, size, ...
- Pause / stop / resume with checkpoints, then run **delta clones** that only copy new messages.
- Fast (server-side copy, batching) and gentle (adaptive rate limiting, FLOOD_WAIT handling, anti-spam hygiene).

## Usage

Available now (phase 3):

```bash
tgmirror login                # api_id / api_hash (from my.telegram.org/apps) + account login
tgmirror whoami | logout
tgmirror channels [--search TEXT] [--writable] [--json]
tgmirror new                  # wizard: pick source, pick or create destination, save the job, maybe run it
tgmirror new --src "@my_channel" --dst-new "My channel (copy)" --yes --run   # keep the quotes in PowerShell
tgmirror new --src "@my_channel" --dst-new "Videos" --media video --hashtag "#news" --since 2024-01-01 --yes
tgmirror new --src "@my_channel" --dst "Copy" --filter-file filters.yaml --preview   # YAML: include/exclude/date/id/album
tgmirror run <job>            # start / resume (id or exact name); Ctrl+C saves and exits
tgmirror run <job> --refilter --media photo   # change what a job copies, scan again (nothing is copied twice)
tgmirror run <job> --wait     # sit out FloodWaits of any length (default: park the job after [limits] max_auto_wait)
tgmirror pause|stop <job>     # from another terminal: stop after the current batch
```

Planned (later phases):

```bash
tgmirror new                  # wizard: also the options step (mode, caption handling)
tgmirror sync <job>           # delta clone (today `run` on a finished job already picks up new messages)
tgmirror status | jobs
```

## Development

```bash
uv sync                 # create .venv, install deps + dev tools
uv run pytest           # tests (no network; uses FakeGateway)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run tgmirror --version
```

## Docs

| File | Content |
|---|---|
| [docs/00-tong-quan.md](docs/00-tong-quan.md) | Goals, non-goals, decisions, legal notes |
| [docs/01-kien-truc.md](docs/01-kien-truc.md) | Architecture, clone strategies, runner loop |
| [docs/02-cli-ux.md](docs/02-cli-ux.md) | Commands, wizard flow, config |
| [docs/03-filters.md](docs/03-filters.md) | Filter model and DSL |
| [docs/04-state-checkpoint.md](docs/04-state-checkpoint.md) | SQLite schema, checkpoint, delta sync |
| [docs/05-chong-flood.md](docs/05-chong-flood.md) | Rate limiting, FLOOD_WAIT, anti-spam |
| [docs/06-lo-trinh.md](docs/06-lo-trinh.md) | Roadmap, open questions |

## Responsible use

Only clone content you own or are allowed to copy. tgmirror respects channels with "Restrict saving content" enabled (see decision D3 in the overview). Automating a user account can get it limited by Telegram; the tool reduces that risk but cannot eliminate it.
