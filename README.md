# tgmirror

CLI tool that clones a Telegram channel, group or forum you have joined into another one (existing or newly created; forum topics are mapped topic to topic), using your own Telegram API credentials (MTProto, via [Telethon](https://github.com/LonamiWebs/Telethon)).

> Status: **phase 4 (rate limiting) done on top of phase 3 (filters) and phase 2 (copy, checkpoints, pause/resume); the job flow was then redesigned (2026-09-20): `clone` copies at once, earlier runs are a log (`history`), running the same pair again is a delta. Phase 2 was run by hand on a real account with a channel that allows forwarding (albums and kill-then-resume work; a "Restrict saving content" group you do not administer is refused as designed; an admin-owned restricted source is not tried yet). Filters, including the server-side narrowing, are tested against fakes only: compare a job made with `--no-pushdown` before trusting them on a real channel. The rate limiter (AIMD delay, FloodWait auto-wait, daily cap) is tested against fakes only: its numbers are conservative guesses, not measured Telegram limits, and no real FloodWait has been seen yet**. `login`, `logout`, `whoami`, `channels`, `clone`, `run`, `pause`, `stop` and `history` exist. The keys `p`/`r`/`q` and the in-place pause are tested against fakes only. The database schema changed without a migration: delete an old `tgmirror.db`. `retry`, `status`, reupload and the Rich view come in later phases. See [docs/](docs/) for the design and [.claude/skills/](.claude/skills/) for the project skills.

## Goals

- Pick a source (channel, supergroup/group or forum) from what you joined, pick a destination (existing or create new).
- One account, and everything is run by hand as an ordinary command in your terminal (`clone` copies at once, Ctrl+C stops it): no background daemon, no schedule, no jobs to manage.
- Filter what gets cloned: media types, hashtags, regex, date/ID range, size, ...
- Pause / stop / resume with checkpoints (keys `p` / `r` / `q`), then run the same clone again for a **delta** that only copies new messages.
- Fast (server-side copy, batching) and gentle (adaptive rate limiting, FLOOD_WAIT handling, anti-spam hygiene).

## Usage

Available now:

```bash
tgmirror login                # api_id / api_hash (from my.telegram.org/apps) + account login
tgmirror whoami | logout
tgmirror channels [--search TEXT] [--writable] [--json]
tgmirror clone                # wizard: pick source, pick or create destination, filter, one question, then it copies
tgmirror clone --src "@my_channel" --dst-new "My channel (copy)" --yes   # keep the quotes in PowerShell
tgmirror clone --src "@my_channel" --dst-new "Videos" --media video --hashtag "#news" --since 2024-01-01 --yes
tgmirror clone --src "@my_channel" --dst "Copy" --filter-file filters.yaml --preview   # YAML: include/exclude/date/id/album
tgmirror clone --src "@my_channel" --dst "Copy" --yes   # the same pair again: only what is new, same filter
tgmirror clone --src "@my_channel" --dst "Copy" --media photo --yes   # another filter: read again, nothing copied twice
tgmirror run [n]              # the latest run's clone again (or run n of `history`); Ctrl+C stops it, saving progress
tgmirror run --wait           # sit out FloodWaits of any length (default: end the run after [limits] max_auto_wait)
tgmirror pause | stop         # from another terminal: pause in place / stop after the current batch
tgmirror history [n] [--json] # what earlier runs did (n: one run in detail, with failed messages and why)
```

While a clone runs in a terminal: `p` pause (it holds until resumed), `r` resume, `q` stop; Ctrl+C also stops (exit 130). Everything runs in your terminal: no background process and no schedule.

Planned (later phases):

```bash
tgmirror clone                # wizard: also the options step (mode, caption handling)
tgmirror retry [n] | status   # retry the failed messages of a run; progress and ETA
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
| [docs/04-state-checkpoint.md](docs/04-state-checkpoint.md) | SQLite schema (runs and mirrors), checkpoint, delta |
| [docs/05-chong-flood.md](docs/05-chong-flood.md) | Rate limiting, FLOOD_WAIT, anti-spam |
| [docs/06-lo-trinh.md](docs/06-lo-trinh.md) | Roadmap, open questions |

## Responsible use

Only clone content you own or are allowed to copy. tgmirror respects channels with "Restrict saving content" enabled (see decision D3 in the overview). Automating a user account can get it limited by Telegram; the tool reduces that risk but cannot eliminate it.
