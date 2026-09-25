"""``engine/backup.py`` end to end on ``FakeGateway`` and a real SQLite file: no network.

What Phase 11a is judged by (docs/06-lo-trinh.md): a source with an album/topic/poll is backed up
to disk in order, an album never split, decision D3 gated the same way as strategy B, and a backup
stopped or killed midway resumes as a delta (by the highest id already in ``messages.jsonl``)
without duplicating anything.
"""

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from tests.fakes import FakeGateway
from tests.integration.test_runner import LIMITS
from tgmirror.core.errors import PerMessage
from tgmirror.core.gateway import ChannelInfo, ChatKind, MediaKind
from tgmirror.engine import backupdir
from tgmirror.engine.backup import BackupNeedsAcknowledgement as NeedsAck
from tgmirror.engine.backup import (
    BackupWriter,
    FiltersChanged,
    WrongSource,
    begin_backup,
)
from tgmirror.engine.endpoints import SourceRestricted
from tgmirror.engine.runner import RunControl
from tgmirror.engine.runner import RunnerTiming as Timing
from tgmirror.store.backups import Backup
from tgmirror.store.db import Store
from tgmirror.store.runs import Control, RunStatus


class Recorder:
    def __init__(self) -> None:
        self.notices: list[tuple[str, dict[str, object]]] = []
        self.backups: list[Backup] = []

    def notice(self, code: str, **params: object) -> None:
        self.notices.append((code, params))

    def progress(self, backup: Backup) -> None:
        self.backups.append(backup)

    def transfer(self, transfer: object) -> None:
        pass

    @property
    def codes(self) -> list[str]:
        return [c for c, _ in self.notices]


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.gw = FakeGateway()
        self.src: ChannelInfo = self.gw.add_channel("Source")
        self.path = tmp_path / "state.db"
        self.dir = tmp_path / "out"
        self.recorder = Recorder()
        self._stores: list[Store] = []

    async def store(self) -> Store:
        store = await Store.open(self.path)
        self._stores.append(store)
        return store

    async def sleep(self, seconds: float) -> None:
        pass

    def fill(self, count: int, start: int = 1) -> None:
        for i in range(start, start + count):
            self.gw.add_message(self.src.id, f"m{i}")

    def writer(
        self,
        *,
        control: RunControl | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> BackupWriter:
        return BackupWriter(
            self._store,
            self.gw,
            LIMITS,
            reporter=self.recorder,
            control=control,
            sleep=sleep or self.sleep,
            rng=random.Random(0),
            timing=Timing(poll_interval=0.1, heartbeat_interval=3600),
        )

    async def begin(self, store: Store, **kw: Any) -> tuple[Backup, backupdir.BackupManifest]:
        self._store = store
        return await begin_backup(store, self.gw, self.src, self.dir, **kw)

    async def run(self, store: Store, control: RunControl | None = None, **kw: Any) -> Backup:
        backup, manifest = await self.begin(store, **kw)
        return await self.writer(control=control).run(backup, self.dir, manifest)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    r = Rig(tmp_path)
    yield r
    for store in r._stores:
        await store.close()


async def test_backs_up_text_and_photo_in_order(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, "hello")
    rig.gw.add_message(rig.src.id, "world", media=MediaKind.PHOTO, size=100, mime="image/jpeg")
    store = await rig.store()

    final = await rig.run(store)

    assert final.status is RunStatus.DONE
    assert final.done == 2
    records = backupdir.iter_records(rig.dir)
    assert [r.id for r in records] == [1, 2]
    assert records[0].text_html == "hello"
    assert records[1].media is not None and records[1].media.kind is MediaKind.PHOTO
    media_file = backupdir.media_dir(rig.dir) / records[1].media.filename
    assert media_file.exists()


async def test_album_kept_as_one_unit(rig: Rig) -> None:
    rig.gw.add_album(rig.src.id, [MediaKind.PHOTO, MediaKind.PHOTO, MediaKind.VIDEO], "caption")
    store = await rig.store()

    final = await rig.run(store)

    assert final.done == 3
    records = backupdir.iter_records(rig.dir)
    assert len({r.grouped_id for r in records}) == 1
    assert [r.id for r in records] == [1, 2, 3]


async def test_forum_topic_recorded(rig: Rig) -> None:
    rig.gw.channels[rig.src.id] = ChannelInfo(
        rig.src.id, rig.src.title, kind=ChatKind.FORUM, is_admin=True
    )
    news = rig.gw.add_topic(rig.src.id, "News")
    rig.gw.add_message(rig.src.id, "n1", topic_id=news.id)
    store = await rig.store()

    backup, manifest = await rig.begin(store)
    titles = {t.id: t.title for t in manifest.topics}
    assert titles[1] == "General"
    assert titles[news.id] == "News"

    final = await rig.writer().run(backup, rig.dir, manifest)
    records = backupdir.iter_records(rig.dir)
    assert records[0].topic_id == news.id
    assert final.done == 1


async def test_poll_is_backed_up_with_options(rig: Rig) -> None:
    rig.gw.add_message(
        rig.src.id,
        media=MediaKind.POLL,
        title="Favourite colour?",
        poll_options=("Red", "Blue"),
        poll_quiz=True,
        poll_correct_option=1,
    )
    store = await rig.store()

    final = await rig.run(store)

    assert final.done == 1
    record = backupdir.iter_records(rig.dir)[0]
    assert record.media is not None
    assert record.media.kind is MediaKind.POLL
    assert record.media.poll_question == "Favourite colour?"
    assert record.media.poll_options == ("Red", "Blue")
    assert record.media.poll_correct_option == 1


async def test_message_deleted_between_listing_and_export_is_skipped(rig: Rig) -> None:
    rig.fill(2)
    store = await rig.store()
    real_export = rig.gw.export_unit

    async def flaky(src: int, unit: Any, media_dir: Path, on_transfer: Any = None) -> Any:
        if unit.ids == [1]:
            raise PerMessage("gone_from_source")
        return await real_export(src, unit, media_dir, on_transfer)

    rig.gw.export_unit = flaky  # type: ignore[method-assign]

    final = await rig.run(store)

    assert final.done == 1
    assert final.gone == 1
    assert "gone" in rig.recorder.codes
    records = backupdir.iter_records(rig.dir)
    assert [r.id for r in records] == [2]


# ---- decision D3 -------------------------------------------------------------------------------


async def test_protected_source_refused_without_admin_or_ack(rig: Rig) -> None:
    rig.gw.channels[rig.src.id] = ChannelInfo(
        rig.src.id, rig.src.title, noforwards=True, is_admin=False
    )
    store = await rig.store()

    with pytest.raises(SourceRestricted):
        await rig.begin(store)


async def test_protected_source_admin_needs_acknowledgement(rig: Rig) -> None:
    rig.gw.channels[rig.src.id] = ChannelInfo(
        rig.src.id, rig.src.title, noforwards=True, is_admin=True
    )
    store = await rig.store()

    with pytest.raises(NeedsAck):
        await rig.begin(store)


async def test_protected_source_backed_up_with_ack(rig: Rig) -> None:
    rig.gw.channels[rig.src.id] = ChannelInfo(
        rig.src.id, rig.src.title, noforwards=True, is_admin=False
    )
    rig.fill(1)
    store = await rig.store()

    backup, manifest = await rig.begin(store, protected_ack=True)

    assert manifest.protected_ack is True
    assert manifest.src_noforwards is True
    final = await rig.writer().run(backup, rig.dir, manifest)
    assert final.done == 1


# ---- resume / delta -----------------------------------------------------------------------------


async def test_stop_then_resume_is_a_delta_with_no_duplicates(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()
    control = RunControl()
    control.request_stop()  # stop before the very first unit: nothing written this run

    first = await rig.run(store, control=control)
    assert first.status is RunStatus.STOPPED
    assert backupdir.last_id(rig.dir) == 0

    second = await rig.run(store)  # a fresh writer, same directory: continues from id 0
    assert second.status is RunStatus.DONE
    assert second.done == 4
    records = backupdir.iter_records(rig.dir)
    assert [r.id for r in records] == [1, 2, 3, 4]


async def test_resume_after_partial_progress(rig: Rig) -> None:
    rig.fill(4)
    store = await rig.store()

    stops_after = 2

    class StopAfterN:
        def __init__(self) -> None:
            self.control = RunControl()
            self.count = 0

        def progress(self, backup: Backup) -> None:
            self.count += 1
            if self.count >= stops_after:
                self.control.request_stop()

        def notice(self, code: str, **params: object) -> None:
            pass

        def transfer(self, transfer: object) -> None:
            pass

    stopper = StopAfterN()
    backup, manifest = await rig.begin(store)
    writer = BackupWriter(
        store,
        rig.gw,
        LIMITS,
        reporter=stopper,
        control=stopper.control,
        sleep=rig.sleep,
        rng=random.Random(0),
        timing=Timing(poll_interval=0.1, heartbeat_interval=3600),
    )
    first = await writer.run(backup, rig.dir, manifest)
    assert first.status is RunStatus.STOPPED
    assert backupdir.last_id(rig.dir) == 2

    second = await rig.run(store)
    assert second.status is RunStatus.DONE
    records = backupdir.iter_records(rig.dir)
    assert [r.id for r in records] == [1, 2, 3, 4]  # no duplicate, nothing missing


async def test_filter_cannot_change_on_resume(rig: Rig) -> None:
    rig.fill(1)
    store = await rig.store()
    await rig.run(store, filters_json='{"include": [{"media": ["photo"]}]}')

    with pytest.raises(FiltersChanged):
        await rig.begin(store, filters_json='{"include": [{"media": ["video"]}]}')


async def test_resuming_with_same_filter_is_fine(rig: Rig) -> None:
    rig.gw.add_message(rig.src.id, media=MediaKind.PHOTO, size=1, mime="image/jpeg")
    store = await rig.store()
    filt = '{"include": [{"media": ["photo"]}]}'
    await rig.run(store, filters_json=filt)

    rig.gw.add_message(rig.src.id, media=MediaKind.PHOTO, size=1, mime="image/jpeg")
    final = await rig.run(store, filters_json=filt)  # same filter: fine, continues
    assert final.done == 1  # only the second message: the first is already past the cursor


async def test_wrong_source_for_directory_is_refused(rig: Rig) -> None:
    rig.fill(1)
    store = await rig.store()
    await rig.run(store)

    other = rig.gw.add_channel("Other")
    with pytest.raises(WrongSource):
        await begin_backup(store, rig.gw, other, rig.dir)


# ---- control: pause/stop from another terminal ---------------------------------------------------


async def test_pause_from_the_store_holds_then_resumes(rig: Rig) -> None:
    rig.fill(2)
    store = await rig.store()
    backup, manifest = await rig.begin(store)
    await store.set_backup_control(backup.id, Control.PAUSE)

    control = RunControl()
    writer = rig.writer(control=control)
    run_task = asyncio.create_task(writer.run(backup, rig.dir, manifest))
    await asyncio.sleep(0.05)
    live = await store.get_backup(backup.id)
    assert live is not None and live.status is RunStatus.PAUSED

    await store.set_backup_control(backup.id, Control.NONE)
    final = await run_task
    assert final.status is RunStatus.DONE
    assert "paused" in rig.recorder.codes
    assert "resumed" in rig.recorder.codes
