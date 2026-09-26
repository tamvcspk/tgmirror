# tgmirror

CLI tool that clones a Telegram channel, group or forum you have joined into another one (existing or newly created; forum topics are mapped topic to topic), using your own Telegram API credentials (MTProto, via [Telethon](https://github.com/LonamiWebs/Telethon)).

> Status: **phases 0-13 done** (of 16+; see `docs/06-lo-trinh.md`), including group/supergroup/forum sources with topic mapping (phase 8), OS keyring for `api_id`/`api_hash` (phase 9), `tgmirror appdata export`/`import` to move to another machine (phase 10), `tgmirror backup`/`restore` to and from a plain directory (phase 11), a Docker image (phase 12, **confirmed working against real Telegram** by hand), and CI plus a release workflow that builds a wheel/sdist and pushes the Docker image to Docker Hub on a tag (phase 13, not yet run against a real tag) — but phases 8-11 and 13 have each only been checked against a fake Telegram/keyring/filesystem in tests so far, **not yet run end to end with a real account** (a real forum, a real keyring, a real cross-machine export/import, a real restore, a real tagged release). PyInstaller binaries, PyPI, winget, and the APT/RPM/AUR channels (phase 14) and a full-screen menu entry for `backup`/`restore` (phase 15) are not started. `clone` copies at once (server-side copy, or download-then-reupload for protected/caption-changing cases), Ctrl+C or `docker stop` stops it and saves progress; running the same pair again is a **delta** (only new messages). A destination no longer has to match the source's kind (e.g. group → channel is fine); a forum source paired with a non-forum destination can keep each topic's name as a hashtag instead of losing it. Filters (media/hashtag/regex/date/id/size/topic/sender, with server-side narrowing), the rate limiter (AIMD delay, FloodWait auto-wait, daily cap), `retry`/`status`, and reupload (captions, polls/quizzes/locations, prefetching) all exist and have each been run by hand on a real account at least once except phases 8-11 and 13's mechanics (see `docs/06-lo-trinh.md` for exactly what has and has not been tried live). Typing bare `tgmirror` on a real terminal opens a full-screen, keyboard-driven app (menu) instead of typing each command by hand; `tgmirror doctor` checks the session, `cryptg`, destination permissions and prints the safety notes below. See [docs/](docs/) for the design and [.claude/skills/](.claude/skills/) for the project skills.

## Goals

- Pick a source (channel, supergroup/group or forum) from what you joined, pick a destination (existing or create new).
- One account, and everything is run by hand as an ordinary command in your terminal (`clone` copies at once, Ctrl+C stops it): no background daemon, no schedule, no jobs to manage.
- Filter what gets cloned: media types, hashtags, regex, date/ID range, size, ...
- Pause / stop / resume with checkpoints (keys `p` / `r` / `q`), then run the same clone again for a **delta** that only copies new messages.
- Fast (server-side copy, batching) and gentle (adaptive rate limiting, FLOOD_WAIT handling, anti-spam hygiene).
- Back up a source to a directory on disk (`backup`) and rebuild it on a channel later (`restore`) — same filters, pause/stop/resume and "Restrict saving content" handling as a clone.

## Install

Not published to PyPI yet; install straight from the source instead:

```bash
uv tool install git+https://github.com/tamvcspk/tgmirror.git   # latest main, straight from GitHub
uv tool install .                                               # from a local checkout (this repo)
pipx install .                                                  # pipx works the same way
```

Any of these put a `tgmirror` command on your PATH, in its own isolated environment (`uv tool`/`pipx` both manage that for you — no manual venv needed). Run `tgmirror doctor` afterwards to check the session, `cryptg`, and destination permissions.

### Docker

A tagged release (`v*`) has CI push the image to `docker.io/tamvo1808/tgmirror:<version>` (and `:latest` for a non-prerelease version) — `docker pull tamvo1808/tgmirror`. That hasn't happened for a real tag yet (the workflow exists but hasn't been run against one: see `docs/06-lo-trinh.md`), so build it locally in the meantime:

```bash
docker build -t tgmirror .

# first run: log in interactively, session goes on the /data volume
docker run -it --rm -v tgm-data:/data -v tgm-config:/config tgmirror login

# every run after that: no -it needed, everything is flags
docker run --rm -v tgm-data:/data -v tgm-config:/config tgmirror run --yes
```

- `-v tgm-data:/data -v tgm-config:/config` are required: the session, database and config live there, and `run`/`retry` depend on the session's cached channel entities surviving between runs. Use a bind-mounted host directory instead of named volumes if you want to look at the files; a bind mount keeps the host's ownership, so `chown -R 1000:1000` it (or pass `--user "$(id -u):$(id -g)"`) so the non-root `tgmirror` user inside the container can write to it.
- Credentials: `-e TGMIRROR_API_ID=... -e TGMIRROR_API_HASH=...`, or `-e TGMIRROR_API_ID_FILE=/run/secrets/api_id` pointing at a Docker/Kubernetes secret mount (the value never shows up in `docker inspect`). The image disables the OS keyring (`PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`) since a container has none to use.
- One `docker run` is one `tgmirror` command that copies once and exits — no daemon inside the image, matching the CLI itself; schedule repeat runs with your own cron/Kubernetes CronJob if you want that.
- `docker stop` sends SIGTERM, which the container handles exactly like the first Ctrl+C (finishes the current batch, saves, exits) for `clone`/`run`/`retry`/`backup`/`restore`. Its default 10s grace period before `SIGKILL` can be too short for a batch plus a possible FloodWait; raise it with `docker stop -t 60 <container>` (or `stop_grace_period: 60s` in Compose). A `SIGKILL` mid-batch is still safe — the same write-ahead/reconcile that protects a killed local process covers it.
- `daily_cap` is counted by the container's calendar day; the image defaults `TZ=UTC`, override with `-e TZ=...` if you want the cap's midnight to match your own.

## Usage

Available now:

```bash
tgmirror                      # a real terminal: full-screen, keyboard-driven app (see "Full-screen menu" below)
tgmirror login                # api_id / api_hash (from my.telegram.org/apps) + account login
tgmirror whoami | logout
tgmirror channels [--search TEXT] [--writable] [--json]
tgmirror topics "@my_forum" [--json]   # a forum's topics (id, title, closed), for --topic or the wizard
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
tgmirror appdata export tgmirror-backup.zip        # snapshot tgmirror.db + config.toml (no secrets) into a zip
tgmirror appdata import tgmirror-backup.zip [--yes] # restore it on another machine; existing data is moved aside, not deleted; log in again after
tgmirror backup "@my_channel" ./backups/my_channel  # save a source to a directory on disk (or `tgmirror backup` for the wizard); run it again for the same directory to get only what is new
tgmirror restore ./backups/my_channel --dst-new --yes  # rebuild a channel from a backup directory (or `tgmirror restore` for the wizard); always re-uploads, since there is no server-side copy from disk
```

While a clone runs in a terminal: `p` pause (it holds until resumed), `r` resume, `q` stop; Ctrl+C also stops (exit 130). Everything runs in your terminal: no background process and no schedule.

### Full-screen menu

Typing bare `tgmirror` on a real terminal opens a full-screen app instead (arrow keys + Enter, Esc to go back) with the same commands as menu items: New clone, Continue / Retry failures (only once a pair exists), Status, History, Joined channels, Account, Config, Quit. Not logged in yet: a shorter menu (Log in, Status, History, Config, Quit). Ctrl+C during a run started from the menu stops the run **and** exits the app, same as the classic CLI. No terminal attached (redirected, a script, CI): bare `tgmirror` still prints help, as before.

## Development

```bash
uv sync                 # create .venv, install deps + dev tools
uv run pytest           # tests (no network; uses FakeGateway)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run tgmirror --version
```

`.github/workflows/ci.yml` runs `ruff check`, `ruff format --check` and `pytest` on `ubuntu-latest` and `windows-latest` for every push to `main` and every PR. `.github/workflows/release.yml` runs on a pushed `v*` tag: re-runs the checks, builds the wheel/sdist, builds and pushes the Docker image (see "Docker" above), and attaches everything plus `SHA256SUMS` to a GitHub Release.

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
