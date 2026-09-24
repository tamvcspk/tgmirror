# tgmirror

CLI tool that clones a Telegram channel, group or forum you have joined into another one (existing or newly created; forum topics are mapped topic to topic), using your own Telegram API credentials (MTProto, via [Telethon](https://github.com/LonamiWebs/Telethon)).

> Status: **phases 0–7 done except group/supergroup/forum sources (phase 8, not started)**. `clone` copies at once (server-side copy, or download-then-reupload for protected/caption-changing cases), Ctrl+C stops it and saves progress; running the same pair again is a **delta** (only new messages). Filters (media/hashtag/regex/date/id/size, with server-side narrowing), the rate limiter (AIMD delay, FloodWait auto-wait, daily cap), `retry`/`status`, and reupload (captions, polls/quizzes/locations, prefetching) all exist and have each been run by hand on a real account at least once (see `docs/06-lo-trinh.md` for exactly what has and has not been tried live). Typing bare `tgmirror` on a real terminal opens a full-screen, keyboard-driven app (menu) instead of typing each command by hand; `tgmirror doctor` checks the session, `cryptg`, destination permissions and prints the safety notes below. See [docs/](docs/) for the design and [.claude/skills/](.claude/skills/) for the project skills.

## Goals

- Pick a source (channel, supergroup/group or forum) from what you joined, pick a destination (existing or create new).
- One account, and everything is run by hand as an ordinary command in your terminal (`clone` copies at once, Ctrl+C stops it): no background daemon, no schedule, no jobs to manage.
- Filter what gets cloned: media types, hashtags, regex, date/ID range, size, ...
- Pause / stop / resume with checkpoints (keys `p` / `r` / `q`), then run the same clone again for a **delta** that only copies new messages.
- Fast (server-side copy, batching) and gentle (adaptive rate limiting, FLOOD_WAIT handling, anti-spam hygiene).

## Install

Not published to PyPI yet; install straight from the source instead:

```bash
uv tool install git+https://github.com/tamvcspk/tgmirror.git   # latest main, straight from GitHub
uv tool install .                                               # from a local checkout (this repo)
pipx install .                                                  # pipx works the same way
```

Any of these put a `tgmirror` command on your PATH, in its own isolated environment (`uv tool`/`pipx` both manage that for you — no manual venv needed). Run `tgmirror doctor` afterwards to check the session, `cryptg`, and destination permissions.

## Usage

Available now:

```bash
tgmirror                      # a real terminal: full-screen, keyboard-driven app (see "Full-screen menu" below)
tgmirror login                # api_id / api_hash (from my.telegram.org/apps) + account login
tgmirror whoami | logout
tgmirror channels [--search TEXT] [--writable] [--json]
tgmirror clone                # wizard: pick source, pick or create destination, filter, mode, one question, then it copies
tgmirror clone --src "@my_channel" --dst-new "My channel (copy)" --yes   # keep the quotes in PowerShell
tgmirror clone --src "@my_channel" --dst-new "Videos" --media video --hashtag "#news" --since 2024-01-01 --yes
tgmirror clone --src "@my_channel" --dst "Copy" --filter-file filters.yaml --preview   # YAML: include/exclude/date/id/album
tgmirror clone --src "@my_channel" --dst "Copy" --yes   # the same pair again: only what is new, same filter
tgmirror clone --src "@my_channel" --dst "Copy" --media photo --yes   # another filter: read again, nothing copied twice
tgmirror clone --src "@my_channel" --dst "Copy" --fresh   # start the pair over: forget what it copied, copy everything again (asks first)
tgmirror clone --src "@protected_channel" --dst "Copy" --mode reupload --yes-i-administer-this-channel   # "Restrict saving content" source: your own statement, your own responsibility (see "Responsible use")
tgmirror run [n]              # the latest run's clone again (or run n of `history`); Ctrl+C stops it, saving progress
tgmirror run --fresh [--yes]   # the same, for the latest run's pair
tgmirror run --wait           # sit out FloodWaits of any length (default: end the run after [limits] max_auto_wait)
tgmirror pause | stop         # from another terminal: pause in place / stop after the current batch
tgmirror history [n] [--json] # what earlier runs did (n: one run in detail, with failed messages and why)
tgmirror retry [n]            # send again the messages run n failed to copy (default: the latest run); a run of its own
tgmirror status [--json]      # progress, speed, ETA, failures and Telegram's limits of the running clone (works from a second terminal)
tgmirror config get [KEY] [--json]   # paths + every [limits] key, or one of them
tgmirror config set KEY VALUE        # change one [limits] key (validated before it is written)
tgmirror doctor                      # session, cryptg, destination permissions, safety notes
```

While a clone runs in a terminal: `p` pause (it holds until resumed), `r` resume, `q` stop; Ctrl+C also stops (exit 130). Everything runs in your terminal: no background process and no schedule.

### Full-screen menu

Typing bare `tgmirror` on a real terminal opens a full-screen app instead (arrow keys + Enter, Esc to go back) with the same commands as menu items: New clone, Continue / Retry failures (only once a pair exists), Status, History, Joined channels, Account, Config, Quit. Not logged in yet: a shorter menu (Log in, Status, History, Config, Quit). Ctrl+C during a run started from the menu stops the run **and** exits the app, same as the classic CLI. No terminal attached (redirected, a script, CI): bare `tgmirror` still prints help, as before.

Planned (phase 8): group/supergroup/forum sources with topic mapping.

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
