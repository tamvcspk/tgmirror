# tgmirror — project guide for Claude

CLI app (`tgmirror`) that clones a Telegram channel, group or forum (with topic mapping) the user joined into another one, via MTProto (Telethon). One account, manual `sync` runs — no daemon, no multi-account. Design docs in `docs/` (Vietnamese) are the source of truth; update them when a decision changes.

## Stack

Python >= 3.11, `uv`, Telethon + `cryptg`, Typer (CLI), questionary (prompts), Rich (progress/TUI), aiosqlite, pydantic (config/filters), platformdirs (paths), tenacity (non-Telegram retries), pytest + pytest-asyncio, ruff. Config is read with stdlib `tomllib`. Dev commands: `uv sync`, `uv run pytest`, `uv run ruff check .` / `uv run ruff format .` (ruff ignores `*.md`).

## Layout (`src/tgmirror/`; packages are added phase by phase, see `docs/06-lo-trinh.md`)

```
core/     gateway (Telegram wrapper), auth (login flow), telethon_gateway (only Telethon importer), limiter, errors, config, paths
engine/   endpoints (source/destination rules), planner, batcher, strategies (copy / reupload), runner
filters/  model, parser (YAML + flags), server pushdown, client matcher
store/    schema.sql, db, repos (jobs, msg_map, flood_log)
cli/      app, wizard, runtime (injectable Runtime), errors (exit codes), commands/
ui/       messages (all user strings), prompts, tables, progress
```

## Hard rules

1. **Every Telegram network call goes through `core.gateway.TelegramGateway` and the limiter.** No raw `client(...)` / `client.send_*` elsewhere. This is what makes FLOOD_WAIT handling and tests possible. See skill `flood-safety`.
2. The Telethon client is created with `flood_sleep_threshold=0`, so every FloodWait surfaces to our limiter instead of being slept silently.
3. Progress is durable: state changes go through `store` in one transaction per batch. See skill `checkpoint-state`.
4. Never split an album (`grouped_id`) across batches.
5. Respect `noforwards` sources (decision D3). Do not add code that bypasses content protection on channels the user does not administer.
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

- Code, identifiers, comments, commit messages: English. Design docs in `docs/`: Vietnamese.
- Type hints everywhere; `ruff` clean; async all the way down (no blocking calls in the event loop).
- Telegram does not publish exact rate limits. Numeric defaults in `docs/05-chong-flood.md` are conservative starting points, tuned from `flood_log` data — do not present them as documented limits.
