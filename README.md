# tgmirror

CLI tool that clones a Telegram channel, group or forum you have joined into another one (existing or newly created; forum topics are mapped topic to topic), using your own Telegram API credentials (MTProto, via [Telethon](https://github.com/LonamiWebs/Telethon)).

> Status: **phase 1 (login, channels, create a destination) done and checked against a real account**. `login`, `logout`, `whoami`, `channels` and `new` (pick a source, pick or create a destination; it does not save a job yet) exist; copying starts in phase 2. See [docs/](docs/) for the design and [.claude/skills/](.claude/skills/) for the project skills.

## Goals

- Pick a source (channel, supergroup/group or forum) from what you joined, pick a destination (existing or create new).
- One account, and `sync` is run by hand: there is no background daemon.
- Filter what gets cloned: media types, hashtags, regex, date/ID range, size, ...
- Pause / stop / resume with checkpoints, then run **delta clones** that only copy new messages.
- Fast (server-side copy, batching) and gentle (adaptive rate limiting, FLOOD_WAIT handling, anti-spam hygiene).

## Usage

Available now (phase 1):

```bash
tgmirror login                # api_id / api_hash (from my.telegram.org/apps) + account login
tgmirror whoami | logout
tgmirror channels [--search TEXT] [--writable] [--json]
tgmirror new                  # wizard: pick source, pick or create destination (no job saved yet)
tgmirror new --src "@my_channel" --dst-new "My channel (copy)" --yes   # keep the quotes in PowerShell
```

Planned (later phases):

```bash
tgmirror new                  # full wizard: source -> destination -> filters
tgmirror run <job>            # start / resume
tgmirror pause|stop <job>
tgmirror sync <job>           # delta clone
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
