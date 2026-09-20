# 01 — Kiến trúc

## Module

```
                 ┌────────────┐
   user ───────► │    cli     │  Typer commands + wizard (questionary) + Rich UI
                 └─────┬──────┘
                       │ RunRequest
                 ┌─────▼──────┐        ┌───────────┐
                 │   engine   │◄──────►│   store   │  SQLite: runs, mirrors, msg_map, flood_log
                 │ runner     │        └───────────┘
                 │ planner    │        ┌───────────┐
                 │ strategies │◄──────►│  filters  │  model, parser, server pushdown, matcher
                 └─────┬──────┘        └───────────┘
                       │ (protocol)
                 ┌─────▼──────┐
                 │  gateway   │  ← limiter (AIMD, daily cap, FloodWait) sits here
                 └─────┬──────┘
                       │
                    Telethon
```

- **gateway**: `TelegramGateway` protocol + `TelethonGateway`. Mọi lời gọi mạng đi qua đây, và đi qua `Limiter`.
- **engine**: không import Telethon. Phần đọc của gateway có protocol hẹp `MessageReader` (`iter_messages`) để planner/preview/reconcile nhận cả gateway trần lẫn bản đã qua `FloodGuard`. Làm việc với dataclass riêng (`SrcMessage`, `Unit`, `ChannelInfo`, `ServerFilter`, `MediaKind` định nghĩa trong `core/gateway.py` vì protocol dùng chúng; `Batch` nằm ở engine). `Unit` tự kiểm tra bất biến album (một `grouped_id`, id tăng dần).
- **store**: chỉ engine và CLI đọc/ghi, qua `Store` (`store/db.py`) với các phương thức theo ý định (`start_run`, `begin_batch`, `commit_batch`, `finish`, `set_control`, ...); SQL không rò ra ngoài `store/` (có test).
- **filters**: thuần logic, không I/O: `model` (pydantic, chuẩn hóa), `parser` (YAML + cờ), `matcher` (đánh giá một Unit), `pushdown` (`plan_read`: chuyển phần an toàn của filter thành `ReadPlan`/`ServerFilter`). Engine dùng chúng; `engine/preview.py` lấy mẫu để xem trước.

### `TelegramGateway` (rút gọn)

```python
class TelegramGateway(Protocol):
    async def list_channels(self) -> list[ChannelInfo]: ...            # broadcast, supergroup, forum, group
    async def get_channel(self, ref: int) -> ChannelInfo: ...          # has .kind, .noforwards, .can_post, .is_admin
    async def create_channel(self, title: str, about: str = "", kind: ChatKind = ...) -> ChannelInfo: ...
    async def list_topics(self, src: int) -> list[TopicInfo]: ...      # forum only
    async def create_topic(self, dst: int, title: str, ...) -> int: ...# forum only
    def iter_messages(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> AsyncIterator[SrcMessage]: ...   # id > min_id, tăng dần
    async def last_message_id(self, chat: int) -> int: ...             # 0 nếu trống; lần chạy đầu của cặp ghi làm dst_base_id
    async def copy_messages(self, src: int, dst: int, ids: list[int]) -> list[int | None]: ...   # strategy A
    async def reupload(self, src: int, dst: int, unit: Unit, tmp: Path) -> list[int]: ...        # strategy B
```

Đăng nhập cũng là lời gọi mạng nên có protocol riêng, `TelegramAuth` (`core/auth.py`): `account()`, `request_code`, `sign_in_code`, `sign_in_password`, `log_out`. Luồng `login(auth, prompts)` (thử lại tối đa 3 lần cho số điện thoại, mã, mật khẩu; mã hết hạn thì gửi lại một lần) chỉ biết protocol và `LoginPrompts`, nên test được bằng `FakeAuth`. `AccountInfo` cố ý không có số điện thoại.

`ServerFilter(media, search, since, until, max_id)` là phần Telegram lọc hộ; gateway chỉ được **thu hẹp an toàn** (trả về tập chứa mọi tin khớp), engine luôn chạy lại client matcher (xem `03-filters.md`). `since`/`until` là ngày nên chỉ gateway đổi được thành vị trí (kèm lề `ALBUM_MARGIN` id để album trên biên còn nguyên); `media`/`search` làm rớt các tin anh em trong album nên planner phải hoàn thiện album (một lần đọc không lọc quanh album). `FakeGateway` làm đúng hai điều đó nên test đối chiếu pushdown/quét đầy đủ có nghĩa. `FakeGateway` (`tests/fakes.py`) hiện thực protocol này trong bộ nhớ, có `fail_next(method, error)` để giả lập FloodWait/PeerFlood và `poison(channel, msg_id)` để giả lập một tin làm `copy_messages` ném `PerMessage`.

Hợp đồng của `copy_messages`: kết quả thẳng hàng với `ids`. Lời gọi trả về bình thường là kết luận cuối: `None` nghĩa là Telegram không tạo tin nào cho id đó (đã xóa ở nguồn, không forward được) → `failed('not_copied')`. `PerMessage` nghĩa là Telegram từ chối chính các id (không tạo gì), engine thử lại từng unit. Nếu lời gọi bị ngắt (`Transient`) thì kết quả không rõ, chỉ reconcile mới biết (`04-state-checkpoint.md`).

## Hai chiến lược clone

| | A. Copy (mặc định) | B. Reupload |
|---|---|---|
| API | `forward_messages(dst, ids, from_peer=src, drop_author=True)` | download → `send_file` |
| Băng thông | ~0 | tải xuống + tải lên |
| Tốc độ | Cao (tới 100 id/lời gọi, mặc định 20) | Thấp, bị chặn bởi upload |
| Khi nào | Mặc định | Nguồn `noforwards` mà user là admin (D3), hoặc forward lỗi từng tin, hoặc muốn biến đổi media |

Chọn chiến lược ở `planner` mỗi lần chạy (`mode = auto | copy | reupload`). `auto`: dùng copy; nếu nguồn `noforwards` thì áp D3.

### Chi tiết chiến lược B

- Tải về file tạm (`tmp/<run>/<msg_id>`), gửi bằng `send_file` giữ caption + `formatting_entities`, giữ attributes video (duration, `supports_streaming`), xóa file tạm ngay sau khi gửi.
- Album: gom cả nhóm vào một `send_file([...])`.
- Telethon không có parallel upload sẵn: nếu cần tăng tốc, viết uploader nhiều connection (kiểu FastTelethon) ở `core/uploader.py`. Chỉ làm ở phase 6.

## Loại nguồn: broadcast, supergroup, forum

`ChannelInfo.kind`: `broadcast` · `supergroup` · `forum` (supergroup bật topics) · `group` (basic group cũ). Cả bốn loại đều là nguồn hợp lệ (quyết định 2026-09-19). Cùng một engine, chỉ khác ở gateway và cách chọn đích:

| Nguồn | Đích tạo mới | Ghi chú |
|---|---|---|
| `broadcast` | broadcast channel | Luồng gốc, không topic |
| `supergroup` / `group` | supergroup | Basic group không tạo mới được nên đích luôn là supergroup |
| `forum` | supergroup bật forum | Ánh xạ topic → topic (bên dưới) |

Đích có sẵn phải cùng loại với nguồn (forum → forum); khác loại thì từ chối trước khi chạy (chốt 2026-09-19).

### Ánh xạ topic (forum)

- Lần chạy đầu của cặp: đọc danh sách topic nguồn, tạo topic tương ứng ở đích (tên + icon), lưu `src_topic_id → dst_topic_id` trong bảng `topic_map` (`04-state-checkpoint.md`). Topic General (id 1) ánh xạ vào General của đích. Topic mới xuất hiện ở nguồn ở lần chạy sau thì được tạo bổ sung.
- Duyệt nguồn theo **id tăng dần toàn group** (id là chung cho mọi topic), nên `cursor_src_id` vẫn là một số duy nhất. Mỗi tin được định tuyến theo topic của nó.
- Một lời gọi forward chỉ có một topic đích. Batcher cắt batch khi topic đổi, nên nhóm chat xen kẽ nhiều topic sẽ có batch nhỏ hơn (chậm hơn, nhưng thứ tự trong từng topic vẫn đúng).
- `forward_messages` của Telethon **không** có tham số topic. `TelethonGateway` phải gọi `ForwardMessagesRequest(top_msg_id=...)` trực tiếp (vẫn nằm trong gateway + limiter, luật 1). Cần spike xác nhận (`06-lo-trinh.md`).

### Hạn chế khi copy từ group

- Forward với `drop_author` bỏ tên người gửi: tin của nhiều thành viên đều hiện như tin của account đang chạy. Đã chốt **không** thêm tiền tố tên người gửi, kể cả ở chiến lược B; README phải nêu rõ hạn chế này.
- Forward không giữ liên kết reply (`reply_to`). Chiến lược B có thể ánh xạ reply qua `msg_map`; chiến lược A thì không.
- Đích là supergroup có thể có slow mode: xử lý như FloodWait (đã có ở bảng lỗi).

## Tin đặc thù: poll/quiz, location, contact, game, invoice

Chốt 2026-09-19. Chiến lược A (forward phía server) để Telegram giữ nguyên loại tin; hành vi thực tế của từng loại với `drop_author=True` cần spike (`06-lo-trinh.md`). Bảng dưới là hành vi của **chiến lược B** (đọc payload rồi gửi tin mới), sau khi D3 đã cho phép reupload:

| Loại | Quyết định | Cách làm & hạn chế |
|---|---|---|
| Poll | Giữ, tạo lại (opt-in `--reset-polls`) | Câu hỏi, đáp án, cấu hình được giữ. **Mất toàn bộ số vote** |
| Quiz | Giữ nếu account đã trả lời; nếu chưa → bỏ + cảnh báo | Telethon chỉ dựng lại được quiz khi thấy đáp án đúng, mà đáp án chỉ hiện sau khi trả lời (`get_input_media` ném `TypeError` với quiz chưa trả lời). Tool **không** tự trả lời quiz |
| Location / Venue | Giữ | Payload tĩnh, `send_message(dst, file=message.media)` |
| Contact | Giữ | Như trên |
| Game | Bỏ + cảnh báo | Gắn với bot; user account không phát hành lại được |
| Invoice | Bỏ + cảnh báo | Gắn với payment provider/bot; Telethon không có chuyển đổi `MessageMediaInvoice` → `InputMedia` |

- Dùng `send_message(dst, file=message.media)` (Telethon tự dựng `InputMediaPoll`/geo/venue/contact qua `utils.get_input_media`), không tự lắp `InputMediaPoll` bằng tay. Quiz chưa trả lời và invoice phải được bắt trước khi gọi, không để `TypeError` nổi lên.
- Tin bị bỏ vì không hỗ trợ: log `[WARN] Bỏ qua <loại>: "<title>" tại tin <id>`, tăng `stats.skipped_unsupported`, ghi `msg_map` với `status='skipped'`, `reason='unsupported:<loại>'` (không phải `failed`, để `retry` không thử lại vô ích).
- Với `--placeholder`, mỗi tin bị bỏ vì không hỗ trợ (game, invoice, quiz chưa trả lời) được thay bằng một tin text `[<Loại>: <title> — không thể sao chép]` ở đích, để người xem biết chỗ đó từng có gì. Đây là một lần gửi ghi (qua limiter); hàng `msg_map` vẫn `status='skipped'` nhưng có `dst_msg_id` của tin thay thế.
- Cờ CLI: xem `02-cli-ux.md` mục "Tin đặc thù".

## Unit và Batch

- **Unit**: một tin đơn, hoặc **toàn bộ album** (cùng `grouped_id`). Đơn vị không bao giờ bị tách.
- **Batch**: tập Unit gửi trong một lời gọi (tổng tin <= `batch_size`). Album lớn hơn `batch_size` vẫn gửi nguyên trong một batch (tối đa 10 tin/album nên không vượt 100).
- Filter đánh giá trên Unit (xem `03-filters.md`). Unit không khớp thành một `Skip(count, last_id)` (planner); batcher cộng dồn `Skip` vào `Batch.skipped`/`Batch.upto` của batch đang được phát: con trỏ của batch (`Batch.last_id`) được vượt qua chúng vì chúng đều đứng trước unit chưa vào batch. Một batch đang chờ được phát ra sau mỗi `FLUSH_AFTER` = 500 tin bị loại (kể cả batch chỉ có `Skip`, không unit nào), để quãng dài không có tin khớp vẫn lưu tiến độ và nghe được pause/stop.

## Vòng lặp runner

Bản dưới là pseudo-code; cài đặt thật ở `engine/runner.py` + `engine/flood.py` (phase 4). Lần chạy được mở trước bởi `engine/runs.py::begin_run` (một transaction: tìm hoặc tạo mirror của cặp, chọn filter, chặn nếu tiến trình khác đang giữ cặp); runner rồi: nạp `limiter_state` → reconcile → vòng `planner` → `batcher` → (bỏ unit đã `done`) → `guard.pace` (limiter, ngủ ngắt được) → kiểm `pause`/`stop` (`_gate`: `stop` thì thoát; `pause` thì **giữ tại chỗ** — trạng thái `paused`, tiến trình và heartbeat vẫn sống — cho đến `run`/phím `r` hoặc `stop`) → write-ahead → `guard.write(copy_batch)` (FloodWait: log, AIMD, ngủ, gửi lại **đúng lời gọi đó**) → `commit_batch` (kèm `limiter_state`). Mọi lần đọc của runner (planner, reconcile) đi qua `guard.reader(gateway)`, cùng giãn cách và xử lý FloodWait (đọc tiếp từ tin cuối đã trao). `Runner._nap` ném `Interrupted` khi có `stop`/Ctrl+C nên không lời gọi nào đi tiếp sau đó; `pause` không cắt ngang lúc chờ mà có hiệu lực ở ranh giới batch kế tiếp.

```
run = begin_run(...); gw = gateway; lim = limiter
for batch in batcher(planner.units(run), run.batch_size):
    ctl = store.control(run_id)                 # none | pause | stop (hoặc phím p/r/q)
    if ctl == stop: store.finish(run, stopped); break
    if ctl == pause: hold_until_resumed_or_stopped()
    while True:
        await lim.acquire(cost=batch.size, kind=run.mode)
        try:
            results = await strategy.execute(batch)
            break
        except FloodWait as e:
            lim.on_flood(); store.log_flood(...)
            if e.seconds > cfg.max_auto_wait: store.set_waiting(run, resume_at); return
            await sleep(e.seconds + jitter)     # then retry same batch
        except PeerFlood:
            store.fail(run, "peer_flood"); return    # never retry
        except PerMessageError as e:
            results = partial(e)                # mark failed items, continue
    lim.on_success()
    store.commit_batch(run, batch, results)     # ONE transaction: msg_map + cursor + stats
    ui.update(...)
```

`planner.units(...)` (phase 3): `plan = plan_read(filters, run.cursor_from, pushdown=run.options.pushdown)` rồi `gw.iter_messages(src, min_id=plan.min_id, filters=plan.server)` (ascending), gom album (hoàn thiện album nếu `plan.complete_albums`), áp `Matcher`, bỏ service message, đưa ra `Unit` hoặc `Skip`.

## Xử lý lỗi

| Lỗi | Hành động |
|---|---|
| `FloodWaitError` | Ghi `flood_log`, tăng delay (AIMD), sleep `seconds + jitter`, retry đúng lời gọi đó (batch vẫn `pending`). Quá `max_auto_wait` (không có `--wait`) hoặc 5 lần liền cho một lời gọi → xóa `pending` của batch, status `waiting_flood` + `resume_at`, mã 3 |
| Daily cap (`DailyCapReached`, không phải lỗi Telegram) | Trước write-ahead: lần chạy `waiting_flood` với `fail_reason='daily_cap'`, `resume_at` = 00:00 ngày kế, mã 3 |
| `SlowModeWaitError` | Như FloodWait (chủ yếu nhóm); `FloodWait.slow_mode=True` để `flood_log` ghi `slow_mode` |
| `PeerFloodError` | Dừng lần chạy, status `failed(peer_flood)`, khuyến cáo nghỉ >= 24h. Không retry |
| `ChatWriteForbiddenError` / `ChatAdminRequiredError` | Dừng, báo thiếu quyền ở kênh đích |
| `FileReferenceExpiredError` | Lấy lại message rồi retry một lần |
| `ChatForwardsRestrictedError` (CHAT_FORWARDS_RESTRICTED) | Áp D3 |
| Mất kết nối | Backoff mũ (tenacity), Telethon tự reconnect. Phase 1: `Transient`, in một câu, chưa retry |
| Lỗi từng tin (`MessageIdInvalidError` khi mọi id đã xóa, media invalid, ...) | `PerMessage`: gửi lại từng unit; unit vẫn lỗi → `msg_map.status = failed` + `reason`, tiếp tục; `tgmirror retry` xử lý sau |

Ánh xạ từ Telethon sang `core/errors.py` nằm ở một chỗ: `map_exception` trong `core/telethon_gateway.py` (thêm các lỗi đăng nhập: `InvalidCode`, `CodeExpired`, `PasswordRequired`, `InvalidPassword`, `InvalidPhone`, `BadApiCredentials`, `NotLoggedIn`; và `TooManyChannels`, `SessionBusy`, `PerMessage` từ `MessageIdInvalidError`). Lỗi của store/lần chạy (`StoreError`, `SchemaTooNew`, `RunBusy` ở `core/errors.py`; `RunNotFound`, `ModeUnsupported`, `RunWaiting` ở `engine/runs.py`) cũng qua `cli/errors.py`. Lỗi RPC chưa biết → `GatewayError` với tên lỗi của Telegram, không để `RPCError` lọt ra ngoài. CLI đổi mỗi lỗi thành một câu và một mã thoát ở `cli/errors.py`.

## Cấu trúc gói

```
src/tgmirror/
  core/     gateway.py  auth.py  telethon_gateway.py  limiter.py  errors.py  config.py  paths.py  uploader.py
  engine/   endpoints.py  runs.py  planner.py  batcher.py  copy.py  flood.py  reconcile.py  preview.py  reupload.py  runner.py
  filters/  model.py  parser.py  pushdown.py  matcher.py
  store/    schema.sql  db.py  runs.py  msgmap.py  floodlog.py  limiterstate.py
  cli/      app.py  wizard.py  filter_options.py  runtime.py  errors.py  interrupt.py  keys.py  commands/ (auth.py channels.py clone.py run.py control.py history.py ...)
  ui/       messages.py  prompts.py  tables.py  progress.py
tests/      fakes.py (FakeGateway, FakeAuth, ScriptedPrompter)  unit/  integration/
```

## Kiểm thử

- `FakeGateway` mô phỏng kênh (danh sách tin, album, lỗi FloodWait/PeerFlood theo kịch bản) và đồng hồ giả cho limiter.
- Test bắt buộc: resume sau khi kill giữa chừng không trùng/sót; album không bị tách; FloodWait làm tăng delay; PeerFlood dừng lần chạy; delta chỉ lấy tin mới; pause giữ tại chỗ rồi chạy tiếp. Phase 2 có `tests/integration/test_runner.py`, `tests/unit/test_store.py`, ...; phase 4 thêm `tests/unit/test_limiter.py` (đồng hồ giả) và `tests/integration/test_runner_flood.py` (kịch bản flood trên `FakeGateway`); test kiến trúc (`tests/unit/test_architecture.py`) giữ Telethon và SQL trong đúng chỗ.
- Không test tự động chống lại Telegram thật. Có script thủ công `scripts/smoke.py` dùng kênh test riêng.
