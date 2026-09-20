---
name: flood-safety
description: Rate limiting and anti-spam rules for tgmirror — the AIMD limiter, FloodWait/SlowMode/PeerFlood handling, daily caps, jitter and long pauses. Use when touching core/limiter.py, the runner loop, retry logic, batch sizing, or any code that makes Telegram write calls.
---

# Flood safety

Source docs: `docs/05-chong-flood.md` (numbers, rationale) and `docs/01-kien-truc.md` (runner loop, error table). Telegram publishes no exact limits: treat every number as a conservative default that `flood_log` data may change. Never write "Telegram allows N per minute" in code comments or docs.

## Invariants

1. Every Telegram call a `run` makes goes through the gateway, wrapped by `FloodGuard` (`engine/flood.py`): writes `await guard.pace(cost)` (= `limiter.acquire(cost, "write")`) before the write-ahead and go through `guard.write(...)`; reads go through `guard.reader(gateway)`. A test (`test_architecture.py`) fails if `runner.py` touches the gateway any other way. The user-started one-shot commands (login, channels, `new`, `--preview`) are the documented exception: a flood there is one sentence and exit code 3, no retry.
2. The client has `flood_sleep_threshold=0`. Every `FloodWaitError` must reach `limiter.on_flood(seconds)`. Never catch it and `await asyncio.sleep` locally.
3. A FloodWait retry re-sends the **same batch** (already `pending` in `msg_map`). Never rebuild the batch, never advance the cursor.
4. `PeerFloodError` ⇒ end the run (`failed`, `peer_flood`), exit code 3. No retry, no delay reset, no "try again in a minute".
5. Users may slow the tool down but never below `min_delay`, and never disable jitter or the daily cap without an explicit, documented flag that prints a warning.
6. Sleeping is interruptible: pause/stop/Ctrl+C must work during a flood wait (use `asyncio.wait` on a stop event, not a bare `sleep`).

**Where things live:** `core/limiter.py` (`Limiter`: AIMD, jitter, long pause, daily cap, read bucket, throttle; pure, injected `clock`/`sleep`), `engine/flood.py` (`FloodGuard`: logging, waiting, retrying, giving up), `store/limiterstate.py` (persistence; `commit_batch(limiter=...)` saves it with the batch). The runner's `_nap` raises `Interrupted` on pause/stop, so nothing sent after a request. Design and numbers: `docs/05-chong-flood.md`; what was chosen in phase 4: `docs/06-lo-trinh.md`.

## Limiter behaviour (spec)

- `delay` starts at `min_delay`; on flood `delay = min(delay*2, max_delay)`; after 20 consecutive successes `delay = max(delay*0.9, min_delay)`.
- Actual wait = `delay * uniform(1-jitter, 1+jitter)`.
- Every `long_pause_every` messages: sleep `uniform(*long_pause_range)`.
- Daily cap: when reached, the run ends in `waiting_flood` with `resume_at` = next local midnight and `reason=daily_cap`. This is not an error.
- ≥3 floods in 10 minutes ⇒ halve `batch_size` and raise `min_delay` temporarily (log it).
- State (`delay`, `sent_today`, day) is persisted in `limiter_state` so a restart does not forget lessons.
- Time comes from an injected `clock`. Tests use a fake clock; no real `sleep` in unit tests. `Limiter.acquire` may raise from `sleep` (`Interrupted`): it must record nothing before its sleep returns.

## FloodWait handling checklist

When adding or changing a code path that can raise flood errors:

- [ ] Wrapped by `FloodGuard.write` / `FloodGuard.reader` (do not hand-roll retry loops).
- [ ] Logs to `flood_log` (method, seconds, delay, batch_size).
- [ ] `seconds <= max_auto_wait` → notice, sleep `seconds + uniform(1,5)`, retry the same call (a cut read resumes after the last message it gave).
- [ ] `seconds > max_auto_wait`, or 5 floods in a row on one call → refused batch's `pending` deleted, run `waiting_flood`, `resume_at` set, exit 3 (keep waiting only with `--wait`, never past the 5-in-a-row stop).
- [ ] SlowMode handled the same way as FloodWait.

## Hygiene rules to preserve in code and docs

- Batch to reduce call count (forward up to `batch_size` ids per call).
- One account ⇒ one run at a time. Never parallelise sends across runs or pairs on the same session.
- Read calls are limited too: set `wait_time`, cache entities. Filters add reads (date-to-id lookups, one unfiltered window per album after content pushdown): they are counted as read requests (`requests` in `_GuardedReader`), so a new read path must add its request count there. `get_messages` (reading the failed messages by id for `retry`) is one paced read request per call (`_GuardedReader.get_messages`). `count` (the run's analysis) is deliberately *not* paced: one call that opens the run, like the setup reads of `begin_run`, so the read bucket still starts with the first page of messages; its FloodWait is logged and sat out like any other (`_GuardedReader.count`).
- Strategy B (phase 6): `prepare` (re-read + download) is one paced read request through `guard.reader`; the send goes through `guard.write` (`send_prepared`, `send_text`) like any write and counts against `daily_cap` per message. Only the *read* half runs ahead (`Pipeline`, `[limits] prefetch`), never the sends: still one write at a time. The daily cap, not transfer speed, is what usually decides how long a big clone takes: `status` and the first lines of a run say how many days of rest it costs (`cap_days`). There is no size-based extra delay yet (no data; revisit with `flood_log`). A FloodWait while sending repeats the same `send_prepared` with the files already on disk.
- Sending by file id (`Strategy.REFERENCE`): `reader.fetch` is one paced read request, `send_by_reference` is one write through `guard.write` per unit (counts against `daily_cap`, paced like any write). The stale-reference retry adds one `fetch`. Nothing is transferred, so the pace and the cap are what limits it.
- File transfers (strategy B) go through one `RequestBudget` (`core/pool.py`, `[limits] max_requests`, default 4, starts at 2): it grows a step per 32 clean parts and is halved by any pushback. The transport-level `HTTP 429` is *not* a `FloodWaitError`: the pool turns it into `FloodWait(60, transport=True)` (logged as `transport_429`, sat out, the transfer repeated; a download resumes from the parts it has), never a retry within seconds. Bulk data has connections of its own, never the main one. Every time the budget shrinks it logs a warning (`tgmirror.core.pool`, printed by the CLI as `[cảnh báo] ...`): a transfer that suddenly crawls is usually a budget stuck near 1. Never raise the default on a guess: 16 requests in flight drew a 429 in the spike and 8 drew one in the first real run. Individual parts are not paced or counted against `daily_cap`; the budget is their limit. A `FloodWait` from a part ends the transfer and reaches `FloodGuard` like any other.
- Pace is the gap between two *posts*, not between two uploads (2026-09-20): a big single file goes up first through `guard.transfer("upload_prepared")` (not paced, not counted), the time it took is passed to `guard.pace(cost, credit)` and counts against the **delay** only (never a long pause), and `check_cap` runs before the upload. Do not give the copy, by-id or album paths a credit: the runner's `mono` clock is what tests pin to zero so no test earns credit by accident.
- Default order is chronological (D4). Any reordering option must be opt-in.
- `tgmirror doctor` and README must say: risk is reduced, not eliminated; use an established account.

## Tests that must exist

- Fake gateway raises `FloodWait(30)` once → limiter delay doubles, batch retried once, no duplicate `msg_map` rows.
- `FloodWait(max_auto_wait+1)` → run `waiting_flood`, `resume_at` set, exit code 3.
- `PeerFlood` → run `failed`, no retry.
- 20 successes → delay decays but never below `min_delay`.
- Stop event during a flood sleep → returns promptly, state saved.
- Daily cap reached → `waiting_flood`, resumes next day (fake clock).

## Doc sync

Before finishing, run the `doc-sync` skill. Typical updates from this area: changed defaults or new `[limits]` keys (`docs/05-chong-flood.md` **and** `docs/02-cli-ux.md` config block, with the reason for the change), new error handling rows in `docs/01-kien-truc.md`, and this skill's spec section if behaviour changed.
