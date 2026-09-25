---
name: checkpoint-state
description: Durable state rules for tgmirror runs and mirrors — SQLite schema, write-ahead pending rows, one-transaction-per-batch commits, resume/reconcile after a crash, delta via the pair's cursor, and pause/stop control. Use when touching store/*, engine/runner.py, engine/runs.py, the run/retry/history commands, or any code that changes clone progress.
---

# Checkpoint & state

Source doc: `docs/04-state-checkpoint.md` (schema, rationale). This skill is the rulebook for changing it safely.

## Model in one paragraph

One SQLite file (WAL) with two things and only one of them visible. A **run** (`runs`) is one execution of `clone`/`run`: status, this run's own counters (`stats_json`), the filter it used, `cursor_from`/`cursor_to`, `control` flag, heartbeat (`updated_at`), `fail_reason`/`resume_at`. Runs are the log (`tgmirror history`). A **mirror** (`mirrors`) is the checkpoint of a source→destination pair, never shown to the user: `cursor_src_id` (highest source id fully finished), the remembered `filters_json`, `options_json` (`dst_base_id`), `account`, `src_kind`/`dst_kind` (schema v2, phase 8 — no longer forced equal, `docs/01-kien-truc.md`). `msg_map` hangs off the mirror (`mirror_id`) and maps `src_msg_id → dst_msg_id` with status `pending|done|failed|skipped` (`skipped` = unsupported type such as game/invoice/quiz, or a poll left out because `--reset-polls` was not given, with `reason='unsupported:<kind>'` (and `dst_msg_id` = the placeholder text that stands for it, if `--placeholder` posted one; counted in `stats.skipped_unsupported`), or a failed message a retry found deleted at the source, `reason='gone_from_source'`; `retry` ignores it) and the `run_id` that last *settled* the row (`finish_batch`/`confirm_pending`/`mark_gone`; a write-ahead row keeps the old one). Why the checkpoint stays: delta needs the cursor, crash safety needs write-ahead rows and `dst_base_id`, and neither can be derived from a log. Filter-skipped messages are **not** stored — they only advance the cursor and the run's `skipped_filter` counter (credited per batch: `Batch.skipped`/`Batch.upto`, committed with the batch or via `advance_cursor(extra_stats=...)`; the batcher flushes a batch after 500 skips so long stretches still save progress and hear pause/stop). Forum pairs also keep `topic_map` (`mirror_id`, `src_topic_id → dst_topic_id`, filled lazily by `engine/topics.py::TopicResolver` the first time each topic is seen) and `msg_map.src_topic_id`; creating a destination topic and saving its `topic_map` row happen back to back with nothing in between (**not** one literal SQL transaction spanning the Telegram call — rule 5 forbids that; a crash in that narrow window just means the next run recreates the topic once more). `flood_log` (by `run_id`) and `limiter_state` support tuning and persistence (`limiter_state` holds the limiter's `delay`, local `day` and `sent_today` per `mirrors.account`; `commit_batch(limiter=...)` writes it in the batch's transaction, `save_limiter_state` after a flood). `options_json` is `RunOptions` (`store/runs.py`): `batch_size`, `dst_base_id` (the destination's newest message id when the pair was first cloned, recorded once on the mirror and copied to each run, so reconcile never scans an old destination) and `pushdown` (`false` = read everything, filter locally), plus the strategy B options (`caption`, `caption_text`, `reset_polls`, `ignore_unsupported`, `placeholder`, `protected_ack`: kept on the mirror and inherited by `run`/`retry`; older JSON reads as the defaults) and two keys that belong to one run and never to the mirror (`RunOptions.for_pair` drops them): `src_last_id` (the source's newest message id when the run began, the total that `status` measures progress and ETA against) and `retry_of` (set by `tgmirror retry`: the run whose `failed` messages this run sends again). Unknown keys are ignored. `filters_json` is the canonical `FilterSpec.to_json()` (`{}` = no filter). `Store` methods take the `run_id` doing the work and find its mirror themselves.

## Rules

1. **Write-ahead**: before calling Telegram for a batch, insert its messages as `pending` (with `batch_id`) and commit.
2. **One transaction per batch result**: update `msg_map` rows (`done`+`dst_msg_id` / `failed`+`reason`), `mirrors.cursor_src_id`, `runs.cursor_to`, `runs.stats_json`, `limiter_state`, `updated_at` together. Never split these.
3. `cursor_src_id` is monotonic and only advances past a batch with no `pending` rows left. Never move it backwards, except in `Store.start_run` — (a) when `fresh=True` (`--fresh`): after the busy check, in one transaction, every `msg_map` row of the pair is deleted, the cursor goes to 0 and the new `dst_base_id` is stored (`StartedRun.forgot` = done rows forgotten; `Store.count_copied` lets the CLI warn first) — and (b) when the filter differs from the remembered one (`clone` with another filter, or `--no-filter`): one transaction sets the new `filters_json` and cursor 0 and opens the run with `cursor_from = 0`; it relies on `msg_map` to skip `done` items, and `skipped_filter` starts over because counters are per run. It refuses (`RunBusy`) a pair another process is running.
3b. **Discarding `pending` rows never forgets a message that had failed.** A write-ahead row over a `failed` one keeps its `reason` (and `run_id`); `msgmap.delete_pending` (behind `discard_batch`/`discard_pending`) puts a row that still has a `reason` back to `failed` and deletes the rest. A failed message is below the cursor, so nothing else reads it again: deleting the row silently loses it. This bites only `retry`, and only when the batch is refused, cancelled or found unsent by reconcile.
4. The store API (`Store` in `store/db.py`) exposes intent-level methods (`start_run`, `begin_batch`, `commit_batch`, `discard_batch`, `confirm_pending`, `finish`, `set_control`, `set_status`, ...), not raw SQL, to the engine. Raw SQL stays inside `store/` (a test enforces it). Every `Store` method takes its `asyncio.Lock`: the runner and its heartbeat task share one connection.
5. Use `BEGIN IMMEDIATE` for writes; keep transactions short; never hold one across a Telegram call.
6. Migrations: `schema.sql` is version 1 (the runs + mirrors redesign was folded into it before the first release, with no migration from the old `jobs` tables), and `PRAGMA user_version` holds the version; every schema change appends a numbered script to `default_migrations()` in `store/db.py` (never edit an old one) and gets a test that upgrades a DB created by the previous version (see `test_upgrading_a_database_made_by_the_previous_version`).
7. Timestamps are UTC ISO-8601 strings; `limiter_state.day` uses local date (documented, because "daily cap" is a user-facing concept).

## Resume algorithm (must stay in this order)

1. `engine/runs.py::begin_run` → `Store.start_run` opens the run and takes the pair (one transaction: status `running` + heartbeat; refused with `RunBusy` while another run of the pair is `running`/`paused` with a heartbeat younger than 2 minutes, unless `--force-takeover`; a run with an older heartbeat is closed as `failed('interrupted')`). It also refuses, before connecting, a pair whose latest run is `waiting_flood` before `resume_at` or `failed('peer_flood')` within 24 h (`check_runnable`).
2. Reconcile any `pending` rows (`engine/reconcile.py` decides, `engine/runner.py` reads and writes):
   - re-read the pending source messages (media kind, album structure) and the destination tail: non-service messages with id > `max(dst_msg_id of done rows, options.dst_base_id)`;
   - count, media kinds and album structure all match ⇒ `confirm_pending`: mark `done` with the found dst ids and advance the cursor, one transaction;
   - no new dst messages ⇒ delete the `pending` rows (the batch will be re-sent);
   - anything else (including a pending message that vanished from the source) ⇒ warn, delete and re-send (favour "no gap" over "no duplicate").
3. Iterate `iter_messages(src, min_id=run.cursor_from)` (ascending) with the run's filters (`plan_read`: pushdown, or a full scan with `options.pushdown = false`).
4. Skip any unit that already has a `done` row; a batch with nothing left only advances the cursor (and books its filter skips).

## What a copy call leaves behind

- Returned normally: `dst_id` ⇒ `done`; `None` ⇒ `failed('not_copied')` (Telegram made no message; not "unknown").
- `PerMessage`: delete the batch's `pending`, resend unit by unit; a unit that still fails ⇒ `failed`.
- `FloodWait` within `max_auto_wait`: wait and repeat the same call, `pending` stays. `FloodWait` too long or 5 in a row, `PeerFlood`, `NoPermission`, `ForwardsRestricted`, other rejections, stop/Ctrl+C during a flood wait: nothing was created ⇒ delete the batch's `pending`, then end the run. A *pause* during a flood wait does not cut it short: it is honoured after the wait, at the batch boundary. The daily cap fires before the write-ahead (nothing pending): run `waiting_flood`, `fail_reason='daily_cap'`.
- Strategy B: one unit per batch, so a pending batch is one unit and reconcile compares it as usual. `prepare` runs **before** the write-ahead, so a refused fetch (`PerMessage`) leaves nothing pending: the unit is written `failed` at once. A unit left out on purpose (`DROP`) is recorded with `begin_batch` + `commit_batch` and no Telegram call between. A big single file goes up **before** the write-ahead (`upload_prepared`, no message exists, nothing pending; a kill there leaves nothing to reconcile), then `pace`, write-ahead, and `send_prepared` only posts; a `FloodWait` on the post repeats it with the file already uploaded, on the upload it repeats the upload. Albums and small files still upload inside `send_prepared`. A `FloodWait` while sending repeats the same call with the same downloaded files. The daily cap is checked before the upload (`check_cap`).
- `Transient` (connection cut after sending): outcome unknown ⇒ **keep** `pending`, run `failed('transient')`; the next run reconciles.

## Delta

Cloning the same pair again (`clone` or `run`) is a new run on the same mirror: the stored filter and cursor are used (no filter given ⇒ the remembered one), no new messages ⇒ exit 0 quickly, the counters are the new run's own. Another filter ⇒ cursor 0 and skip-`done` (rule 3). Edit/delete sync is out of scope for v1 — do not half-implement it.

## Retry

`tgmirror retry [n]` starts an ordinary run with `options.retry_of = n` (`RunRequest.retry_of`). Only the `Unit` source of the runner differs (`Runner._failed_units`): the `failed` ids of run `n` (`run_failures(n)`, snapshot after reconcile), read by id with `get_messages` in chunks of 100 (`planner.failed_units`: albums kept whole, only their failed members; `Gone` for ids deleted at the source → `Store.mark_gone`), then the same gate → `pace` → write-ahead → `guard.write` → `commit_batch`. The source cursor and the filter are untouched (`MAX` keeps the cursor; the read never sees the filter). Rows sent again get `run_id` = the retry, so a message that fails again belongs to it and `retry` without a number goes on; a stopped retry is continued by `retry n`, not `run` (`execute` prints the right hint). Never send a `done` row again.

## Control

- `tgmirror pause|stop|run` (another terminal) only write `runs.control` (`pause`, `stop`, `none`), and only on a `running`/`paused` run. The runner polls it between batches (one cheap `SELECT`) and, for stop, during sleeps (`Interrupted`).
- The keys `p`/`r`/`q` and Ctrl+C reach the same runner through `RunControl` (`engine/runner.py`; thread-safe). A resume request also clears a `pause` left in the store.
- **Stop / Ctrl+C**: finish the current batch, commit, `finish(STOPPED)`, clear `control`. A second Ctrl+C exits immediately; resume must cope via reconcile.
- **Pause is in place** (`Runner._hold`): after the current batch the run becomes `paused` (the process, its heartbeat and the terminal stay alive, nothing is sent) until resumed or stopped. It is honoured at batch boundaries, never by cutting a sleep short. A `paused` run still holds its pair (`RunBusy` for a second run).

## Tests that must exist (FakeGateway)

Strategy B (`tests/integration/test_runner_reupload.py`): one send per unit and never a forward, an album stays one album, files kept until sent and gone afterwards (also after a stop, a kill and a refused fetch), the next unit downloaded while this one uploads and never more than the window allows, a stop noticed while a download is in flight, polls/games/quizzes per flag, a placeholder id kept on the skipped row, kill before/after the upload resumes without a duplicate. Existing since phase 2: `tests/integration/test_runner.py`, `tests/unit/test_store.py`, `tests/unit/test_planner.py`, `tests/unit/test_reconcile.py`. Keep them covering:

- Kill the runner before the copy call and before `commit_batch`, at every batch ⇒ resume yields no gaps and no duplicates; duplicates only in the documented ambiguous case.
- Album never split across a batch, including across a resume.
- `cursor_src_id` never decreases (except a changed filter in `start_run`); filter-only-skipped stretches still advance it and a stop gets through them; `skipped_filter` is counted once across runs (each run counts its own) and through the `PerMessage` split (`tests/integration/test_runner_filters.py`).
- Delta after `done` picks up only new messages; the same filter again is delta, another one reads from the start without copying twice.
- Two runs of one pair: the second refuses (fresh heartbeat); a dead run is logged `failed('interrupted')`; a paused run holds its pair.
- Pause in place: held after the batch in flight, resumed by `RunControl` or by the store flag, stop while held.
- Migration from every previous schema version.
- Retry (`tests/integration/test_runner_retry.py`): only the failed ids are sent, read by id, cursor and filter untouched; kill before/after the copy and a refused (long flood) or stopped retry lose no failed message; albums whole across the 100-id read boundary; deleted-at-source set aside; a retry of a retry.

## Doc sync

Before finishing, run the `doc-sync` skill. Any schema change must update the SQL block in `docs/04-state-checkpoint.md`, the model paragraph here, and add a numbered migration; changes to resume/reconcile order must update both this skill and the doc.
