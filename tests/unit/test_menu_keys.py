"""``decode_menu_key``/``pump_menu_keys``/``menu_key_queue`` (``cli/keys.py``): the arrow/Enter/Esc
decoding added for the full-screen menu, and the queue-feeding thread built on the same
``KeyReader`` protocol as the classic ``p``/``r``/``q`` hotkeys (``pump_keys``, untouched)."""

import asyncio
import threading

import pytest

from tgmirror.cli.keys import MenuKey, decode_menu_key, menu_key_queue, pump_menu_keys


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\r", MenuKey.ENTER),
        ("\n", MenuKey.ENTER),
        ("\x1b", MenuKey.ESC),
        ("\x7f", MenuKey.BACKSPACE),
        ("\x08", MenuKey.BACKSPACE),
        ("\x1b[A", MenuKey.UP),  # POSIX arrow up
        ("\xe0H", MenuKey.UP),  # Windows arrow up
        ("\x1b[B", MenuKey.DOWN),
        ("\xe0P", MenuKey.DOWN),
        ("p", "p"),  # a plain character passes through unchanged (p/r/q, type-to-filter, ...)
        ("Q", "Q"),
    ],
)
def test_decode_menu_key(raw: str, expected: MenuKey | str) -> None:
    assert decode_menu_key(raw) == expected


class _ScriptedReader:
    """Feeds ``keys`` in order, then tells ``pump_menu_keys`` to stop (like the real readers, its
    ``read`` never blocks longer than a short timeout)."""

    def __init__(self, keys: list[str], stop: threading.Event) -> None:
        self._keys = list(keys)
        self._stop = stop

    def read(self, timeout: float) -> str | None:
        if not self._keys:
            self._stop.set()
            return None
        return self._keys.pop(0)

    def close(self) -> None:
        pass


async def test_pump_menu_keys_decodes_and_queues_in_order() -> None:
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    queue: asyncio.Queue[MenuKey | str] = asyncio.Queue()
    reader = _ScriptedReader(["p", "\r", "\x1b[A"], stop)

    pump_menu_keys(reader, queue, loop, stop)  # runs to completion: the reader empties and stops

    assert await queue.get() == "p"
    assert await queue.get() == MenuKey.ENTER
    assert await queue.get() == MenuKey.UP


async def test_menu_key_queue_yields_none_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tgmirror.cli.keys.open_reader", lambda: None)
    loop = asyncio.get_running_loop()

    with menu_key_queue(loop) as queue:
        assert queue is None


async def test_menu_key_queue_feeds_a_real_reader_through_the_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _OnceReader:
        def __init__(self) -> None:
            self._sent = False

        def read(self, timeout: float) -> str | None:
            if self._sent:
                return None
            self._sent = True
            return "q"

        def close(self) -> None:
            pass

    monkeypatch.setattr("tgmirror.cli.keys.open_reader", lambda: _OnceReader())
    loop = asyncio.get_running_loop()

    with menu_key_queue(loop) as queue:
        assert queue is not None
        assert await asyncio.wait_for(queue.get(), timeout=2) == "q"
