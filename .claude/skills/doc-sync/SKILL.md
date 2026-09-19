---
name: doc-sync
description: Keeps tgmirror's docs (docs/*.md, CLAUDE.md) and skills (.claude/skills/*) in sync with reality. Run it at the end of EVERY task, before reporting done — code, refactor, spike, bug fix, config change or docs-only. Also use when a doc and the code disagree, when a spike answers an open question, or when deciding whether a new skill or doc is needed.
---

# doc-sync

Docs and skills are the project's memory. A stale doc is worse than none: the next session trusts it. Every task ends with this pass, even if the answer is "nothing to update".

## When to run

- **Always**, as the last step of a task, before the final message.
- Mid-task if you discover the docs are wrong, or a design decision has to change.
- Other skills point here (`## Doc sync` section at their end). Following them means running this skill, not skipping it.

## The pass (5 steps, keep it fast)

1. **List what changed.** Files touched, behaviours changed, new/renamed modules, new flags or config keys, changed defaults, new error types, resolved unknowns, new decisions.
2. **Map to targets** using the table below. One change often maps to several targets.
3. **Update in place**, minimal diff. Edit the sentence that became false; do not rewrite or reformat whole files.
4. **Check cross-references**: file names, section names, flag names, config keys, decision ids (D1…) still exist and are spelled the same across docs, skills and `CLAUDE.md`.
5. **Report** one line in the final message: `Doc-sync: updated <files>` or `Doc-sync: no change needed — <reason>`. Silence is not acceptable.

## Change → target map

| Changed | Update |
|---|---|
| Gateway API, Telethon usage, strategies A/B, error mapping | `docs/01-kien-truc.md`, skill `telethon-engine` |
| Limiter, defaults, FloodWait/PeerFlood handling, hygiene | `docs/05-chong-flood.md`, skill `flood-safety`; config defaults also in `docs/02-cli-ux.md` |
| SQLite schema, transactions, resume/reconcile, delta, control flags | `docs/04-state-checkpoint.md`, skill `checkpoint-state` |
| Filter model, predicates, pushdown, album semantics | `docs/03-filters.md`, skill `filter-dsl`; CLI flags also in `docs/02-cli-ux.md` |
| Commands, flags, wizard steps, TUI, exit codes, config keys | `docs/02-cli-ux.md`, skill `cli-wizard`, README usage block if user-visible |
| New/renamed/moved module or package layout | `docs/01-kien-truc.md` (package tree), `CLAUDE.md` (Layout) |
| A decision D1–D9 changes, or a new decision | `docs/00-tong-quan.md` table + dated entry in `docs/06-lo-trinh.md` decision log (+ affected skills). **Ask the user first** — decisions are theirs |
| A spike/unknown from `docs/06-lo-trinh.md` is resolved | Record the answer in `docs/06-lo-trinh.md` (tick it, add the finding), then fix every doc/skill that hedged on it (e.g. remove "verify" caveats in `telethon-engine`) |
| A phase starts or finishes | Status checklist in `docs/06-lo-trinh.md` |
| New hard rule / invariant / convention | `CLAUDE.md` (Hard rules or Conventions) and the owning skill |
| New user-visible feature | README (goals/usage), `docs/02-cli-ux.md` |
| Dependency added/removed/changed | `CLAUDE.md` Stack, `docs/00-tong-quan.md` if it is a decision |

If nothing on the table applies and no behaviour, structure, decision or unknown changed, "no change needed" is the correct outcome. Do not invent edits.

## Keeping skills healthy

- A skill states rules and checklists; **details and rationale live in `docs/`**. Link to the doc instead of copying paragraphs, so there is one place to update.
- Each SKILL.md `description` must still say accurately *when to use it*. Update it if the skill's scope changed.
- Remove rules that no longer hold; do not leave them "for history" (history is the decision log in `docs/06-lo-trinh.md` and version control).
- Keep skills short. If one grows past roughly 150 lines, split by topic or move detail to docs.
- **New skill** only when there is a recurring area or workflow with non-obvious rules that a future task would otherwise get wrong. Otherwise put the knowledge in `docs/`. When adding one: create `.claude/skills/<name>/SKILL.md` (frontmatter `name`, `description`), add its `## Doc sync` footer, and list it in `CLAUDE.md` → Skills and in the map above.
- **New doc** only if no existing numbered doc fits; use the next number and add it to the README docs table.

## Consistency rules

- Code is the truth for *what is*; docs are the truth for *what was decided*. If they disagree, determine which is wrong: a bug → fix code; an undocumented improvement → fix docs; a decision-level conflict → ask the user.
- Language: `docs/` in Vietnamese; `README.md`, `CLAUDE.md`, skills in English. Keep technical identifiers (flags, config keys, class names) verbatim in both.
- Never put secrets, real phone numbers, `api_hash`, session strings or real channel ids in docs, skills or examples.
- Numbers in `docs/05-chong-flood.md` are defaults, not Telegram facts. Any change to a default records *why* (which `flood_log` data or test).
- Dates in the decision log are absolute (`YYYY-MM-DD`).

## Checklist before saying "done"

- [ ] Did behaviour, structure, config, defaults, a decision, or an open question change? → targets updated.
- [ ] Docs and skills that mention it no longer contradict the code or each other.
- [ ] Cross-references (paths, flags, keys, D-ids, skill names) still valid.
- [ ] `docs/06-lo-trinh.md` status/spikes/decision log current.
- [ ] Final message contains the `Doc-sync:` line.
