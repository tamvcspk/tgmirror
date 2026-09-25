# tgmirror — project guide for Claude

CLI app (`tgmirror`) that clones a Telegram channel, group or forum (with topic mapping) the user joined into another one, via MTProto (Telethon). One account, manual runs (`tgmirror clone` copies at once in the foreground, Ctrl+C stops it) — no daemon, no schedule, no multi-account, no user-facing "jobs": earlier runs are just a log (`tgmirror history`). Design docs in `docs/` (Vietnamese) are the source of truth; update them when a decision changes.

## Stack

Python >= 3.11, `uv`, Telethon + `cryptg`, Typer (CLI), questionary (prompts), Rich (progress/TUI), aiosqlite, pydantic (config/filters), PyYAML (`--filter-file`), `regex` (filter regexes, with a timeout), `hachoir` (Telethon reads video/audio details from files it re-uploads in an album), platformdirs (paths), tenacity (non-Telegram retries), `keyring` (OS credential store for `api_id`/`api_hash`, Phase 9), pytest + pytest-asyncio + pytest-timeout (60 s per test: a hung test fails instead of blocking the run), ruff. Config is read with stdlib `tomllib`. Dev commands: `uv sync`, `uv run pytest`, `uv run ruff check .` / `uv run ruff format .` (ruff ignores `*.md`). `scripts/mutation_check.py` breaks one mechanism at a time in a temporary copy of `src/`+`tests/` and reports which ones no test notices (manual, a few minutes; never touches the working tree). `scripts/spike_*.py` are questions only real Telegram can answer (count, sending media by reference, transfer speed); the user runs them, some write to a test channel.

## Layout (`src/tgmirror/`; packages are added phase by phase, see `docs/06-lo-trinh.md`)

```
core/     gateway (Telegram wrapper), auth (login flow), telethon_gateway (only Telethon importer), limiter (AIMD, daily cap, `limiter_state`), pool (`RequestBudget` and `run_parts`: file parts in parallel under one request budget), errors, config, paths
engine/   endpoints (source/destination rules), runs (`begin_run`, resolve, vet), planner (`units`, and `failed_units` for `retry`), batcher, strategy (`Strategy`, `router`: which unit is forwarded, sent again by its file ids, or downloaded and uploaded again), preview, copy (A), reupload (B: `plan_unit`, `send_unit`, `Window`/`Pipeline` that download ahead; `Window` is the hard ceiling on how far downloading runs ahead of uploading), transfer (`TransferTracker`: speed and progress of the file in flight), flood (`FloodGuard`: pacing + FloodWait handling for reads and writes), reconcile, runner, status (progress/ETA estimates for `status`), topics (`TopicResolver`: forum topic mapping and the topic-as-hashtag fallback)
filters/  model, parser (YAML + flags), pushdown (`plan_read`), matcher (pure, client side)
store/    schema.sql (+ numbered migrations after release), db (`Store`: the only place with SQL), runs (`Run`, `Mirror`), msgmap, floodlog, limiterstate, topicmap, appdata (export/import of `tgmirror.db` + `config.toml`, no secrets, Phase 10)
cli/      app, wizard, filter_options (shared filter flags), runtime (injectable Runtime), errors (exit codes), interrupt (Ctrl+C), keys (hotkeys p/r/q), commands/ (auth, channels, topics, clone, run, retry, status, control = pause/stop, history, config = get/set `[limits]`, doctor, appdata = export/import)
ui/       messages (all user strings), prompts (`Prompter`, `run_steps`), tables, progress (plain-line reporter), tui (Rich Live), menu/ (full-screen app of bare `tgmirror`: `MenuPrompter`, screens)
```

## Hard rules

1. **Every Telegram network call goes through `core.gateway.TelegramGateway` and the limiter.** No raw `client(...)` / `client.send_*` elsewhere. This is what makes FLOOD_WAIT handling and tests possible. In a `run` that means `FloodGuard` (`engine/flood.py`); the one-shot commands (login, channels, `topics`, `history`, `doctor`) and the setup `clone`/`run` does before it starts copying (listing chats, creating the destination, `last_message_id` of the destination and the source, the preview, and the `count` that opens a run: its analysis) are the documented exception. See skill `flood-safety`.
2. The Telethon client is created with `flood_sleep_threshold=0`, so every FloodWait surfaces to our limiter instead of being slept silently.
3. Progress is durable: state changes go through `store` in one transaction per batch. See skill `checkpoint-state`.
4. Never split an album (`grouped_id`) across batches.
5. Respect `noforwards` sources (decision D3, changed 2026-09-20: the user takes full responsibility). Nothing copies a protected source by default. The only way is `--mode reupload` with the user's own statement, `--yes-i-administer-this-channel`, which also lets an account that is not an admin through (the user owns the channel through another account; tgmirror cannot check the claim and says so) — typed as a CLI flag, or, in an interactive flow (wizard or full-screen menu, refined 2026-09-25) where there is no flag to type, into a dedicated prompt that asks for that exact flag text verbatim (not a Yes/No click: `cli/commands/clone.py::CloneFlow._confirm_unadministered`). An account that does administer the source may answer a plain Yes/No prompt instead; `--yes` never stands in for the flag or the typed statement. Every run that could re-upload re-reads the source first (`begin_run`), so `run`/`retry` cannot slip past it.
6. Never log or print `api_hash`, session strings, phone numbers or login codes. `*.session` files are secrets.
7. The CLI must work both interactively (wizard) and non-interactively (flags/YAML) — same code path underneath. See skill `cli-wizard`.
8. Engine code depends on the `TelegramGateway` protocol, not on Telethon types, so it can be tested with `FakeGateway` (no network).
9. **Every task ends with the `doc-sync` skill**: decide whether `docs/`, `CLAUDE.md` or any skill needs an update, apply it, and state `Doc-sync: ...` in the final message (even when the answer is "no change needed"). Decision changes (D1–D9) require asking the user first.

## Skills (in `.claude/skills/`)

- `telethon-engine` — how we use Telethon: client setup, listing dialogs, creating channels, iterating, forward-copy, re-upload.
- `flood-safety` — limiter, FloodWait/PeerFlood handling, anti-spam defaults.
- `checkpoint-state` — SQLite schema, transactions, resume and delta semantics.
- `filter-dsl` — adding or changing filter predicates.
- `cli-wizard` — adding commands and wizard steps.
- `doc-sync` — keeps docs, `CLAUDE.md` and skills accurate; run at the end of **every** task.

## Conventions

- **Telethon is pinned to one exact version** (`telethon==1.45.0`, `TELETHON_CHECKED` in `core/telethon_gateway.py`) because the request pool and reupload use private internals. Upgrading is a procedure, not a `uv lock --upgrade`: see `docs/06-lo-trinh.md`, "Nâng cấp Telethon"; `tests/unit/test_telethon_pin.py` fails until it is followed.
- Code, identifiers, comments, commit messages: English. Design docs in `docs/`: Vietnamese.
- Type hints everywhere; `ruff` clean; async all the way down (no blocking calls in the event loop).
- Telegram does not publish exact rate limits. Numeric defaults in `docs/05-chong-flood.md` are conservative starting points, tuned from `flood_log` data — do not present them as documented limits.
