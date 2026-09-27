"""``cli/errors.py``: turning any exception into one sentence + exit code (N4, Phase 15b).

Before this, ``run()`` only handled ``TgMirrorError``: anything else — a disk-full ``OSError``, a
stray ``RuntimeError`` — reached the user as a raw traceback, and the run/backup row it came from
was left ``running`` forever (fixed separately in ``engine/runner.py``/``engine/backup.py``, whose
own ``except BaseException`` branch this module's messages back up).
"""

import dataclasses
import errno
from collections.abc import Callable

import pytest
import typer

from tgmirror.cli.errors import describe_any, run
from tgmirror.cli.runtime import Runtime
from tgmirror.core.errors import NotLoggedIn
from tgmirror.ui.messages import t

MakeRuntime = Callable[..., Runtime]


async def _raise(exc: BaseException) -> None:
    raise exc


def test_a_plain_exception_gets_one_sentence_and_exit_code_1(
    make_runtime: MakeRuntime, capsys: pytest.CaptureFixture[str]
) -> None:
    rt = make_runtime()

    with pytest.raises(typer.Exit) as exc_info:
        run(rt, _raise(RuntimeError("boom")))

    assert exc_info.value.exit_code == 1
    assert t("err.crashed", detail="RuntimeError: boom") in capsys.readouterr().err


def test_disk_full_gets_its_own_sentence(
    make_runtime: MakeRuntime, capsys: pytest.CaptureFixture[str]
) -> None:
    rt = make_runtime()
    exc = OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(typer.Exit) as exc_info:
        run(rt, _raise(exc))

    assert exc_info.value.exit_code == 1
    assert t("err.disk_full", detail=str(exc)) in capsys.readouterr().err


def test_other_os_errors_get_the_generic_os_error_sentence(
    make_runtime: MakeRuntime, capsys: pytest.CaptureFixture[str]
) -> None:
    rt = make_runtime()
    exc = OSError(errno.EACCES, "Permission denied")

    with pytest.raises(typer.Exit) as exc_info:
        run(rt, _raise(exc))

    assert exc_info.value.exit_code == 1
    assert t("err.os_error", detail=str(exc)) in capsys.readouterr().err


def test_debug_lets_a_plain_exception_through_instead_of_printing_it(
    make_runtime: MakeRuntime,
) -> None:
    rt = dataclasses.replace(make_runtime(), debug=True)

    with pytest.raises(RuntimeError):
        run(rt, _raise(RuntimeError("boom")))


def test_describe_any_defers_to_describe_for_tgmirror_errors() -> None:
    assert describe_any(NotLoggedIn("x")) == t("err.not_logged_in")
