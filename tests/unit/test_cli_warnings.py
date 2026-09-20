"""What the library warns about reaches the terminal, once, in the program's own words."""

import logging

import pytest

from tgmirror.cli.app import _show_warnings


@pytest.fixture(autouse=True)
def clean_logger() -> None:
    logger = logging.getLogger("tgmirror")
    before = list(logger.handlers), logger.level
    yield
    logger.handlers[:], logger.level = before[0], before[1]


def test_a_warning_from_the_library_is_printed_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _show_warnings()

    logging.getLogger("tgmirror.core.pool").warning("limit 4 -> 2 (closed connection)")

    err = capsys.readouterr().err
    assert err.strip() == "[cảnh báo] limit 4 -> 2 (closed connection)"


def test_calling_it_twice_does_not_print_everything_twice(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _show_warnings()
    _show_warnings()

    logging.getLogger("tgmirror.core.pool").warning("once")

    assert capsys.readouterr().err.count("once") == 1


def test_information_and_debugging_stay_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    _show_warnings()

    logging.getLogger("tgmirror.core.pool").info("a detail")
    logging.getLogger("tgmirror.core.pool").debug("a lot of detail")

    assert capsys.readouterr().err == ""


def test_hachoirs_own_chatter_about_big_files_is_silenced() -> None:
    from hachoir.core import config

    import tgmirror.core.telethon_gateway  # noqa: F401 - importing it is what silences hachoir

    assert config.quiet is True
