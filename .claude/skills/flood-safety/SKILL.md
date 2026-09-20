---
name: flood-safety
description: Rate limiting and anti-spam rules for tgmirror — the AIMD limiter, FloodWait/SlowMode/PeerFlood handling, daily caps, jitter and long pauses. Use when touching core/limiter.py, the runner loop, retry logic, batch sizing, or any code that makes Telegram write calls.
---

# Flood safety

Source docs: `docs/05-chong-flood.md` (numbers, rationale) and `docs/01-kien-truc.md` (runner loop, error table). Telegram publishes no exact limits: treat every number as a conservative default that `flood_log` data may change. Never write "Telegram allows N per minute" in code comments or docs.

## Invariants

1. Every Telegram call goes through the gateway, and every write call awaits `limiter.acquire(cost, kind)` first. No exceptions, including "just one quick call" in the wizard.
2. The client has `flood_sleep_threshold=0`. Every `FloodWaitError` must reach `limiter.on_flood(seconds)`. Never catch it and `await asyncio.sleep` locally.
3. A FloodWait retry re-sends the **same batch** (already `pending` in `msg_map`). Never rebuild the batch, never advance the cursor.
4. `PeerFloodError` ⇒ stop the job (`failed`, `peer_flood`), exit code 3. No retry, no delay reset, no "try again in a minute".
5. Users may slow the tool down but never below `min_delay`, and never disable jitter or the daily cap without an explicit, documented flag that prints a warning.
6. Sleeping is interruptible: pause/stop/Ctrl+C must work during a flood wait (use `asyncio.wait` on a stop event, not a bare `sleep`).

**Phase 2 state:** `core/limiter.py` is an interim `Limiter.acquire(cost)` that only spaces batches (`min_delay` ± jitter, a long pause every `long_pause_every` messages); the runner's sleep is interruptible. There is no AIMD, daily cap, `limiter_state` or auto-wait yet: a FloodWait stops the job (`waiting_flood`, `resume_at`, a `flood_log` row, exit code 3; `run` refuses before `resume_at`) and a PeerFlood fails it (`run` refuses for 24 h). The one-shot phase 1 calls (list dialogs, get entity, create channel, login) map FloodWait/PeerFlood to an error and exit code 3 without retry. Phase 4 replaces the body of `Limiter` and the flood branch of the runner; keep the `acquire` interface and the invariants above (`docs/06-lo-trinh.md`, "Phase 2 — ghi chú").

## Limiter behaviour (spec)

- `delay` starts at `min_delay`; on flood `delay = min(delay*2, max_delay)`; after 20 consecutive successes `delay = max(delay*0.9, min_delay)`.
- Actual wait = `delay * uniform(1-jitter, 1+jitter)`.
- Every `long_pause_every` messages: sleep `uniform(*long_pause_range)`.
- Daily cap: when reached, the job goes to `waiting_flood` with `resume_at` = next local midnight and `reason=daily_cap`. This is not an error.
- ≥3 floods in 10 minutes ⇒ halve `batch_size` and raise `min_delay` temporarily (log it).
- State (`delay`, `sent_today`, day) is persisted in `limiter_state` so a restart does not forget lessons.
- Time comes from an injected `Clock`. Tests use a fake clock; no real `sleep` in unit tests.

## FloodWait handling checklist

When adding or changing a code path that can raise flood errors:

- [ ] Wrapped by the shared `with_flood_handling` helper (do not hand-roll retry loops).
- [ ] Logs to `flood_log` (method, seconds, delay, batch_size).
- [ ] `seconds <= max_auto_wait` → countdown UI, sleep `seconds + uniform(1,5)`, retry same batch.
- [ ] `seconds > max_auto_wait` → job `waiting_flood`, `resume_at` set, state saved, exit 3 (or keep waiting only with `--wait`).
- [ ] SlowMode handled the same way as FloodWait.

## Hygiene rules to preserve in code and docs

- Batch to reduce call count (forward up to `batch_size` ids per call).
- One account ⇒ one job at a time. Never parallelise sends across jobs on the same session.
- Read calls are limited too: set `wait_time`, cache entities. Filters add reads (date-to-id lookups, one unfiltered window per album after content pushdown); phase 4 must put them through the limiter.
- Default order is chronological (D4). Any reordering option must be opt-in.
- `tgmirror doctor` and README must say: risk is reduced, not eliminated; use an established account.

## Tests that must exist

- Fake gateway raises `FloodWait(30)` once → limiter delay doubles, batch retried once, no duplicate `msg_map` rows.
- `FloodWait(max_auto_wait+1)` → job `waiting_flood`, `resume_at` set, exit code 3.
- `PeerFlood` → job `failed`, no retry.
- 20 successes → delay decays but never below `min_delay`.
- Stop event during a flood sleep → returns promptly, state saved.
- Daily cap reached → `waiting_flood`, resumes next day (fake clock).

## Doc sync

Before finishing, run the `doc-sync` skill. Typical updates from this area: changed defaults or new `[limits]` keys (`docs/05-chong-flood.md` **and** `docs/02-cli-ux.md` config block, with the reason for the change), new error handling rows in `docs/01-kien-truc.md`, and this skill's spec section if behaviour changed.
