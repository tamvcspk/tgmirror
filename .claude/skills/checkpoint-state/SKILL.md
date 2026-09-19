---
name: checkpoint-state
description: Durable state rules for tgmirror jobs — SQLite schema, write-ahead pending rows, one-transaction-per-batch commits, resume/reconcile after a crash, delta sync via cursor, and pause/stop control flags. Use when touching store/*, engine/runner.py, resume/sync/retry commands, or any code that changes job progress.
---

# Checkpoint & state

Source doc: `docs/04-state-checkpoint.md` (schema, rationale). This skill is the rulebook for changing it safely.

## Model in one paragraph

One SQLite file (WAL). `jobs` holds config + status + `cursor_src_id` (highest source id fully finished) + control flag. `msg_map` maps `src_msg_id → dst_msg_id` per job with status `pending|done|failed|skipped` (`skipped` = unsupported type such as game/invoice, with `reason='unsupported:<kind>'`; `retry` ignores it). Filter-skipped messages are **not** stored — they only advance the cursor and a counter. Forum jobs also keep `topic_map` (`src_topic_id → dst_topic_id`) and `msg_map.src_topic_id`; creating a destination topic and saving its `topic_map` row happen in one transaction. `flood_log` and `limiter_state` support tuning and persistence (`limiter_state` is unused until phase 4). `jobs.options_json` is `JobOptions`: `batch_size` and `dst_base_id` (the destination's newest message id at creation, so reconcile never scans an old destination); unknown keys are ignored.

## Rules

1. **Write-ahead**: before calling Telegram for a batch, insert its messages as `pending` (with `batch_id`) and commit.
2. **One transaction per batch result**: update `msg_map` rows (`done`+`dst_msg_id` / `failed`+`reason`), `cursor_src_id`, `stats_json`, `limiter_state`, `updated_at` together. Never split these.
3. `cursor_src_id` is monotonic and only advances past a batch with no `pending` rows left. Never move it backwards (use `--refilter`, which restarts from 0 and relies on `msg_map` to skip `done` items).
4. The store API (`Store` in `store/db.py`) exposes intent-level methods (`begin_batch`, `commit_batch`, `discard_batch`, `confirm_pending`, `claim`, `finish`, `set_control`, ...), not raw SQL, to the engine. Raw SQL stays inside `store/` (a test enforces it). Every `Store` method takes its `asyncio.Lock`: the runner and its heartbeat task share one connection.
5. Use `BEGIN IMMEDIATE` for writes; keep transactions short; never hold one across a Telegram call.
6. Migrations: `schema.sql` is version 1 and `PRAGMA user_version` holds the version; every schema change appends a numbered script to `default_migrations()` in `store/db.py` (never edit an old one) and gets a test that upgrades a DB created by the previous version (see `test_upgrading_a_database_made_by_the_previous_version`).
7. Timestamps are UTC ISO-8601 strings; `limiter_state.day` uses local date (documented, because "daily cap" is a user-facing concept).

## Resume algorithm (must stay in this order)

1. Take the job lock (status `running` + heartbeat; takeover only if the heartbeat is older than 2 minutes or `--force-takeover`).
2. Reconcile any `pending` rows (`engine/reconcile.py` decides, `engine/runner.py` reads and writes):
   - re-read the pending source messages (media kind, album structure) and the destination tail: non-service messages with id > `max(dst_msg_id of done rows, options.dst_base_id)`;
   - count, media kinds and album structure all match ⇒ `confirm_pending`: mark `done` with the found dst ids and advance the cursor, one transaction;
   - no new dst messages ⇒ delete the `pending` rows (the batch will be re-sent);
   - anything else (including a pending message that vanished from the source) ⇒ warn, delete and re-send (favour "no gap" over "no duplicate").
3. Iterate `iter_messages(src, min_id=cursor_src_id)` (ascending) with the stored filters.
4. Skip any unit that already has a `done` row; a batch with nothing left only advances the cursor.

## What a copy call leaves behind

- Returned normally: `dst_id` ⇒ `done`; `None` ⇒ `failed('not_copied')` (Telegram made no message; not "unknown").
- `PerMessage`: delete the batch's `pending`, resend unit by unit; a unit that still fails ⇒ `failed`.
- `FloodWait`, `PeerFlood`, `NoPermission`, `ForwardsRestricted`, other rejections: nothing was created ⇒ delete the batch's `pending`, then stop the job.
- `Transient` (connection cut after sending): outcome unknown ⇒ **keep** `pending`, job `failed('transient')`; the next run reconciles.

## Delta sync

Same as resume with a `done` job: use the stored filters and cursor; no new messages ⇒ exit 0 quickly. Edit/delete sync is out of scope for v1 — do not half-implement it.

## Control flags

- `tgmirror pause|stop` only writes `jobs.control`. The runner polls it between batches (one cheap `SELECT`) and during sleeps.
- On pause/stop the runner finishes the current batch, commits, sets `status`, clears `control`.
- Ctrl+C = same as stop. A second Ctrl+C exits immediately; resume must cope via reconcile.

## Tests that must exist (FakeGateway)

Existing since phase 2: `tests/integration/test_runner.py`, `tests/unit/test_store.py`, `tests/unit/test_planner.py`, `tests/unit/test_reconcile.py`. Keep them covering:

- Kill the runner before the copy call and before `commit_batch`, at every batch ⇒ resume yields no gaps and no duplicates; duplicates only in the documented ambiguous case.
- Album never split across a batch, including across a resume.
- `cursor_src_id` never decreases; filter-only-skipped stretches still advance it (phase 3).
- Delta after `done` picks up only new messages.
- Two runners on one job: second one refuses (fresh heartbeat).
- Migration from every previous schema version.

## Doc sync

Before finishing, run the `doc-sync` skill. Any schema change must update the SQL block in `docs/04-state-checkpoint.md`, the model paragraph here, and add a numbered migration; changes to resume/reconcile order must update both this skill and the doc.
