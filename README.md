# tgmirror

CLI tool that clones a Telegram channel, group or forum you have joined into another one (existing or newly created; forum topics are mapped topic to topic), using your own Telegram API credentials (MTProto, via [Telethon](https://github.com/LonamiWebs/Telethon)).

> Status: **phase 2 (copy, saved jobs, pause/resume) done; tested with fakes and run by hand on a real account with a channel that allows forwarding (albums and kill-then-resume work; a "Restrict saving content" source is not tried yet)**. `login`, `logout`, `whoami`, `channels`, `new` (saves a job), `run`, `pause` and `stop` exist. Filters, flood auto-wait, delta `sync`, reupload and the TUI come in later phases. See [docs/](docs/) for the design and [.claude/skills/](.claude/skills/) for the project skills.

## Goals

- Pick a source (channel, supergroup/group or forum) from what you joined, pick a destination (existing or create new).
- One account, and `sync` is run by hand: there is no background daemon.
- Filter what gets cloned: media types, hashtags, regex, date/ID range, size, ...
- Pause / stop / resume with checkpoints, then run **delta clones** that only copy new messages.
- Fast (server-side copy, batching) and gentle (adaptive rate limiting, FLOOD_WAIT handling, anti-spam hygiene).

## Usage

Available now (phase 2):

```bash
tgmirror login                # api_id / api_hash (from my.telegram.org/apps) + account login
tgmirror whoami | logout
tgmirror channels [--search TEXT] [--writable] [--json]
tgmirror new                  # wizard: pick source, pick or create destination, save the job, maybe run it
tgmirror new --src "@my_channel" --dst-new "My channel (copy)" --yes --run   # keep the quotes in PowerShell
tgmirror run <job>            # start / resume (id or exact name); Ctrl+C saves and exits
tgmirror pause|stop <job>     # from another terminal: stop after the current batch
```

Planned (later phases):

```bash
tgmirror new                  # full wizard: source -> destination -> filters
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
