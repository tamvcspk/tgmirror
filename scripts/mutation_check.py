r"""Manual mutation check: break one mechanism at a time and see whether the tests notice.

    .venv\Scripts\python.exe scripts\mutation_check.py            # all mutations
    .venv\Scripts\python.exe scripts\mutation_check.py --list
    .venv\Scripts\python.exe scripts\mutation_check.py --only "poll"   # names containing text

It never touches your working tree: it copies ``src/``, ``tests/`` and ``pyproject.toml`` to a
temporary folder and mutates the copy (``PYTHONPATH`` makes the copy win over the editable
install). So Ctrl+C, a crash or a hung test leaves nothing broken behind, and you can keep working.

Every run has two limits, so nothing can hang the check: ``--timeout`` (per pytest run, seconds) and
pytest-timeout's ``--timeout=30`` per test. A mutation whose tests hang counts as caught: a test
that cannot finish is a test that notices.

Each line is printed as soon as it is known. A ``SURVIVED`` line is a mechanism no test protects
(or a mutation that changes nothing observable): read it, do not just make it green.
"""

# ruff: noqa: E501 - the mutation patterns must equal the source text, long lines included

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TESTS = [
    "tests/integration/test_runner_reupload.py",
    "tests/unit/test_reupload.py",
    "tests/unit/test_cli_reupload.py",
    "tests/unit/test_telethon_reupload.py",
    "tests/unit/test_architecture.py",
    "tests/integration/test_runner_analysis.py",
    "tests/unit/test_pool.py",
    "tests/unit/test_telethon_pool.py",
    "tests/unit/test_telethon_transfer.py",
    "tests/unit/test_transfer.py",
    "tests/unit/test_status.py",
    "tests/unit/test_limiter.py",
    "tests/integration/test_runner_split.py",
]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    old: str
    new: str


MUTATIONS = [
    Mutation(
        "no room reserved before a download",
        "src/tgmirror/engine/runner.py",
        "        await window.reserve(size)\n        try:",
        "        try:",
    ),
    Mutation(
        "downloads not deleted after a unit",
        "src/tgmirror/engine/reupload.py",
        "                path.unlink(missing_ok=True)",
        "                pass",
    ),
    Mutation(
        "a poll is sent without --reset-polls",
        "src/tgmirror/engine/reupload.py",
        "        if not options.reset_polls:\n            return Action(ActionKind.DROP",
        "        if False:\n            return Action(ActionKind.DROP",
    ),
    Mutation(
        "a game is dropped silently",
        "src/tgmirror/engine/reupload.py",
        "    raise UnsupportedMedia(kind, msg_id)",
        "    return Action(ActionKind.DROP, reason)",
    ),
    Mutation(
        "D3: confirmation skipped on clone",
        "src/tgmirror/cli/commands/clone.py",
        "        if plan.protected and downloads:\n            await self._confirm_protected(plan)",
        "        if False:\n            await self._confirm_protected(plan)",
    ),
    Mutation(
        "D3: --yes answers the question",
        "src/tgmirror/cli/commands/clone.py",
        "        if self._o.admin_ack:\n            return\n        if not self._interactive:",
        "        if self._o.admin_ack or True:\n            return\n        if not self._interactive:",
    ),
    Mutation(
        "D3: a run does not read the source again",
        "src/tgmirror/engine/runs.py",
        "    if may_reupload(request.mode, request.caption):\n        protected = await check_source(",
        "    if False:\n        protected = await check_source(",
    ),
    Mutation(
        "D3: a protected source is copied without the user's word",
        "src/tgmirror/engine/runs.py",
        "\n    raise NeedsAcknowledgement(current.title)",
        "\n    return",
    ),
    Mutation(
        "D3: a non-admin may copy a protected source without the statement",
        "src/tgmirror/engine/runs.py",
        "    if not current.is_admin:\n        raise SourceRestricted(current)",
        "    if False:\n        raise SourceRestricted(current)",
    ),
    Mutation(
        "D3: the statement is not needed by a non-admin at clone time",
        "src/tgmirror/engine/endpoints.py",
        '        elif take_responsibility:\n            warnings.append("noforwards_unadministered")\n        else:\n            raise SourceRestricted(src)',
        '        else:\n            warnings.append("noforwards_unadministered")',
    ),
    Mutation(
        "D3: the statement is ignored when the pair runs again",
        "src/tgmirror/engine/runs.py",
        "    if request.protected_ack:\n        return True\n",
        "",
    ),
    Mutation(
        "D3: run forgets the confirmation",
        "src/tgmirror/cli/commands/run.py",
        "                protected_ack=target.options.protected_ack,\n",
        "",
    ),
    Mutation(
        "D3: clone does not record the confirmation",
        "src/tgmirror/cli/commands/clone.py",
        "            base = replace(base, protected_ack=True)",
        "            pass",
    ),
    Mutation(
        "a reuploaded unit may share a batch",
        "src/tgmirror/engine/batcher.py",
        "        if strategy is not Strategy.COPY:  # nothing can join it\n"
        "            yield emit()\n"
        "            current, count, skipped, upto = [], 0, 0, 0\n"
        "            strategy = Strategy.COPY",
        "        pass",
    ),
    Mutation(
        "a strategy change does not end the batch",
        "src/tgmirror/engine/batcher.py",
        "wanted is not strategy or count",
        "count",
    ),
    Mutation(
        "a flood while fetching is not waited out",
        "src/tgmirror/engine/flood.py",
        '                await self._guard.flooded("prepare", exc, give_up=floods >= MAX_FLOODS_PER_CALL)',
        "                raise",
    ),
    Mutation(
        "stale downloads not cleared at the start",
        "src/tgmirror/engine/runner.py",
        "        await self._clear_tmp()  # what a killed run left behind\n",
        "",
    ),
    Mutation(
        "a refused fetch is not recorded as failed",
        "src/tgmirror/engine/runner.py",
        "        if ready.rejected is not None:",
        "        if False:",
    ),
    Mutation(
        "a dropped unit is not settled",
        "src/tgmirror/engine/runner.py",
        "            await self._settle(run, todo, left_out(todo.units[0], ready.action))\n"
        "            return True",
        "            return True",
    ),
    Mutation(
        "the consumer never notices a dead download task",
        "src/tgmirror/engine/reupload.py",
        "            if producer.done():\n                if producer.cancelled():",
        "            if False:\n                if producer.cancelled():",
    ),
    Mutation(
        "stop while waiting is ignored",
        "src/tgmirror/engine/runner.py",
        "                raise Interrupted  # the download in flight is dropped with the pipeline",
        "                pass",
    ),
    Mutation(
        "in-flight download leaks on cancel",
        "src/tgmirror/engine/reupload.py",
        "                except BaseException:  # cancelled while waiting for room: nobody else has it\n"
        "                    await self.finish(ready)\n"
        "                    raise",
        "                except BaseException:\n                    raise",
    ),
    Mutation(
        "append adds to a caption that does not exist",
        "src/tgmirror/core/telethon_gateway.py",
        'return (f"{text}\\n\\n{policy.text}" if text else text), list(entities)',
        'return (f"{text}\\n\\n{policy.text}" if text else policy.text), list(entities)',
    ),
    Mutation(
        "a video is forced to a file",
        "src/tgmirror/core/telethon_gateway.py",
        '"force_document": kind is MediaKind.DOCUMENT,',
        '"force_document": True,',
    ),
    Mutation(
        "an album of songs or videos is forced to files",
        "src/tgmirror/core/telethon_gateway.py",
        "as_documents = all(media_kind(i.message) is MediaKind.DOCUMENT for i in items)",
        "as_documents = all(i.message.document is not None for i in items)",
    ),
    Mutation(
        "markdown parsing left on",
        "src/tgmirror/core/telethon_gateway.py",
        "                parse_mode=None,  # the entities are the formatting: nothing to parse as markdown\n",
        "",
    ),
    Mutation(
        "utf-16 offsets replaced by code points",
        "src/tgmirror/core/telethon_gateway.py",
        '    return _units16(text)[2 * offset : 2 * (offset + length)].decode("utf-16-le", "ignore")',
        "    return text[offset : offset + length]",
    ),
    Mutation(
        "a finished download is not reused",
        "src/tgmirror/core/telethon_gateway.py",
        "        if await asyncio.to_thread(final.exists):\n            return final\n"
        '        part = tmp / f"{message.id}.part"',
        '        part = tmp / f"{message.id}.part"',
    ),
    Mutation(
        "placeholder id not kept on the skipped row",
        "src/tgmirror/engine/reupload.py",
        "        return left_out(unit, action, await gateway.send_text(dst, action.text))",
        "        await gateway.send_text(dst, action.text)\n        return left_out(unit, action)",
    ),
    Mutation(
        "D3: a protected source is sent by file id",
        "src/tgmirror/engine/runner.py",
        "by_reference=not run.options.src_protected",
        "by_reference=True",
    ),
    Mutation(
        "a stale reference is not refreshed",
        "src/tgmirror/engine/reupload.py",
        "            if attempt == 0:\n                prepared = await reader.fetch(src, unit)",
        "            pass",
    ),
    Mutation(
        "media Telegram refuses by id is not sent the long way",
        "src/tgmirror/engine/reupload.py",
        "    on_fallback()\n",
        "    raise FileRefExpired('no way out')\n    on_fallback()\n",
    ),
    Mutation(
        "a bigger file than Telegram said is not booked",
        "src/tgmirror/engine/runner.py",
        "                await window.grow(actual - size)",
        "                pass",
    ),
    Mutation(
        "a file without a size counts for nothing",
        "src/tgmirror/engine/reupload.py",
        "UNKNOWN_SIZE = 1 << 20",
        "UNKNOWN_SIZE = 0",
    ),
    Mutation(
        "a range is counted as the whole chat",
        "src/tgmirror/core/telethon_gateway.py",
        "        return max(from_low - above_high, 0)",
        "        return total",
    ),
    Mutation(
        "the count is not capped by the id span",
        "src/tgmirror/engine/runner.py",
        "            total = min(total, max(head - plan.min_id, 0))",
        "            pass",
    ),
    Mutation(
        "an error of the analysis fails the run",
        "src/tgmirror/engine/runner.py",
        "        except GatewayError:\n            return run",
        "        except GatewayError:\n            raise",
    ),
    Mutation(
        "messages the pair already has are not counted as handled",
        "src/tgmirror/engine/runner.py",
        "already=batch.already + passed)",
        "already=batch.already)",
    ),
    Mutation(
        "pool: pushback does not shrink the budget",
        "src/tgmirror/core/pool.py",
        "        self._limit = max(1, self._limit // 2)",
        "        pass",
    ),
    Mutation(
        "pool: a FloodWait is swallowed and the part repeated",
        "src/tgmirror/core/pool.py",
        "                except FloodWait as exc:\n"
        "                    budget.pressure(str(exc))\n"
        "                    raise\n",
        "                except FloodWait as exc:\n                    budget.pressure(str(exc))\n",
    ),
    Mutation(
        "pool: the other workers are not cancelled when one fails",
        "src/tgmirror/core/pool.py",
        "        for task in tasks:\n            task.cancel()",
        "        for task in tasks:\n            pass",
    ),
    Mutation(
        "pool: a half-made download is left on the disk",
        "src/tgmirror/core/telethon_gateway.py",
        "            except BaseException:  # never leave a big half-made file on the disk\n"
        "                self._partial.pop(part, None)\n"
        "                await asyncio.to_thread(_make_room, part)\n"
        "                raise",
        "            except BaseException:\n                raise",
    ),
    Mutation(
        "the time the bytes took is not credited against the pace",
        "src/tgmirror/engine/runner.py",
        "            credit = self._mono() - started",
        "            credit = 0.0",
    ),
    Mutation(
        "the daily cap is not checked before the bytes go up",
        "src/tgmirror/engine/runner.py",
        "            self._guard.check_cap(todo.size)  # do not upload what cannot be posted today",
        "            pass",
    ),
    Mutation(
        "a credit shortens the long pause too",
        "src/tgmirror/core/limiter.py",
        "                wait += self._rng.uniform(*lim.long_pause_range)",
        "                wait += max(self._rng.uniform(*lim.long_pause_range) - credit, 0.0)",
    ),
    Mutation(
        "the post uploads the file again",
        "src/tgmirror/core/telethon_gateway.py",
        "            if item.uploaded is not None:  # the bytes went up before: only post them",
        "            if False:",
    ),
    Mutation(
        "a FloodWait while the bytes go up is not sat out",
        "src/tgmirror/engine/flood.py",
        "                await self.flooded(method, exc, give_up=floods >= MAX_FLOODS_PER_CALL)\n"
        "            except PeerFlood:\n",
        "                raise\n            except PeerFlood:\n",
    ),
    Mutation(
        "a transport 429 is retried in place instead of sat out",
        "src/tgmirror/core/pool.py",
        "                    if exc.flood:\n                        raise FloodWait(TRANSPORT_WAIT, transport=True) from exc\n",
        "",
    ),
    Mutation(
        "a cut-off download starts again from the beginning",
        "src/tgmirror/core/telethon_gateway.py",
        "        have: set[int] = known[1] if resuming and known is not None else set()",
        "        have: set[int] = set()",
    ),
    Mutation(
        "downloads share the main connection",
        "src/tgmirror/core/telethon_gateway.py",
        "            else await self._download_sender()\n",
        "            else self._client._sender  # noqa: SLF001\n",
    ),
    Mutation(
        "fewer upload connections than asked for go unsaid",
        "src/tgmirror/core/telethon_gateway.py",
        "            if failure is not None:\n",
        "            if False:\n",
    ),
    Mutation(
        "a download connection that cannot be made goes unsaid",
        "src/tgmirror/core/telethon_gateway.py",
        '                log.warning(\n                    "download connection could not be made',
        '                (lambda *a: None)(\n                    "download connection could not be made',
    ),
]


def copy_tree(work: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    for folder in ("src", "tests"):
        shutil.copytree(ROOT / folder, work / folder, ignore=ignore)
    shutil.copy(ROOT / "pyproject.toml", work / "pyproject.toml")


def environment(work: Path) -> dict[str, str]:
    env = dict(os.environ)
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(work / "src") + (os.pathsep + old if old else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TGMIRROR_LANG"] = "en"
    return env


def run_tests(work: Path, env: dict[str, str], timeout: float) -> tuple[str, str]:
    """``("passed" | "failed" | "timeout", output tail)``."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-x",
        "-p",
        "no:cacheprovider",
        "--timeout=30",
        *TESTS,
    ]
    try:
        done = subprocess.run(
            command, cwd=work, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        tail = (exc.stdout or b"")[-500:] if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return "timeout", str(tail)
    return ("passed" if done.returncode == 0 else "failed"), (done.stdout + done.stderr)[-1500:]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--list", action="store_true", help="list the mutations and exit")
    parser.add_argument("--only", metavar="TEXT", help="only mutations whose name contains TEXT")
    parser.add_argument("--timeout", type=float, default=120, help="seconds per pytest run")
    args = parser.parse_args()

    chosen = [m for m in MUTATIONS if not args.only or args.only.lower() in m.name.lower()]
    if args.list:
        for m in chosen:
            print(f"{m.name}  [{m.path}]")
        return 0
    if not chosen:
        print("no mutation matches", args.only)
        return 2

    work = Path(tempfile.mkdtemp(prefix="tgmirror-mutation-"))
    survivors: list[str] = []
    try:
        copy_tree(work)
        env = environment(work)
        where = subprocess.run(
            [sys.executable, "-c", "import tgmirror; print(tgmirror.__file__)"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if not where.startswith(str(work)):
            print(f"the copy is not the code under test ({where}); refusing to go on")
            return 2
        print(f"working on a copy: {work}", flush=True)

        state, tail = run_tests(work, env, args.timeout * 2)
        if state != "passed":
            print(f"the tests do not pass before any mutation ({state}); fix that first:\n{tail}")
            return 2
        print("baseline: passed", flush=True)

        for number, m in enumerate(chosen, 1):
            target = work / m.path
            original = target.read_text(encoding="utf-8")
            if original.count(m.old) != 1:
                print(
                    f"[{number}/{len(chosen)}] SKIPPED  {m.name}: pattern found {original.count(m.old)}x"
                )
                survivors.append(f"{m.name} (pattern no longer matches: update the script)")
                continue
            target.write_text(original.replace(m.old, m.new), encoding="utf-8")
            try:
                state, _ = run_tests(work, env, args.timeout)
            finally:
                target.write_text(original, encoding="utf-8")
            label = {"failed": "killed (a test failed)", "timeout": "killed (tests hung)"}.get(
                state
            )
            print(f"[{number}/{len(chosen)}] {label or 'SURVIVED'}  {m.name}", flush=True)
            if label is None:
                survivors.append(m.name)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    print(f"{len(chosen) - len(survivors)}/{len(chosen)} caught")
    for name in survivors:
        print("  survived:", name)
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
