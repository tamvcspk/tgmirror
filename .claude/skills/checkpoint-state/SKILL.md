---
name: checkpoint-state
description: Durable state rules for tgmirror jobs — SQLite schema, write-ahead pending rows, one-transaction-per-batch commits, resume/reconcile after a crash, delta sync via cursor, and pause/stop control flags. Use when touching store/*, engine/runner.py, resume/sync/retry commands, or any code that changes job progress.
---

# Checkpoint & state

Source doc: `docs/04-state-checkpoint.md` (schema, rationale). This skill is the rulebook for changing it safely.

## Model in one paragraph

One SQLite file (WAL). `jobs` holds config + status + `cursor_src_id` (highest source id fully finished) + control flag. `msg_map` maps `src_msg_id → dst_msg_id` per job with status `pending|done|failed|skipped` (`skipped` = unsupported type such as game/invoice, with `reason='unsupported:<kind>'`; `retry` ignores it). Filter-skipped messages are **not** stored — they only advance the cursor and a counter. Forum jobs also keep `topic_map` (`src_topic_id → dst_topic_id`) and `msg_map.src_topic_id`; creating a destination topic and saving its `topic_map` row happen in one transaction. `flood_log` and `limiter_state` support tuning and persistence.

## Rules

1. **Write-ahead**: before calling Telegram for a batch, insert its messages as `pending` (with `batch_id`) and commit.
2. **One transaction per batch result**: update `msg_map` rows (`done`+`dst_msg_id` / `failed`+`reason`), `cursor_src_id`, `stats_json`, `limiter_state`, `updated_at` together. Never split these.
3. `cursor_src_id` is monotonic and only advances past a batch with no `pending` rows left. Never move it backwards (use `--refilter`, which restarts from 0 and relies on `msg_map` to skip `done` items).
4. The store API exposes intent-level methods (`begin_batch`, `commit_batch`, `mark_waiting`, `set_control`), not raw SQL, to the engine. Raw SQL stays inside `store/`.
5. Use `BEGIN IMMEDIATE` for writes; keep transactions short; never hold one across a Telegram call.
6. Migrations: `schema.sql` + a `schema_version` pragma/table; every schema change gets a numbered migration and a test that upgrades a DB created by the previous version.
7. Timestamps are UTC ISO-8601 strings; `limiter_state.day` uses local date (documented, because "daily cap" is a user-facing concept).

## Resume algorithm (must stay in this order)

1. Take the job lock (status `running` + heartbeat; takeover only if the heartbeat is older than 2 minutes or `--force-takeover`).
2. Reconcile any `pending` rows:
   - read the destination tail: messages with id > max `dst_msg_id` among `done` rows;
   - count and media types match the pending batch ⇒ mark `done` with the found dst ids;
   - no new dst messages ⇒ delete the `pending` rows (the batch will be re-sent);
   - ambiguous ⇒ warn, prefer re-send (favour "no gap" over "no duplicate"), log it.
3. Iterate `iter_messages(src, min_id=cursor_src_id, reverse=True)` with the stored filters.
4. Skip any `src_msg_id` already `done`.

## Delta sync

Same as resume with a `done` job: use the stored filters and cursor; no new messages ⇒ exit 0 quickly. Edit/delete sync is out of scope for v1 — do not half-implement it.

## Control flags

- `tgmirror pause|stop` only writes `jobs.control`. The runner polls it between batches (one cheap `SELECT`) and during sleeps.
- On pause/stop the runner finishes the current batch, commits, sets `status`, clears `control`.
- Ctrl+C = same as stop. A second Ctrl+C exits immediately; resume must cope via reconcile.

## Tests that must exist (FakeGateway)

- Kill the runner (raise between `begin_batch` and `commit_batch`) at every step ⇒ resume yields no gaps, and duplicates only in the documented ambiguous case.
- Album never split across a batch, including across a resume.
- `cursor_src_id` never decreases; filter-only-skipped stretches still advance it.
- Delta after `done` picks up only new messages.
- Two runners on one job: second one refuses (fresh heartbeat).
- Migration from every previous schema version.

## Doc sync

Before finishing, run the `doc-sync` skill. Any schema change must update the SQL block in `docs/04-state-checkpoint.md`, the model paragraph here, and add a numbered migration; changes to resume/reconcile order must update both this skill and the doc.
