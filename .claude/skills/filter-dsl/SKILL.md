---
name: filter-dsl
description: How to add, change or debug message filters in tgmirror — the include/exclude/date/id filter model, predicates (media, hashtag, regex, size, ...), album semantics, server-side pushdown safety, YAML and CLI-flag parsing. Use when touching filters/*, adding a predicate, or changing what the wizard offers for filtering.
---

# Filter DSL

Source doc: `docs/03-filters.md` (semantics, predicate table, pushdown table). Update it in the same change as the code.

## Semantics to preserve

A unit (single message or whole album) is cloned iff
`global_ok AND (include is empty OR any include-rule matches) AND NOT any exclude-rule matches`.
Inside a rule, predicates are ANDed; rules are ORed. Service messages are always skipped. `date`/`id` are global.

## Layers

| File | Job |
|---|---|
| `filters/model.py` | pydantic models: `FilterSpec`, `Rule`, predicate types. Validation + normalisation (lowercase hashtags, parse `2GB`, parse dates) |
| `filters/parser.py` | YAML file and CLI flags → `FilterSpec`. Mixing `--filter-file` with flags is an error |
| `filters/pushdown.py` | `FilterSpec → ServerFilter(filter=..., search=..., min_id, max_id, offset_date)` |
| `filters/matcher.py` | Pure function `matches(spec, unit) -> bool`; no I/O |

## Adding a predicate — checklist

1. Add the field to `model.py` with validation and a normalised canonical form (this is what gets stored in `jobs.filters_json`; keep it backwards compatible — old stored JSON must still load).
2. Implement in `matcher.py`, operating on our `SrcMessage` dataclass (never Telethon types).
3. Decide pushdown: only if it can **only narrow safely** (never drops a message that matches). If unsure, do not push down.
4. Parser: YAML key + CLI flag (`docs/02-cli-ux.md` shorthand table) + wizard step if user-facing.
5. Tests: matcher truth table, album cases (`any`/`all`/`first`), parser round-trip (YAML ↔ flags ↔ stored JSON), pushdown-vs-full-scan equivalence on a fake channel.
6. Update `docs/03-filters.md` and, if the wizard changed, `docs/02-cli-ux.md`.

## Rules that are easy to get wrong

- **Always re-run the client matcher after pushdown.** Telegram `search` is tokenised/substring, `filter` types are approximate. Pushdown is an optimisation, not the source of truth.
- Hashtags match `MessageEntityHashtag` entities, case-insensitive; do not rely on raw substring `#`. Convert entities to plain data in the gateway so the matcher stays pure.
- Album semantics: caption/hashtag is usually on one member. Default `album: any` for include; **exclude always uses `any`** (one excluded member excludes the album).
- Regex: compile once, reject patterns that are invalid at spec-validation time (job creation), and guard against catastrophic backtracking (timeout or a safe engine) — filters may come from shared YAML files.
- Sizes/durations use explicit units; reject bare ambiguous numbers in size fields.
- Changing filters on an existing job never rewrites history: it is a new job or `--refilter` (cursor back to 0, `done` items skipped through `msg_map`).
- Filter-skipped messages are not written to `msg_map` (see `checkpoint-state`); they only bump `skipped_filter` and advance the cursor. This is different from *unsupported* messages (game, invoice, ...), which are recorded as `skipped`.
- `media` also has `geo contact game invoice`; the `topic` and `from_user` predicates only make sense for group/forum sources (`docs/03-filters.md`).

## Preview (wizard step 5)

Sample the first ~100 messages through pushdown + matcher and show matched/total plus a few example captions. Preview reads count against the read limiter (see `flood-safety`).

## Doc sync

Before finishing, run the `doc-sync` skill. A new or changed predicate updates the predicate/pushdown tables in `docs/03-filters.md`, the CLI shorthand list there and in `docs/02-cli-ux.md`, and this skill if the layer responsibilities changed.
