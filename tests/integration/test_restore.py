"""``tgmirror restore`` end to end: a hand-built backup directory as the source (no gateway on
that side at all), ``FakeGateway`` as the destination, a real SQLite file: no network.

What phase 11b is judged by (docs/06-lo-trinh.md): a backup with a topic/album/poll restores in
order, an album never split, decision D3 gated the same way as backup/reupload (re-asked, no
"is_admin" branch), a restore stopped or killed midway resumes as a delta without duplicating
anything, `retry` sends only what failed, and a restore shares its mirror/``msg_map`` with a live
clone of the same original source into the same destination.
"""

import random
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeGateway
from tests.integration.test_runner import LIMITS, Crash
from tgmirror.core.errors import PerMessage
from tgmirror.core.gateway import ChannelInfo, ChatKind, ExportedMedia, ExportedMessage, MediaKind
from tgmirror.engine import backupdir
from tgmirror.engine.backup_reader import BackupReader
from tgmirror.engine.runner import RunControl, Runner, RunnerTiming
from tgmirror.engine.runs import NeedsAcknowledgement, RunRequest, begin_run
from tgmirror.store.db import Store
from tgmirror.store.runs import Run, RunStatus

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SRC_ID = -1001


class Recorder:
    def __init__(self) -> None:
        self.notices: list[tuple[str, dict[str, object]]] = []

    def notice(self, code: str, **params: object) -> None:
        self.notices.append((code, params))

    def progress(self, run: Run) -> None:
        pass

    def transfer(self, transfer: object) -> None:
        pass

    @property
    def codes(self) -> list[str]:
        return [c for c, _ in self.notices if c not in ("analyzed", "cap_days")]


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.gw = FakeGateway()
        self.dst = self.gw.add_channel("Destination", kind=ChatKind.FORUM)
        self.dir = tmp_path / "backup"
        self.path = tmp_path / "state.db"
        self.recorder = Recorder()
        self.batch_size = 3
        self._stores: list[Store] = []

    async def store(self) -> Store:
        store = await Store.open(self.path)
        self._stores.append(store)
        return store

    async def sleep(self, seconds: float) -> None:
        pass

    def seed(
        self,
        records: list[ExportedMessage] = (),  # type: ignore[assignment]
        *,
        noforwards: bool = False,
        protected_ack: bool = False,
        topics: tuple[backupdir.BackupTopic, ...] = (),
    ) -> ChannelInfo:
        manifest = backupdir.BackupManifest(
            format_version=1,
            tgmirror_version="0.1.0",
            src_id=SRC_ID,
            src_title="Source",
            src_kind=ChatKind.BROADCAST,
            src_about="",
            src_noforwards=noforwards,
            protected_ack=protected_ack,
            filters_json="{}",
            topics=topics,
        )
        backupdir.write_manifest(self.dir, manifest)
        if records:
            backupdir.append_records(self.dir, list(records))
        return ChannelInfo(
            manifest.src_id,
            manifest.src_title,
            manifest.src_kind,
            noforwards=manifest.src_noforwards,
        )

    def reader(self) -> BackupReader:
        manifest = backupdir.read_manifest(self.dir)
        assert manifest is not None
        return BackupReader(self.dir, manifest)

    def runner(self, store: Store, *, control: RunControl | None = None) -> Runner:
        return Runner(
            store,
            self.gw,
            LIMITS,
            reporter=self.recorder,
            control=control,
            sleep=self.sleep,
            rng=random.Random(0),
            timing=RunnerTiming(poll_interval=0.2, heartbeat_interval=3600),
            reader_override=self.reader(),
        )

    async def begin(self, store: Store, src: ChannelInfo, **options: Any) -> Run:
        request = RunRequest(
            mode="reupload",
            from_backup=str(self.dir),
            reset_polls=True,
            batch_size=self.batch_size,
            **options,
        )
        started = await begin_run(store, self.gw, src, self.dst, request)
        return started.run

    @property
    def dst_texts(self) -> list[str]:
        return [m.text for m in self.gw.messages[self.dst.id]]


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


def rows(rig: Rig) -> list[tuple[int, str, int | None, str | None]]:
    with sqlite3.connect(rig.path) as db:
        return db.execute(
            "SELECT src_msg_id, status, dst_msg_id, reason FROM msg_map ORDER BY src_msg_id"
        ).fetchall()


def _msg(msg_id: int, text: str = "", **kw: Any) -> ExportedMessage:
    grouped_id = kw.pop("grouped_id", None)
    topic_id = kw.pop("topic_id", None)
    return ExportedMessage(msg_id, NOW, grouped_id, topic_id, None, text, None, **kw)


def _photo(filename: str) -> ExportedMedia:
    return ExportedMedia(kind=MediaKind.PHOTO, filename=filename)


async def test_text_photo_album_and_topic_restore_in_order(rig: Rig) -> None:
    topic = backupdir.BackupTopic(2, "News")
    records = [
        _msg(1, "hello"),
        _msg(2, "album", grouped_id=100, media=_photo("2.jpg")),
        _msg(3, "", grouped_id=100, media=_photo("3.jpg")),
        _msg(4, "", topic_id=2),
        _msg(
            5,
            "",
            media=ExportedMedia(
                kind=MediaKind.POLL, poll_question="colour?", poll_options=("red", "blue")
            ),
        ),
    ]
    src = rig.seed(records, topics=(topic,))
    store = await rig.store()
    run = await rig.begin(store, src)
    final = await rig.runner(store).run(run)

    assert final.status is RunStatus.DONE
    assert final.done == 5
    assert [r[1] for r in rows(rig)] == ["done"] * 5
    dst = rig.gw.messages[rig.dst.id]
    assert len(dst) == 5
    # the album stayed together, whole, in order
    album = [m for m in dst if m.grouped_id is not None]
    assert len(album) == 2 and album[0].grouped_id == album[1].grouped_id
    # the source's topic 2 ("News") got a destination topic of its own, not General
    assert rig.gw.calls_to("create_topic")
    poll_msg = dst[-1]
    assert poll_msg.media is MediaKind.POLL


async def test_killed_mid_run_resumes_without_duplicates(rig: Rig) -> None:
    records = [_msg(i, f"m{i}") for i in range(1, 5)]
    src = rig.seed(records)
    store = await rig.store()
    rig.batch_size = 1
    run = await rig.begin(store, src)

    original = rig.gw.send_prepared
    calls = 0

    async def dies_after_the_second_one(
        dst: int, prepared: Any, caption: Any, *a: Any, **kw: Any
    ) -> list[int]:
        nonlocal calls
        calls += 1
        ids = await original(dst, prepared, caption, *a, **kw)
        if calls == 2:
            raise Crash
        return ids

    rig.gw.send_prepared = dies_after_the_second_one  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await rig.runner(store).run(run)
    rig.gw.send_prepared = original  # type: ignore[method-assign]

    resumed_run = await rig.begin(store, src, force=True)
    final = await rig.runner(store).run(resumed_run)

    assert final.status is RunStatus.DONE
    assert rig.dst_texts == ["m1", "m2", "m3", "m4"]  # not five, none twice
    assert [r[1] for r in rows(rig)] == ["done"] * 4


async def test_d3_needs_the_statement_again_even_though_the_backup_has_one(rig: Rig) -> None:
    src = rig.seed([_msg(1, "secret")], noforwards=True, protected_ack=True)
    store = await rig.store()
    with pytest.raises(NeedsAcknowledgement):
        await rig.begin(store, src)  # begin_run re-asks: the backup's own ack is not enough

    run = await rig.begin(store, src, protected_ack=True)
    final = await rig.runner(store).run(run)
    assert final.status is RunStatus.DONE


async def test_retry_sends_only_the_failed_message_again(rig: Rig) -> None:
    records = [_msg(1, "a"), _msg(2, "b")]
    src = rig.seed(records)
    store = await rig.store()
    rig.batch_size = 1
    run = await rig.begin(store, src)

    original = rig.gw.send_prepared
    calls = 0

    async def fail_second(dst: int, prepared: Any, caption: Any, *a: Any, **kw: Any) -> list[int]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PerMessage("boom")
        return await original(dst, prepared, caption, *a, **kw)

    rig.gw.send_prepared = fail_second  # type: ignore[method-assign]
    first = await rig.runner(store).run(run)
    rig.gw.send_prepared = original  # type: ignore[method-assign]

    assert first.failed == 1
    assert [r[1] for r in rows(rig)] == ["done", "failed"]

    retry_run = await rig.begin(store, src, retry_of=first.id)
    final = await rig.runner(store).run(retry_run)
    assert final.status is RunStatus.DONE
    assert final.done == 1
    assert [r[1] for r in rows(rig)] == ["done", "done"]
    assert rig.dst_texts == ["a", "b"]


async def test_a_live_clone_and_a_restore_of_the_same_source_share_one_mirror(rig: Rig) -> None:
    """docs/06-lo-trinh.md, phase 11: id N restored, then a live clone of the same original
    channel continues from N+1 without re-copying what the restore already sent."""
    src = rig.seed([_msg(1, "a"), _msg(2, "b")])
    store = await rig.store()
    run = await rig.begin(store, src)
    restored = await rig.runner(store).run(run)
    assert restored.status is RunStatus.DONE

    # the original channel is "still alive": register it directly, with one more message
    rig.gw.channels[src.id] = src
    rig.gw.add_message(src.id, "a")
    rig.gw.add_message(src.id, "b")
    rig.gw.add_message(src.id, "c")

    live_request = RunRequest(mode="auto", batch_size=10)
    live_started = await begin_run(store, rig.gw, src, rig.dst, live_request)
    live_runner = Runner(
        store, rig.gw, LIMITS, reporter=rig.recorder, sleep=rig.sleep, rng=random.Random(0)
    )
    final = await live_runner.run(live_started.run)

    assert final.status is RunStatus.DONE
    assert final.done == 1  # only "c": 1 and 2 are already done from the restore, same mirror
    assert rig.dst_texts == ["a", "b", "c"]
