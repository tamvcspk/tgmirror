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
- **engine**: không import Telethon. Phần đọc của gateway có protocol hẹp `MessageReader` (`iter_messages`, `get_messages`, `prepare`) để planner/preview/reconcile/retry/chiến lược B nhận cả gateway trần lẫn bản đã qua `FloodGuard`. Làm việc với dataclass riêng (`SrcMessage`, `Unit`, `ChannelInfo`, `ServerFilter`, `MediaKind` định nghĩa trong `core/gateway.py` vì protocol dùng chúng; `Batch` nằm ở engine). `Unit` tự kiểm tra bất biến album (một `grouped_id`, id tăng dần).
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
    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]: ...   # đọc theo id (1..100/lần); tin đã xóa thì vắng mặt; `retry` dùng
    async def last_message_id(self, chat: int) -> int: ...             # 0 nếu trống; lần chạy đầu của cặp ghi làm dst_base_id (đích) và mỗi lần chạy thường ghi làm `src_last_id` (nguồn)
    async def copy_messages(self, src: int, dst: int, ids: list[int]) -> list[int | None]: ...   # strategy A
    async def prepare(self, src: int, unit: Unit, tmp: Path) -> Prepared: ...                     # strategy B, nửa đọc: đọc lại tin + tải media về `tmp`
    async def send_prepared(self, dst: int, prepared: Prepared, caption: CaptionPolicy) -> list[int]: ...  # strategy B, nửa ghi: gửi lại, trả id mới thẳng hàng với unit
    async def send_text(self, dst: int, text: str) -> int: ...                                    # tin text thay thế (`--placeholder`)
```

Đăng nhập cũng là lời gọi mạng nên có protocol riêng, `TelegramAuth` (`core/auth.py`): `account()`, `request_code`, `sign_in_code`, `sign_in_password`, `log_out`. Luồng `login(auth, prompts)` (thử lại tối đa 3 lần cho số điện thoại, mã, mật khẩu; mã hết hạn thì gửi lại một lần) chỉ biết protocol và `LoginPrompts`, nên test được bằng `FakeAuth`. `AccountInfo` cố ý không có số điện thoại.

`ServerFilter(media, search, since, until, max_id)` là phần Telegram lọc hộ; gateway chỉ được **thu hẹp an toàn** (trả về tập chứa mọi tin khớp), engine luôn chạy lại client matcher (xem `03-filters.md`). `since`/`until` là ngày nên chỉ gateway đổi được thành vị trí (kèm lề `ALBUM_MARGIN` id để album trên biên còn nguyên); `media`/`search` làm rớt các tin anh em trong album nên planner phải hoàn thiện album (một lần đọc không lọc quanh album). `FakeGateway` làm đúng hai điều đó nên test đối chiếu pushdown/quét đầy đủ có nghĩa. `FakeGateway` (`tests/fakes.py`) hiện thực protocol này trong bộ nhớ, có `fail_next(method, error)` để giả lập FloodWait/PeerFlood `poison(channel, msg_id)` (rồi `heal`) để giả lập một tin làm `copy_messages` ném `PerMessage`, và `delete_message` để xóa tin ở nguồn.

Hợp đồng của `get_messages`: một request, tối đa `MAX_IDS_PER_CALL` = 100 id (Telegram cho tối đa 100 id mỗi lời gọi forward hay đọc theo id; phía gọi tự chia). Trả về các tin **còn tồn tại**, tăng dần theo id; id đã bị xóa chỉ đơn giản là vắng mặt, không có chỗ giữ. Tin service cũng có thể có mặt (planner của lần chạy thường loại chúng, `retry` không cần vì tin service không bao giờ vào `msg_map`). `FloodGuard.reader` giãn cách và thử lại nó như `iter_messages` (một request đọc mỗi lời gọi).

Hợp đồng của `prepare`/`send_prepared` (phase 6): `prepare` là một request đọc (`get_messages` theo id của unit) cộng các lần tải; nó ghi mỗi file vào `tmp` dưới tên thật của nó và chỉ đổi tên từ `<id>.part` khi tải xong, nên gọi lại sau FloodWait dùng lại file đã xong thay vì tải lần hai. Tin đã bị xóa từ lúc đọc, hoặc loại media không dựng lại được → `PerMessage`. `Prepared.files` là các file tạm mà **bên gọi** xóa (engine xóa ngay sau khi unit gửi xong hay bị bỏ); `Prepared.handle` chỉ gateway hiểu. `send_prepared` gửi một unit: album trong một lời gọi, tin text bằng `send_message`, poll/location/contact bằng chính đối tượng media của tin gốc; `CaptionPolicy` chỉ đổi caption của tin có media. Kết quả thẳng hàng với `prepared.unit`.

Hợp đồng của `copy_messages`: kết quả thẳng hàng với `ids`. Lời gọi trả về bình thường là kết luận cuối: `None` nghĩa là Telegram không tạo tin nào cho id đó (đã xóa ở nguồn, không forward được) → `failed('not_copied')`. `PerMessage` nghĩa là Telegram từ chối chính các id (không tạo gì), engine thử lại từng unit. Nếu lời gọi bị ngắt (`Transient`) thì kết quả không rõ, chỉ reconcile mới biết (`04-state-checkpoint.md`).

## Hai chiến lược clone

| | A. Copy (mặc định) | B. Reupload |
|---|---|---|
| API | `forward_messages(dst, ids, from_peer=src, drop_author=True)` | download → `send_file` |
| Băng thông | ~0 | tải xuống + tải lên |
| Tốc độ | Cao (tới 100 id/lời gọi, mặc định 20) | Thấp, bị chặn bởi upload |
| Khi nào | Mặc định | Nguồn `noforwards` mà user là admin (D3), hoặc forward lỗi từng tin, hoặc muốn biến đổi media |

Chọn chiến lược **cho từng unit** (`engine/strategy.py::router`), theo `mode` của lần chạy:

- `copy`: mọi unit đi đường forward. Không dùng được với `--caption` khác `keep` (lỗi mã 2).
- `auto` (mặc định): forward, trừ unit mà caption phải sửa (`--caption strip-links|append|none` và unit có tin media kèm caption): unit đó tải xuống rồi tải lên lại. Thứ tự giữ nguyên (D4): batcher cắt batch mỗi khi chiến lược đổi.
- `reupload`: mọi unit tải xuống rồi tải lên lại. Là cách duy nhất sao chép nguồn `noforwards` (D3, đổi 2026-09-20: user chịu hoàn toàn trách nhiệm): phải có lời tuyên bố của user. Tài khoản là admin của nguồn: câu hỏi xác nhận hoặc cờ `--yes-i-administer-this-channel`. Tài khoản **không** phải admin (user là chủ kênh bằng tài khoản khác): chỉ cờ, kèm cảnh báo; không có cờ thì từ chối (mã 4). `--yes` không thay được cờ. Mỗi lần chạy dựa trên lời tuyên bố in một cảnh báo trách nhiệm (`warn.responsibility`).

`run`/`retry` không hỏi lại D3 vì không đổi nguồn; xem "Kiểm lại nguồn khi chạy lại" bên dưới.

### Chi tiết chiến lược B

- **Mỗi unit là một batch** (`Batch.strategy = REUPLOAD`, batcher phát ngay): một lần tải, một lần gửi, nên khi crash chỉ có đúng unit đó `pending` và `reconcile` so được (số tin, loại media, cấu trúc album).
- Tải về `<data>/tmp/run-<id>/<msg_id><đuôi>` (cộng `<msg_id>.thumb.jpg` cho video có ảnh bìa), gửi bằng `send_file` với `caption` + `formatting_entities` (và `parse_mode=None`: entity là định dạng, không parse markdown), giữ **thuộc tính của tệp gốc** (`attributes` của Document: video duration/w/h, audio, sticker, animated, filename; `mime_type`) và `force_document=True` **chỉ** cho tệp vốn là tệp thường (`MediaKind.DOCUMENT`, để một tệp .jpg không bị gửi lại thành ảnh); với video, GIF, video tròn, nhạc, voice, sticker thì **không** đặt `force_document` vì Telethon đổi nó thành `force_file` và Telegram sẽ hiện mọi thứ như tệp bất kể thuộc tính (lỗi người dùng gặp khi thử thật, 2026-09-20); video thường còn thêm `nosound_video=True` để video không có tiếng không bị đổi thành GIF, xóa file tạm ngay sau khi unit gửi xong. Thư mục tạm được dọn khi lần chạy bắt đầu (phần còn lại của lần bị kill) và khi kết thúc.
- Album: gom cả nhóm vào một `send_file([...])` (một caption và một danh sách entity cho từng tin). Telethon gửi mọi file trong album như nhau và đọc chi tiết video/audio từ chính file bằng `hachoir` (dependency), nên thuộc tính riêng từng tin trong album không được giữ như tin đơn.
- **Tải trước (pipeline)**: trong lúc unit hiện tại đang tải lên, unit kế đã được đọc và tải xuống (`engine/reupload.py::Pipeline`, `[limits] prefetch`, mặc định 1: tối đa 2 unit trên đĩa; `tmp_budget_mb` giới hạn tổng dung lượng, một unit lớn hơn ngân sách vẫn đi khi đĩa trống). Chỉ nửa **đọc** chạy trước; ghi vẫn tuần tự, đúng thứ tự, một lời gọi một lúc (một account). Lỗi ở nền (FloodWait quá dài, UnsupportedMedia, đọc nguồn lỗi) được trao đúng thứ tự, sau các batch đi trước nó. Không dùng nhiều kết nối kiểu FastTelethon (người dùng chọn pipeline, 2026-09-20): tải/lên từng file vẫn một kết nối.
- Chi phí qua limiter: mỗi unit là một lần `pace` (giãn cách như mọi ghi, tính vào `daily_cap`) và `prepare` là một request đọc. Chưa có "delay theo dung lượng": không có số liệu, và một upload lớn vốn đã chậm; chỉnh sau khi có `flood_log` (spike 6).

### Kiểm lại nguồn khi chạy lại

`clone` kiểm `noforwards` trước khi làm gì (luật D3 ở trên), nhưng `run`/`retry` đi thẳng vào `begin_run` với cặp cũ, và nguồn có thể đã bật "Restrict saving content" từ lần trước. Vì chiến lược B tải nội dung xuống, `begin_run` đọc lại nguồn (một request chuẩn bị) trước mọi lần chạy có thể tải lên lại (`mode` reupload, hoặc `auto` với `--caption` khác `keep`): nguồn `noforwards` mà lần chạy chưa mang lời tuyên bố của user (`RunOptions.protected_ack`, ghi lúc `clone` được xác nhận và mang theo bởi `run`/`retry`) → `SourceRestricted` (mã 4) nếu tài khoản không phải admin, `NeedsAcknowledgement` (mã 2) nếu là admin; cả hai chỉ cách chạy lại bằng `clone ... --yes-i-administer-this-channel`. Có lời tuyên bố thì chạy tiếp dù tài khoản đã mất quyền admin: đó là trách nhiệm của user, nói một lần cho cả cặp (và mỗi lần chạy nhắc lại).

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
| Poll | Giữ, tạo lại (opt-in `--reset-polls`); không có cờ thì **bỏ + cảnh báo** | Câu hỏi, đáp án, cấu hình được giữ. **Mất toàn bộ số vote** |
| Quiz | Giữ nếu account đã trả lời (và có `--reset-polls`); nếu chưa → không sao chép được | Telethon chỉ dựng lại được quiz khi thấy đáp án đúng, mà đáp án chỉ hiện sau khi trả lời (`get_input_media` ném `TypeError` với quiz chưa trả lời). Tool **không** tự trả lời quiz |
| Location / Venue | Giữ | Payload tĩnh, `send_message(dst, file=message.media)` |
| Contact | Giữ | Như trên |
| Game | Bỏ + cảnh báo | Gắn với bot; user account không phát hành lại được |
| Invoice | Bỏ + cảnh báo | Gắn với payment provider/bot; Telethon không có chuyển đổi `MessageMediaInvoice` → `InputMedia` |

- Dùng `send_message(dst, file=message.media)` (Telethon tự dựng `InputMediaPoll`/geo/venue/contact qua `utils.get_input_media`), không tự lắp `InputMediaPoll` bằng tay. Quiz chưa trả lời và invoice phải được bắt trước khi gọi, không để `TypeError` nổi lên.
- **Không bao giờ bỏ tin âm thầm** (`engine/reupload.py::plan_unit`, quyết định trước khi gọi Telegram, cho từng unit):
  - poll/quiz không có `--reset-polls`: bỏ, một dòng cảnh báo (`run.skipped_unsupported`), `reason='unsupported:poll'` (hoặc `:quiz`). Đó là lựa chọn của user chứ không phải giới hạn kỹ thuật nên **không** có placeholder.
  - game, invoice, quiz chưa trả lời (dù có `--reset-polls`): không sao chép được. Có `--ignore-unsupported`: bỏ + cảnh báo; có `--placeholder`: bỏ và đăng thay bằng một tin text; **không có cờ nào: lần chạy dừng ngay** (`UnsupportedMedia`, lần chạy `failed`, mã 2, nêu id tin và hai cờ), phần đã xong được giữ, chạy lại `clone` với cờ thì đi tiếp từ con trỏ. (Thay cho bước "xem trước báo số tin không hỗ trợ": không quét cả nguồn trước khi chạy; dừng ở tin đầu tiên gặp.)
- Tin bị bỏ: ghi `msg_map` với `status='skipped'`, `reason='unsupported:<loại>'` (không phải `failed`, để `retry` không thử lại vô ích), tăng `stats.skipped_unsupported`, con trỏ đi qua. Vì con trỏ đã qua, delta không nhặt lại poll đã bỏ khi sau này thêm `--reset-polls`; muốn có chúng phải `--fresh`.
- Với `--placeholder`, mỗi tin bị bỏ vì không hỗ trợ được thay bằng một tin text `[<Loại>: <title> — không thể sao chép]` ở đích, để người xem biết chỗ đó từng có gì. Đây là một lần gửi ghi (qua limiter); hàng `msg_map` vẫn `status='skipped'` nhưng có `dst_msg_id` của tin thay thế. Nếu process chết giữa lúc đăng, `reconcile` không so được (đích có một tin text, nguồn là game) nên coi là mơ hồ và đăng lại: có thể trùng một dòng ghi chú.
- Cờ CLI: xem `02-cli-ux.md` mục "Tin đặc thù".

## Unit và Batch

- **Unit**: một tin đơn, hoặc **toàn bộ album** (cùng `grouped_id`). Đơn vị không bao giờ bị tách.
- **Batch**: tập Unit gửi trong một lời gọi (tổng tin <= `batch_size`), tất cả cùng một chiến lược (`Batch.strategy`). Album lớn hơn `batch_size` vẫn gửi nguyên trong một batch (tối đa 10 tin/album nên không vượt 100). Unit đi đường tải lên lại luôn là một batch riêng, phát ngay khi tới (không đợi unit sau).
- Filter đánh giá trên Unit (xem `03-filters.md`). Unit không khớp thành một `Skip(count, last_id)` (planner); batcher cộng dồn `Skip` vào `Batch.skipped`/`Batch.upto` của batch đang được phát: con trỏ của batch (`Batch.last_id`) được vượt qua chúng vì chúng đều đứng trước unit chưa vào batch. Một batch đang chờ được phát ra sau mỗi `FLUSH_AFTER` = 500 tin bị loại (kể cả batch chỉ có `Skip`, không unit nào), để quãng dài không có tin khớp vẫn lưu tiến độ và nghe được pause/stop.

## Vòng lặp runner

Bản dưới là pseudo-code; cài đặt thật ở `engine/runner.py` + `engine/flood.py` (phase 4). Lần chạy được mở trước bởi `engine/runs.py::begin_run` (một transaction: tìm hoặc tạo mirror của cặp, chọn filter, chặn nếu tiến trình khác đang giữ cặp); runner rồi: nạp `limiter_state` → reconcile → vòng `planner` → `batcher` → (bỏ unit đã `done`) → `guard.pace` (limiter, ngủ ngắt được) → kiểm `pause`/`stop` (`_gate`: `stop` thì thoát; `pause` thì **giữ tại chỗ** — trạng thái `paused`, tiến trình và heartbeat vẫn sống — cho đến `run`/phím `r` hoặc `stop`) → write-ahead → `guard.write(copy_batch)` (FloodWait: log, AIMD, ngủ, gửi lại **đúng lời gọi đó**) → `commit_batch` (kèm `limiter_state`). Lần chạy có thể tải lên lại (`mode` reupload, hoặc `--caption` khác `keep`) đọc batch qua `Pipeline`: `_make` (đọc `done` để bỏ unit đã xong, `plan_unit`, giữ chỗ trong `Window`, `reader.prepare`) chạy ở nền cho batch kế trong lúc batch này gửi; consumer vẫn làm đúng thứ tự gate → `pace` → write-ahead → `guard.write(send_prepared)` → `commit_batch`, rồi xóa file và trả chỗ. Unit bị bỏ (`DROP`) không qua `pace`/write-ahead thật mà ghi ngay (`begin_batch` + `commit_batch` không gọi Telegram); placeholder qua `pace` và `guard.write(send_text)`. Lần chạy chỉ forward đọc trực tiếp như trước. Mọi lần đọc của runner (planner, reconcile, prepare) đi qua `guard.reader(gateway)`, cùng giãn cách và xử lý FloodWait (đọc tiếp từ tin cuối đã trao). `Runner._nap` ném `Interrupted` khi có `stop`/Ctrl+C nên không lời gọi nào đi tiếp sau đó; `pause` không cắt ngang lúc chờ mà có hiệu lực ở ranh giới batch kế tiếp.

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

`planner.failed_units(reader, src, ids)` (phase 5, cho `retry`): đọc các id đã lỗi theo lô 100 bằng `get_messages` (không quét nguồn), gom các tin liên tiếp cùng `grouped_id` thành một `Unit` (chỉ gồm các thành viên đã lỗi; album vắt qua ranh giới hai lô vẫn là một unit), và đưa ra `Gone(ids)` cho các id không còn ở nguồn. Không áp filter: các tin này đã qua filter lúc được đọc lần đầu.

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
| Lỗi từng tin (`MessageIdInvalidError` khi mọi id đã xóa, media invalid, caption/tin quá dài sau `append`, tin đã xóa lúc `prepare`, loại media không dựng lại được, ...) | `PerMessage`: gửi lại từng unit (chiến lược A); unit vẫn lỗi → `msg_map.status = failed` + `reason`, tiếp tục; `tgmirror retry` xử lý sau. Chiến lược B: mỗi unit vốn đã đơn lẻ nên lỗi là `failed` ngay |

Ánh xạ từ Telethon sang `core/errors.py` nằm ở một chỗ: `map_exception` trong `core/telethon_gateway.py` (thêm các lỗi đăng nhập: `InvalidCode`, `CodeExpired`, `PasswordRequired`, `InvalidPassword`, `InvalidPhone`, `BadApiCredentials`, `NotLoggedIn`; và `TooManyChannels`, `SessionBusy`, `PerMessage` từ `MessageIdInvalidError`). Lỗi của store/lần chạy (`StoreError`, `SchemaTooNew`, `RunBusy` ở `core/errors.py`; `RunNotFound`, `ModeUnsupported`, `RunWaiting` ở `engine/runs.py`) cũng qua `cli/errors.py`. Lỗi RPC chưa biết → `GatewayError` với tên lỗi của Telegram, không để `RPCError` lọt ra ngoài. CLI đổi mỗi lỗi thành một câu và một mã thoát ở `cli/errors.py`.

## Cấu trúc gói

```
src/tgmirror/
  core/     gateway.py  auth.py  telethon_gateway.py  limiter.py  errors.py  config.py  paths.py
  engine/   endpoints.py  runs.py  planner.py  batcher.py  strategy.py  copy.py  reupload.py  flood.py  reconcile.py  preview.py  runner.py  status.py
  filters/  model.py  parser.py  pushdown.py  matcher.py
  store/    schema.sql  db.py  runs.py  msgmap.py  floodlog.py  limiterstate.py
  cli/      app.py  wizard.py  filter_options.py  runtime.py  errors.py  interrupt.py  keys.py  commands/ (auth.py channels.py clone.py run.py retry.py status.py control.py history.py ...)
  ui/       messages.py  prompts.py  tables.py  progress.py
tests/      fakes.py (FakeGateway, FakeAuth, ScriptedPrompter)  unit/  integration/
```

## Kiểm thử

- `FakeGateway` mô phỏng kênh (danh sách tin, album, lỗi FloodWait/PeerFlood theo kịch bản) và đồng hồ giả cho limiter.
- Test bắt buộc: resume sau khi kill giữa chừng không trùng/sót; album không bị tách; FloodWait làm tăng delay; PeerFlood dừng lần chạy; delta chỉ lấy tin mới; pause giữ tại chỗ rồi chạy tiếp. Phase 2 có `tests/integration/test_runner.py`, `tests/unit/test_store.py`, ...; phase 4 thêm `tests/unit/test_limiter.py` (đồng hồ giả) và `tests/integration/test_runner_flood.py` (kịch bản flood trên `FakeGateway`); phase 5 thêm `tests/integration/test_runner_retry.py` (retry: kill giữa chừng, bị từ chối, album, tin đã xóa), `tests/unit/test_status.py` (ước lượng tiến độ/ETA) và `tests/unit/test_cli_retry.py` (`retry`, `status`); phase 6 thêm `tests/integration/test_runner_reupload.py` (mỗi unit một lần gửi, tải trước, ngân sách đĩa, dừng khi đang tải, caption, tin không hỗ trợ, flood, kill trước/sau khi gửi), `tests/unit/test_reupload.py` (router, batcher, `plan_unit`, `Window`, `Pipeline`), `tests/unit/test_telethon_reupload.py` (tải/gửi/viết lại caption trên client giả) và `tests/unit/test_cli_reupload.py` (cờ, D3, wizard); test kiến trúc (`tests/unit/test_architecture.py`) giữ Telethon và SQL trong đúng chỗ.
- Không test tự động chống lại Telegram thật. Có script thủ công `scripts/smoke.py` dùng kênh test riêng.
- Kiểm tra đột biến thủ công: `scripts/mutation_check.py` (phase 6) phá từng cơ chế của chiến lược B/D3 trên **bản sao tạm** của `src/` và `tests/` rồi xem có test nào đỏ; mỗi lần chạy có timeout riêng nên một đột biến làm test treo cũng chỉ tính là "bị phát hiện". `--list`, `--only <chữ>`. Một dòng `SURVIVED` là cơ chế không test nào bảo vệ (hoặc đột biến tương đương): đọc nó, đừng chỉ làm nó xanh. Test có timeout 60 giây (`pytest-timeout`).
