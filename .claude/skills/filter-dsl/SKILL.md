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
| `filters/model.py` | pydantic models: `FilterSpec`, `Rule`, ranges, `FilterError`. Validation + normalisation (lowercase hashtags, parse `2GB`, parse dates); `to_json`/`from_json` is the stored form and must round-trip |
| `filters/parser.py` | `FlagFilters` (what the flags and the wizard collect) → `from_flags`, `from_file` (YAML), `resolve` (mixing `--filter-file` with flags is `FilterMix`) |
| `filters/pushdown.py` | `plan_read(spec, cursor, pushdown=, content=) → ReadPlan(min_id, ServerFilter, complete_albums)` |
| `filters/matcher.py` | `Matcher(spec).matches(unit) -> bool`; no I/O (regex has a timeout → `FilterError`) |
| `engine/planner.py`, `batcher.py`, `preview.py` | apply the matcher to units (`Skip` for the dropped), complete albums after content pushdown, credit skips to batches, sample for the wizard preview |

## Adding a predicate — checklist

1. Add the field to `model.py` with validation and a normalised canonical form (this is what gets stored in `jobs.filters_json`; keep it backwards compatible — old stored JSON must still load, and the *serialised* form must pass the *input* validators: sizes are stored as `"<bytes>B"` because bare numbers are rejected on input).
2. Implement in `matcher.py`, operating on our `SrcMessage` dataclass (never Telethon types).
3. Decide pushdown: only if it can **only narrow safely** (never drops a message that matches). If unsure, do not push down.
4. Parser: YAML key + CLI flag (`FlagFilters`, `cli/filter_options.py`, `docs/02-cli-ux.md` shorthand table) + wizard step (`cli/wizard.py::pick_filters`) if user-facing.
5. Tests: matcher truth table, album cases (`any`/`all`/`first`), parser round-trip (YAML ↔ flags ↔ stored JSON), and add the filter to `SPECS` in `tests/integration/test_pushdown_equivalence.py` (pushdown-vs-full-scan on random fake channels with albums).
6. Update `docs/03-filters.md` and, if the wizard changed, `docs/02-cli-ux.md`.

## Rules that are easy to get wrong

- **Always re-run the client matcher after pushdown.** Telegram `search` is word/prefix based, `filter` types are approximate. Pushdown is an optimisation, not the source of truth.
- **Pushdown must never split an album.** id bounds carry an `ALBUM_MARGIN`; `media`/`search` drop the non-matching members of an album, so the planner completes each album (`complete_albums`). Never push `contains` (substring vs word search), `document`, `sticker`, `webpage`. `--no-pushdown` (job option) is the escape hatch for checking a real account.
- A predicate about something the message lacks is false, for `max` too; all predicates of a rule look at the same message; `date` is `[from, to)` UTC and `id` inclusive, both judged on the unit's first message.
- Hashtags match `MessageEntityHashtag` entities, case-insensitive; do not rely on raw substring `#`. Convert entities to plain data in the gateway so the matcher stays pure.
- Album semantics: caption/hashtag is usually on one member. Default `album: any` for include; **exclude always uses `any`** (one excluded member excludes the album).
- Regex: compile once, reject patterns that are invalid at spec-validation time (job creation), and guard against catastrophic backtracking (timeout or a safe engine) — filters may come from shared YAML files.
- Sizes/durations use explicit units; reject bare ambiguous numbers in size fields.
- Changing filters on an existing job never rewrites history: it is a new job or `--refilter` (cursor back to 0, `done` items skipped through `msg_map`).
- Filter-skipped messages are not written to `msg_map` (see `checkpoint-state`); they only bump `skipped_filter` and advance the cursor. This is different from *unsupported* messages (game, invoice, ...), which are recorded as `skipped`.
- `media` also has `geo contact game invoice`; the `topic` and `from_user` predicates only make sense for group/forum sources (`docs/03-filters.md`).

## Preview (wizard step 5)

`engine/preview.py::sample` reads the first 100 messages of the range (id/date bounds only, no content narrowing, so the sample is not biased) through the matcher and shows matched/scanned plus a few captions; `new` prints it and, on a terminal, asks to save before anything is created. Preview reads count against the read limiter (see `flood-safety`).

## Doc sync

Before finishing, run the `doc-sync` skill. A new or changed predicate updates the predicate/pushdown tables in `docs/03-filters.md`, the CLI shorthand list there and in `docs/02-cli-ux.md`, and this skill if the layer responsibilities changed.
