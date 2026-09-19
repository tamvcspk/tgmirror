---
name: cli-wizard
description: Conventions for the tgmirror CLI — adding Typer commands, wizard (questionary) steps, Rich progress/TUI, exit codes, and keeping interactive and non-interactive paths identical. Use when touching cli/*, ui/*, or adding/changing any `tgmirror` command or flag.
---

# CLI & wizard

Source doc: `docs/02-cli-ux.md` (command table, wizard flow, exit codes, config). Keep it in sync with any change here.

## Structure

- `cli/app.py`: Typer app, registers commands from `cli/commands/*.py` (one module per command group). Its callback puts a `Runtime` in `ctx.obj`.
- `cli/runtime.py`: `Runtime` = paths, `connect(config)` (async context manager giving `Connection(auth, gateway)`), prompter, `interactive` (TTY), env. Commands use only this, so tests inject fakes with `CliRunner.invoke(app, args, obj=runtime)` (`make_runtime` fixture in `tests/conftest.py`). `authorized(rt)` is the connect-and-require-login helper.
- `cli/errors.py`: `run(rt, coro)` runs a command's coroutine (`asyncio.run`) and turns `TgMirrorError`s into one sentence + exit code (`describe`, `exit_code`); `UsageProblem(key, **params)` is a usage error (exit 2) that already names its message key. Add a new error type there together with its message.
- `cli/wizard.py`: questionary prompts only. **Wizard functions collect values and return a spec; they contain no business logic and never talk to Telegram directly** (they receive already-fetched data, e.g. the channel list).
- `ui/`: `messages.py` (all user strings), `prompts.py` (async `Prompter` protocol + questionary implementation; async because prompts happen between Telegram calls inside a running loop; `ScriptedPrompter` in `tests/fakes.py`), `tables.py` (Rich table / `--json`), later `progress.py`.
- Business logic lives in `engine/` and `store/`. A command is: parse args → build a spec → call one function → render the result.

## The parity rule

Every wizard outcome must be expressible with flags/YAML, and both paths call the same `create_job(spec)`. When adding a wizard step:

1. Add the flag(s) (and YAML key if it belongs to filters) first.
2. Add the wizard step that fills the same field.
3. Add a test that creates a job via flags and via a scripted wizard (`questionary` can be fed with `pytest` monkeypatch / prompt-toolkit input pipes) and asserts equal specs.

`--yes` skips confirmations only; it never skips safety prompts that have their own explicit flag (e.g. the reupload-on-protected-channel confirmation, see `telethon-engine`). Same for unsupported message types under `--mode reupload`: `--ignore-unsupported` (game/invoice/unanswered quiz; `--placeholder` implies it and also posts a stub text) and `--reset-polls` must be given explicitly; without them the wizard asks and non-interactive runs exit `2` (`docs/02-cli-ux.md`, "Tin đặc thù").

## Adding a command — checklist

- [ ] Name is a verb or noun consistent with the table in `docs/02-cli-ux.md`; job commands take `<job>` (id or name).
- [ ] `--help` text is concrete, with one example.
- [ ] Non-interactive friendly: no prompt unless a TTY is attached and `--yes`/all needed flags aren't given; when there is no TTY and info is missing → exit code 2 with the missing flag named.
- [ ] Exit codes: `0` ok · `1` general error · `2` usage · `3` stopped by flood/peer_flood/daily cap · `4` missing permission · `130` interrupted.
- [ ] Machine-readable output via `--json` for `status`, `jobs`, `channels`.
- [ ] Errors are human sentences with the next step ("Bạn không phải admin của kênh nguồn; ..."), not tracebacks. Tracebacks only with `--debug`.
- [ ] Never prints secrets (`api_hash`, session, phone, login code). Phone/code prompts use hidden input where applicable.
- [ ] Tests with Typer's `CliRunner` and `FakeGateway`.

## TUI conventions

- Rich `Live` layout: header (job, mode, current delay), progress bar, counters (done, failed, skipped by filter, floods), key hints.
- Keys: `p` pause/resume, `q` save & quit. No key lowers delay below `min_delay`.
- FloodWait shows a visible countdown and the reason; never a frozen screen.
- Non-TTY: plain line logging every N seconds, no ANSI.

## Language and output

User-facing strings are Vietnamese by default with English fallbacks kept in one `ui/messages.py` (`t(key, **params)`, `TGMIRROR_LANG=en` switches); code, identifiers and comments are English. A key must exist in both tables (tests check keys, placeholders and that every key used in the source exists). Tests run with `TGMIRROR_LANG=en`.

- Real runs force stdout/stderr to UTF-8 (`_use_utf8_output` in `cli/app.py`): on Windows a redirected stream uses cp1252, which crashes Rich and garbles Vietnamese.
- Wrap user-provided text (channel titles) in `rich.text.Text` before putting it in a Rich table; a title like `[pro]` is otherwise read as markup.
- Tables must stay readable at 80 columns (the phase 1 `channels` table was unreadable before it was compacted); test with `CliRunner`, which is 80 wide.

## Doc sync

Before finishing, run the `doc-sync` skill. A new or changed command, flag, wizard step, exit code or config key updates `docs/02-cli-ux.md` (and the README usage block if user-visible), plus this skill's checklist if a convention changed.
