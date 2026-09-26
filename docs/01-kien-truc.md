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
    async def count(self, src: int, *, min_id: int = 0, filters: ServerFilter = NO_FILTER) -> int: ...   # analyze: tổng (cận trên) của khoảng iter_messages sẽ đọc; một request `messages.search` limit=1
    async def get_messages(self, src: int, ids: Sequence[int]) -> list[SrcMessage]: ...   # đọc theo id (1..100/lần); tin đã xóa thì vắng mặt; `retry` dùng
    async def last_message_id(self, chat: int) -> int: ...             # 0 nếu trống; lần chạy đầu của cặp ghi làm dst_base_id (đích) và mỗi lần chạy thường ghi làm `src_last_id` (nguồn)
    async def copy_messages(self, src: int, dst: int, ids: list[int], *, topic: int | None = None) -> list[int | None]: ...   # strategy A; topic: phase 8
    async def prepare(self, src: int, unit: Unit, tmp: Path, on_transfer: OnTransfer | None = None) -> Prepared: ...   # strategy B, nửa đọc: đọc lại tin + tải media về `tmp`, báo tiến độ từng file
    async def send_prepared(self, dst: int, prepared: Prepared, caption: CaptionPolicy, on_transfer: OnTransfer | None = None, *, topic: int | None = None) -> list[int]: ...  # strategy B, nửa ghi: gửi lại, trả id mới thẳng hàng với unit, báo tiến độ tải lên
    async def send_text(self, dst: int, text: str, *, topic: int | None = None) -> int: ...        # tin text thay thế (`--placeholder`)
    async def export_unit(self, src: int, unit: Unit, media_dir: Path, on_transfer: OnTransfer | None = None) -> list[ExportedMessage]: ...   # phase 11a (backup): như prepare, nhưng trả bản ghi tự chứa (text HTML + payload media) thay vì Prepared; file tải vào media_dir và không bị xóa
```

`CaptionPolicy.hashtag` (phase 8): một hashtag đứng thay cho topic khi đích không có topic (`RunOptions.topic_as_hashtag`), cộng vào sau những gì `mode` tạo ra, kể cả tin chữ — trừ tin tự chứa (poll/quiz/location/contact/geo/dice) không có chỗ caption. Xem "Đích khác loại nguồn" bên dưới.

Đăng nhập cũng là lời gọi mạng nên có protocol riêng, `TelegramAuth` (`core/auth.py`): `account()`, `request_code`, `sign_in_code`, `sign_in_password`, `log_out`. Luồng `login(auth, prompts)` (thử lại tối đa 3 lần cho số điện thoại, mã, mật khẩu; mã hết hạn thì gửi lại một lần) chỉ biết protocol và `LoginPrompts`, nên test được bằng `FakeAuth`. `AccountInfo` cố ý không có số điện thoại.

`ServerFilter(media, search, since, until, max_id)` là phần Telegram lọc hộ; gateway chỉ được **thu hẹp an toàn** (trả về tập chứa mọi tin khớp), engine luôn chạy lại client matcher (xem `03-filters.md`). `since`/`until` là ngày nên chỉ gateway đổi được thành vị trí (kèm lề `ALBUM_MARGIN` id để album trên biên còn nguyên); `media`/`search` làm rớt các tin anh em trong album nên planner phải hoàn thiện album (một lần đọc không lọc quanh album). `FakeGateway` làm đúng hai điều đó nên test đối chiếu pushdown/quét đầy đủ có nghĩa. `FakeGateway` (`tests/fakes.py`) hiện thực protocol này trong bộ nhớ, có `fail_next(method, error)` để giả lập FloodWait/PeerFlood `poison(channel, msg_id)` (rồi `heal`) để giả lập một tin làm `copy_messages` ném `PerMessage`, và `delete_message` để xóa tin ở nguồn.

Hợp đồng của `get_messages`: một request, tối đa `MAX_IDS_PER_CALL` = 100 id (Telegram cho tối đa 100 id mỗi lời gọi forward hay đọc theo id; phía gọi tự chia). Trả về các tin **còn tồn tại**, tăng dần theo id; id đã bị xóa chỉ đơn giản là vắng mặt, không có chỗ giữ. Tin service cũng có thể có mặt (planner của lần chạy thường loại chúng, `retry` không cần vì tin service không bao giờ vào `msg_map`). `FloodGuard.reader` giãn cách và thử lại nó như `iter_messages` (một request đọc mỗi lời gọi).

Hợp đồng của `prepare`/`send_prepared` (phase 6): `prepare` là một request đọc (`get_messages` theo id của unit) cộng các lần tải; nó ghi mỗi file vào `tmp` dưới tên thật của nó và chỉ đổi tên từ `<id>.part` khi tải xong, nên gọi lại sau FloodWait dùng lại file đã xong thay vì tải lần hai. Tin đã bị xóa từ lúc đọc, hoặc loại media không dựng lại được → `PerMessage`. `Prepared.files` là các file tạm mà **bên gọi** xóa (engine xóa ngay sau khi unit gửi xong hay bị bỏ); `Prepared.handle` chỉ gateway hiểu. `send_prepared` gửi một unit: album trong một lời gọi, tin text bằng `send_message`, poll/location/contact bằng chính đối tượng media của tin gốc; `CaptionPolicy` chỉ đổi caption của tin có media. Kết quả thẳng hàng với `prepared.unit`.

Hợp đồng của `export_unit` (phase 11a, `tgmirror backup`): như `prepare` (một request đọc lại tin của unit, rồi tải media vào `media_dir`, đặt tên `<id><đuôi>` giống hệt cách `prepare`/`_download` đặt tên — mỗi tin của unit vốn đã có id riêng nên không cần hậu tố phân biệt như `01-kien-truc.md` bản kế hoạch ban đầu nghĩ), khác ở hai chỗ: file **không bị xóa** sau khi dùng (thư mục backup là sản phẩm, không phải chỗ tạm), và kết quả là `ExportedMessage` (text ở dạng HTML của Telethon qua `telethon.extensions.html.unparse`, cộng `ExportedMedia` — tên file/mime/size/duration cho media có file, hoặc payload poll/quiz/geo/venue/contact/game/invoice cho media tự chứa) thay vì `Prepared`, để ghi thẳng vào `messages.jsonl` mà không cần gateway thật lúc đọc lại (`engine/backupdir.py` không phụ thuộc Telethon). `PerMessage` khi tin đã bị xóa từ lúc liệt kê.

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
- `auto` (mặc định): forward, trừ unit mà caption phải sửa (`--caption strip-links|append|none` và unit có tin media kèm caption; hoặc, phase 8, unit của một topic forum phải mang hashtag topic — `topic_as_hashtag` với đích không phải forum, `strategy.takes_hashtag`: unit ngoài General có chỗ caption, tức không phải poll/quiz/location/contact/game/invoice/sticker/video_note). Unit đó đi **gửi bằng mã file** khi nguồn cho lưu nội dung và mọi tin của unit là file (ảnh, video, tài liệu, ...): Telegram nhận id của file nó đã lưu nên **không tải xuống, không tải lên** (`Strategy.REFERENCE`, xem dưới). Ngược lại (nguồn `noforwards`, hay unit là vị trí, danh bạ, poll) thì tải xuống rồi tải lên lại. Thứ tự giữ nguyên (D4): batcher cắt batch mỗi khi chiến lược đổi.
- `reupload`: mọi unit tải xuống rồi tải lên lại (không bao giờ gửi bằng mã file: đó là điều người dùng đã chọn). Là cách duy nhất sao chép nguồn `noforwards` (D3, đổi 2026-09-20: user chịu hoàn toàn trách nhiệm): phải có lời tuyên bố của user. Tài khoản là admin của nguồn: một câu hỏi Có/Không hoặc cờ `--yes-i-administer-this-channel`. Tài khoản **không** phải admin (user là chủ kênh bằng tài khoản khác): cờ đó gõ trên dòng lệnh, hoặc — không có dòng lệnh để gõ (wizard/menu, tinh chỉnh 2026-09-25) — gõ nguyên văn đúng chữ cờ vào một câu hỏi riêng (`CloneFlow._confirm_unadministered`, không phải Có/Không), giữ đúng mức "cố ý" như gõ cờ thật; không có cờ/không gõ đúng thì từ chối (mã 4). `--yes` không thay được cờ hay câu gõ đó. Mỗi lần chạy dựa trên lời tuyên bố in một cảnh báo trách nhiệm (`warn.responsibility`).

`run`/`retry` không hỏi lại D3 vì không đổi nguồn; xem "Kiểm lại nguồn khi chạy lại" bên dưới.

### Gửi bằng mã file (`Strategy.REFERENCE`, 2026-09-20)

Spike 11 (kênh không cấm lưu nội dung): `send_file(dst, message.media, caption=...)` cho Telegram id của file nó đã lưu, và Telegram tạo tin mới đúng loại (video giữ thời lượng, tệp giữ dung lượng; video 184 MB và cả album 6 video 115–490 MB, giữ nguyên nhóm) mà không tải gì. Chỉ dùng cho `--mode auto` đổi caption trên nguồn không cấm lưu nội dung (D3: nguồn `noforwards` giữ đường tải xuống rồi tải lên; spike 2026-09-21 trên nguồn cấm lưu (album 2 video, có lời tuyên bố D3): Telegram **từ chối** gửi bằng mã file với `ChatForwardsRestrictedError` ("can't forward messages from a protected chat", từ `SendMultiMediaRequest`), nên đó là giới hạn của server chứ không chỉ là thận trọng; tin đơn chưa thử riêng; `begin_run` ghi `RunOptions.src_protected` mỗi lần chạy) và cho unit mà **mọi tin là file** (`strategy.has_files`).

- Nửa đọc `reader.fetch(src, unit)`: đọc lại tin (một request đọc có pace, như `prepare`) để lấy mã tham chiếu file còn mới; không có gì trên đĩa, không qua `Window`. Nửa ghi `gateway.send_by_reference(dst, prepared, caption)`: `send_file` với `message.media` (danh sách cho album), `caption` và `formatting_entities` đã viết lại (`rewrite_caption`), `parse_mode=None`. Đi qua `guard.write` như mọi ghi, tính vào `daily_cap`. Mỗi unit là một batch, nên crash chỉ để lại đúng một unit `pending` và reconcile so được (không phụ thuộc chiến lược).
- **Chuỗi lùi** (`engine/reupload.py::send_unit_by_reference`, trong cùng lời gọi ghi nên batch vẫn `pending`): `FileRefExpired` (mã tham chiếu hết hạn, hay Telegram không cho dùng lại: `FILE_REFERENCE_*`, `FILE_ID_INVALID`, `MEDIA_EMPTY`, `MEDIA_INVALID`, `GROUPED_MEDIA_INVALID`) → đọc lại unit rồi thử lại một lần → vẫn lỗi thì tải xuống rồi tải lên lại đúng như `--mode reupload` (báo `run.reference_fallback`, xóa file tạm sau khi gửi). Tin đã bị xóa khỏi nguồn (`PerMessage` từ `fetch`) thì unit `failed`, như mọi đường khác.
- Không có tiến độ truyền file (không có gì được truyền).

### Chi tiết chiến lược B

- **Mỗi unit là một batch** (`Batch.strategy = REUPLOAD`, batcher phát ngay): một lần tải, một lần gửi, nên khi crash chỉ có đúng unit đó `pending` và `reconcile` so được (số tin, loại media, cấu trúc album).
- Tải về `<data>/tmp/run-<id>/<msg_id><đuôi>` (cộng `<msg_id>.thumb.jpg` cho video có ảnh bìa), gửi bằng `send_file` với `caption` + `formatting_entities` (và `parse_mode=None`: entity là định dạng, không parse markdown), giữ **thuộc tính của tệp gốc** (`attributes` của Document: video duration/w/h, audio, sticker, animated, filename; `mime_type`) và `force_document=True` **chỉ** cho tệp vốn là tệp thường (`MediaKind.DOCUMENT`, để một tệp .jpg không bị gửi lại thành ảnh); với video, GIF, video tròn, nhạc, voice, sticker thì **không** đặt `force_document` vì Telethon đổi nó thành `force_file` và Telegram sẽ hiện mọi thứ như tệp bất kể thuộc tính (lỗi người dùng gặp khi thử thật, 2026-09-20); video thường còn thêm `nosound_video=True` để video không có tiếng không bị đổi thành GIF, xóa file tạm ngay sau khi unit gửi xong. Thư mục tạm được dọn khi lần chạy bắt đầu (phần còn lại của lần bị kill) và khi kết thúc.
- Album (2026-09-22, sau spike throughput "đường album qua pool"): mỗi thành viên tải lên riêng — file đủ lớn (`Document`, cùng ngưỡng `min_bytes`/`BIG_FILE` như tin đơn) qua pool (`_upload_parallel`), còn lại (ảnh thật, file nhỏ, ảnh bìa) qua `client.upload_file` một kết nối — rồi `messages.UploadMediaRequest` (Telegram không nhận thẳng `InputMediaUploaded*` cho `SendMultiMediaRequest`, `MediaInvalidError`) đổi thành `InputMediaPhoto`/`Document`, gói vào `InputSingleMedia` (caption + entity riêng từng tin) và gửi một lần bằng `messages.SendMultiMediaRequest`; id tin khớp qua `UpdateMessageID.random_id`. Thuộc tính (Document `attributes`/`mime_type`, `force_file` theo `MediaKind` của từng tin, `nosound_video` cho video) và ảnh bìa (`item.thumb`, đã tải ở `prepare`) đến từ tin gốc, **giữ được riêng từng tin trong album** — không còn dùng `send_file([...])` của Telethon (gửi mọi file như nhau, đọc lại chi tiết video/audio bằng `hachoir`, và chỉ ép `force_document` được cho cả album cùng lúc, không theo từng tin).
- **Tải trước (pipeline)**: trong lúc unit hiện tại đang tải lên, unit kế có thể được đọc và tải xuống trước (`engine/reupload.py::Pipeline`, `[limits] prefetch`, `tmp_budget_mb` giới hạn tổng dung lượng, một unit lớn hơn ngân sách vẫn đi khi đĩa trống). Mặc định **đổi 1 → 0** (2026-09-23): đo lại với đúng hình dạng kết nối sản xuất (dùng chung, tái sử dụng, không mở mới cho mỗi file) cho thấy nhiều unit di chuyển cùng lúc vỡ sớm và nặng hơn một unit chạy một mình — xem `06-lo-trinh.md`, "chạy lại với hình dạng đúng"; `prefetch > 0` vẫn còn trong mã cho ai muốn thử lại. Chỉ nửa **đọc** chạy trước; ghi vẫn tuần tự, đúng thứ tự, một lời gọi một lúc (một account). Lỗi ở nền (FloodWait quá dài, UnsupportedMedia, đọc nguồn lỗi) được trao đúng thứ tự, sau các batch đi trước nó. Trong một file, part chạy song song dưới ngân sách request — **riêng cho mỗi chiều** từ 2026-09-23, không còn dùng chung (`core/pool.py`, `05-chong-flood.md`, "Pool request"): tải xuống một kết nối với tối đa `download_requests` (mặc định 4, hạ từ 8 sau khi một lần chạy thật gặp 429/mất kết nối liên tục ở 8) request đang bay, tải lên chia ra tới `upload_connections` kết nối (mặc định 8) với tối đa `upload_requests` (mặc định 8, đo sạch hai lần với một file tại một thời điểm, không tải xuống nào chạy cùng) request đang bay; `[limits] download_requests = 0` hay `upload_requests = 0` quay về cách cũ của Telethon cho chiều đó.
- Chi phí qua limiter: mỗi unit là một lần `pace` (giãn cách như mọi ghi, tính vào `daily_cap`) và `prepare` là một request đọc. Chưa có "delay theo dung lượng": không có số liệu, và một upload lớn vốn đã chậm; chỉnh sau khi có `flood_log` (spike 6).

### Kiểm lại nguồn khi chạy lại

`clone` kiểm `noforwards` trước khi làm gì (luật D3 ở trên), nhưng `run`/`retry` đi thẳng vào `begin_run` với cặp cũ, và nguồn có thể đã bật "Restrict saving content" từ lần trước. Vì chiến lược B tải nội dung xuống, `begin_run` đọc lại nguồn (một request chuẩn bị) trước mọi lần chạy có thể tải lên lại (`mode` reupload, hoặc `auto` với `--caption` khác `keep` hay với `topic_as_hashtag`: `strategy.may_reupload`): nguồn `noforwards` mà lần chạy chưa mang lời tuyên bố của user (`RunOptions.protected_ack`, ghi lúc `clone` được xác nhận và mang theo bởi `run`/`retry`) → `SourceRestricted` (mã 4) nếu tài khoản không phải admin, `NeedsAcknowledgement` (mã 2) nếu là admin; cả hai chỉ cách chạy lại bằng `clone ... --yes-i-administer-this-channel`. Có lời tuyên bố thì chạy tiếp dù tài khoản đã mất quyền admin: đó là trách nhiệm của user, nói một lần cho cả cặp (và mỗi lần chạy nhắc lại).

## Backup ra đĩa (Phase 11a, `engine/backup.py` + `engine/backupdir.py`)

Một chiều: không có đích Telegram, nên không dùng `Runner`/`msg_map`/write-ahead (lý do đầy đủ ở `04-state-checkpoint.md`, "Backup"). `engine/backup.py::begin_backup` (mở, giống `begin_run`) rồi `BackupWriter.run` (vòng lặp, giống `Runner._loop` nhưng đơn giản hơn nhiều):

- Đọc qua `FloodGuard` (luật 1): `planner.units(reader, ...)` như một lần chạy thường (cùng `plan_read`/`Matcher`, album không bị tách), nhưng không qua `batcher` — mỗi unit xử lý ngay khi tới (không cần gộp batch vì không có lời gọi Telegram để tiết kiệm, chỉ có tải xuống). Mỗi unit: `reader.export_unit(...)` (tải media vào `media/`, không qua `Window`/pipeline — v1 không tải trước) rồi `backupdir.append_records(...)`, rồi `Store.advance_backup(...)` (chỉ để hiển thị, xem `04-state-checkpoint.md`).
- D3 áp y như chiến lược B (`check_source_for_backup`, đọc lại nguồn — ngoại lệ luật 1 của bước chuẩn bị), vì backup luôn tải xuống.
- Tin đã bị xóa ở nguồn từ lúc liệt kê (`PerMessage` từ `export_unit`) không làm hỏng cả lần backup: bỏ qua, đếm vào `stats.gone`, con trỏ đi qua (không có khái niệm `failed`/`retry` cho backup ở v1).
- Điều khiển (pause/stop/resume, FloodWait, PeerFlood) dùng lại nguyên `engine.runner.RunControl` và `engine.flood.FloodGuard`; `FloodGuard` được tổng quát hóa nhẹ (`FloodOwner.of_run`/`of_backup`) để ghi `flood_log` đúng cột (`run_id` hay `backup_id`) thay vì bọc `Run` cụ thể.
- **Wizard** (`cli/commands/backup.py::BackupFlow`, cùng hình dạng `CloneFlow`): chọn nguồn, nhập thư mục (`wizard.ask_backup_dir`), D3 nếu cần (từ chối ngay tại bước này thay vì đợi `begin_backup`, như `plan_endpoints` làm cho `clone`), filter (bỏ qua nếu thư mục đã có backup — không hỏi lại, chỉ báo giữ filter cũ), xem trước, một câu xác nhận. `BackupFlow._check_existing` gọi `engine.runs.check_runnable` sớm (như `CloneFlow._read_history`) để từ chối một thư mục còn đang chờ hết FloodWait trước khi hỏi filter.
- **Chưa làm** (v1): không phân tích/ETA (không gọi `count`), không có dòng tiến độ truyền file (`ui/progress.py::BackupLineReporter.transfer` là no-op), không tải ảnh bìa video, không ghi `reply_to`, chưa nối vào `tgmirror history`, chưa có mục trong giao diện full-screen (menu).

## Restore từ đĩa lên kênh (Phase 11b, `engine/backup_reader.py`)

Ngược với backup: restore là một lần `run` bình thường, chỉ khác ở nguồn — `Runner`/`begin_run`/`reupload.py`/`msg_map`/write-ahead dùng lại y nguyên (khác Phase 11a, vốn không dùng `Runner` vì không có đích). Chiến lược luôn là `reupload` (không có "copy phía server" cho một thư mục).

- **`engine/backup_reader.py::BackupReader`** cài `MessageReader` đầy đủ, đọc `messages.jsonl` một lần vào bộ nhớ: `iter_messages`/`count` chỉ lọc theo `min_id`/`max_id`/ngày (tập cha an toàn theo hợp đồng `ServerFilter`, không lọc `media`/`search` — không có gì để tiết kiệm khi đã đọc hết cục bộ), `get_messages`/`list_topics` (từ `BackupManifest.topics`). `fetch`/`prepare` không tải gì (dữ liệu đã nằm sẵn trong `media/`): trả `Prepared(unit, files=(), handle=FromBackup(messages, media_dir, src_id))` — `files=()` là đủ để dọn dẹp (`Pipeline.finish`, `send_unit_by_reference`) không xóa gì, không cần cờ "không phải file của mình" như bản kế hoạch ban đầu dự đoán.
- **`FromBackup`** (`core/gateway.py`, không phải `telethon_gateway.py`, để `backup_reader.py` không phải import Telethon, luật 8) là `Prepared.handle` trung lập: `messages: tuple[ExportedMessage, ...]`, `media_dir`, `src_id` (để viết-lại-caption biết tên kênh gốc mà không cần một `custom.Message` sống). `TelethonGateway.send_prepared`/`upload_prepared` rẽ nhánh khi gặp `FromBackup`: `upload_prepared` không làm gì (không tối ưu tải trước cho v1), `send_prepared` dựng lời gọi `send_file`/`send_message` thẳng từ `ExportedMedia` — voice/video_note/document dùng cờ tiện lợi của `send_file` (`voice_note`/`video_note`/`force_document`), không dựng `DocumentAttributeX` thủ công (hachoir tự đọc thời lượng như khi tải lên lại tin sống); poll/quiz dựng `types.Poll`/`InputMediaPoll` mới hoàn toàn (không có phiếu bầu trong backup, nên luôn như đã bật `--reset-polls`; quiz cần `poll_correct_option` đã biết mới phục hồi được, như "quiz chưa trả lời" của reupload sống); geo/venue/contact dựng `InputMediaGeoPoint`/`InputMediaVenue`/`InputMediaContact` mới; text lấy qua `telethon.extensions.html.parse` (ngược với `unparse` mà `export_unit` dùng lúc backup).
- **Một core, một config cho việc tải lên, dùng chung giữa clone và restore** (sửa 2026-09-26, theo yêu cầu người dùng): file đơn đủ lớn (`_pooled_upload_path`, cùng ngưỡng `BIG_FILE`/`min_bytes`/`upload_requests` của `[limits]`) và mỗi thành viên đủ lớn trong một album (`_upload_album_member`) đều đi qua `_upload_parallel`/`RequestBudget` — **cùng một hàm, cùng một `TransferSettings`** mà tin sống dùng, không phải một bản sao riêng cho restore. `_album_media` (tin sống) và `_backup_album_member` (restore) chỉ còn là hai lớp mỏng trích `path`/`mime`/`attributes`/`thumb` từ hai nguồn khác nhau (một `_Item` gắn tin Telethon sống, hay một `ExportedMessage`) rồi gọi chung `_upload_album_member`; `_send_album` và `_send_backup_album` cùng gọi `_post_multi_media` (`SendMultiMediaRequest` + ánh xạ id) để đăng. Nhờ vậy album restore giờ cũng báo tiến độ qua `_album_progress` như tin sống. Xem "Phase 11b — ghi chú" (06-lo-trinh.md) để biết vì sao: bản đầu restore dùng `client.send_file(path)`/`send_file([...])` trực tiếp, một kết nối, người dùng đo được ~2.7 MB/s trên một file lớn.
- **`begin_run`** đọc `RunRequest.from_backup` (đường dẫn thư mục): có thì đọc `backupdir.read_manifest`/`last_id` thay cho `gateway.get_channel`/`last_message_id(src)`, và gọi `check_source_from_backup` (D3, chỉ có nhánh cờ/gõ nguyên văn — không có nhánh "admin xác nhận thường" như `check_source`, vì không còn kênh sống để kiểm `is_admin`). `RunOptions.from_backup` là thuộc tính của **lần chạy**, không phải của cặp (giống `retry_of`, không nằm trong `for_pair`): sau khi restore xong, cặp đó có thể quay lại là một clone sống bình thường bằng `clone`/`run` không có `from_backup`.
- **`Runner(reader_override=...)`**: khi có, phía đọc **nguồn** dùng nó thay cho `guard.reader(gateway)`; phía đọc **đích** (trong `reconcile`, để dò "tail" sau một crash) luôn qua gateway thật bằng một reader riêng (`self._dst_reader`) — không thể dùng chung một reader cho cả hai phía như trước phase 11b, vì phía nguồn của một restore không biết đọc kênh Telegram thật.
- **Mirror chung với clone trực tiếp**: `mirrors` chỉ khóa duy nhất theo `(src_id, dst_id)`, không theo `mode` — nên một restore (nguồn = id kênh gốc lấy từ `backup.json`, `mode=reupload`) và một lần `clone`/`run` trực tiếp cùng kênh gốc vào cùng đích chia sẻ đúng một mirror/`msg_map`, chống trùng tự nhiên (id tin giữ nguyên qua backup).
- **Wizard** (`cli/commands/restore.py::RestoreFlow`, cùng hình dạng `BackupFlow`/`CloneFlow`): chọn thư mục (`wizard.ask_restore_dir`), chọn đích (có sẵn, hoặc tạo mới đặt tên thẳng theo `manifest.src_title` — không hỏi tên/about như `clone`, vì backup Phase 11a chưa từng ghi `about`/avatar), D3 (chỉ cờ/gõ nguyên văn, luôn hỏi lại dù `backup.json` đã có `protected_ack`), filter, xem trước, một câu xác nhận. `tgmirror run`/`tgmirror retry` tiếp tục một restore bị dừng qua `cli/commands/run.py::reader_override_for` (dựng lại `BackupReader` từ `RunOptions.from_backup` của lần chạy trước).
- **Chưa làm** (v1, cùng lý do Phase 11a): không phân tích/ETA riêng, không dòng tiến độ cho một album đang gửi (`send_file` nhiều file không có `progress_callback` lộ ra), không avatar, chưa có mục trong giao diện full-screen (menu).

## Loại nguồn: broadcast, supergroup, forum

`ChannelInfo.kind`: `broadcast` · `supergroup` · `forum` (supergroup bật topics) · `group` (basic group cũ). Cả bốn loại đều là nguồn hợp lệ (quyết định 2026-09-19). Cùng một engine, chỉ khác ở gateway và cách chọn đích:

| Nguồn | Đích tạo mới | Ghi chú |
|---|---|---|
| `broadcast` | broadcast channel | Luồng gốc, không topic |
| `supergroup` / `group` | supergroup | Basic group không tạo mới được nên đích luôn là supergroup |
| `forum` | supergroup bật forum | Ánh xạ topic → topic (bên dưới) |

### Đích khác loại nguồn (phase 8, đảo "chốt 2026-09-19")

Đích có sẵn **không** còn phải cùng loại với nguồn: `engine/endpoints.py::eligible_destinations` chỉ còn kiểm "không phải chính nguồn" và "admin + đăng được". Mọi cặp loại (kể cả group ↔ channel) đều hợp lệ, vì `drop_author` đã bỏ danh tính người gửi như nhau cho mọi loại — thứ duy nhất mang cấu trúc mà loại khác không có chỗ chứa là **topic của forum**:

- **forum → forum**: ánh xạ topic đầy đủ (mục dưới).
- **forum → không phải forum**: `Plan.warnings` có `"topic_loss"`. Với `auto` (mọi caption, kể cả `keep`) hoặc `reupload`, wizard hỏi "giữ tên topic dưới dạng hashtag?" (mặc định có, `RunOptions.topic_as_hashtag`/`--topic-as-hashtag`) — hashtag cộng vào sau caption/text (`CaptionPolicy.hashtag`), trừ tin tự chứa (poll/quiz/location/contact/geo/dice, xem "Tin đặc thù"); album chỉ mang **một** hashtag, trên caption đầu tiên còn lại (hoặc ảnh đầu nếu không có caption), để Telegram vẫn hiện nó là caption của cả album. Forward không thêm được hashtag, nên dưới `auto` mọi unit có topic (ngoài General, có chỗ caption) được **gửi lại** thay vì forward: bằng mã file khi nguồn cho lưu nội dung, tải xuống rồi tải lên khi nguồn `noforwards` (vì vậy `topic_as_hashtag` trên nguồn `noforwards` cũng cần lời tuyên bố D3). Với `--mode copy`, không có gì viết lại được nên topic luôn bị bỏ hoàn toàn — `cli/commands/clone.py::_confirm_topic_loss` hỏi xác nhận trước (mặc định không; `--yes` hoặc `--no-topic-as-hashtag` đủ để đồng ý); `--topic-as-hashtag` cùng `--mode copy` là lỗi dùng (mã 2). `run`/`retry` mang tiếp `topic_as_hashtag` của lần chạy trước. Với đích là forum, cờ bị bỏ qua (topic được ánh xạ thật).
- Đích **mới** (`--dst-new`) luôn theo đúng loại nguồn (bảng trên) — không có chat có sẵn để chọn loại khác, nên `topic_loss` không bao giờ xảy ra cho đích mới.

### Ánh xạ topic (forum → forum)

- **Tạo kiểu lười** (`engine/topics.py::TopicResolver`), không liệt kê và tạo sẵn mọi topic nguồn: topic đích được tạo đúng lúc tin đầu tiên cần gửi vào đó xuất hiện (tra tên qua `list_topics`, tạo qua `create_topic`, lưu `src_topic_id → dst_topic_id` vào `topic_map`, `04-state-checkpoint.md`, cùng một nhịp không có gì chen giữa — không phải một transaction SQL thật sự bọc quanh lời gọi Telegram, luật 5 cấm điều đó). Topic mới xuất hiện ở nguồn sau này đi đúng đường này, không cần xử lý riêng. Kết quả tương đương "tạo sẵn ở lần đầu" mà không cần hai đường code.
- **General (topic nguồn id 1) không cần ánh xạ hay lời gọi nào.** Telethon không gắn `reply_to` cho tin ở General (chỉ tin trong các topic khác mới có `reply_to.forum_topic=True`), nên `SrcMessage.topic_id` của một tin General là `None` — giống hệt tin của nguồn không phải forum. `topic=None` được truyền cho `copy_messages`/`send_prepared`/..., Telegram tự định tuyến vào General (**chưa kiểm chứng trên tài khoản thật**, câu hỏi mở số 9 ở `06-lo-trinh.md`).
- Duyệt nguồn theo **id tăng dần toàn group** (id là chung cho mọi topic), nên `cursor_src_id` vẫn là một số duy nhất. Mỗi tin được định tuyến theo topic của nó (`msg_map.src_topic_id`).
- Một lời gọi forward/gửi chỉ có một topic đích. `engine/batcher.py` cắt batch khi `Unit.topic_id` đổi (như khi chiến lược đổi), nên nhóm chat xen kẽ nhiều topic sẽ có batch nhỏ hơn (chậm hơn, nhưng thứ tự trong từng topic vẫn đúng).
- `forward_messages` của Telethon **không** có tham số topic: `copy_messages` giữ nguyên đường đó khi không có topic (đã kiểm chứng từ phase 2), chỉ chuyển sang gọi thẳng `ForwardMessagesRequest(top_msg_id=...)` khi có (vẫn nằm trong gateway + limiter, luật 1) — giảm rủi ro cho đường code cũ. `send_prepared`/`send_by_reference`/`send_text` truyền `reply_to=<dst topic id>` (Telethon không hỗ trợ `top_msg_id` qua API công khai của chúng, chỉ `reply_to_msg_id`; dùng id của tin định nghĩa topic làm `reply_to` thay thế). **Chưa kiểm chứng trên tài khoản thật** (câu hỏi mở số 9, `06-lo-trinh.md`).

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

### Loại tin khi đổi loại đích (phase 8)

Ngoài topic, đổi loại đích (mục trên) không tạo khác biệt nào khác đã biết trước cho hầu hết loại tin — `drop_author` đã bỏ danh tính người gửi như nhau cho mọi loại rồi:

| Loại tin | Ảnh hưởng khi đổi loại đích | `topic_as_hashtag` |
|---|---|---|
| Ảnh/video/tài liệu/audio/voice/gif, tin chữ | Không có | Áp dụng bình thường |
| Sticker/video_note | Không có | Không có chỗ caption: dưới `auto` vẫn forward (không hashtag); `reupload` vẫn thử gắn |
| Poll | Group cho phép `public_voters` (hiện tên người bình chọn), poll trong broadcast channel theo Telegram luôn ẩn danh — đem một poll đã public từ group sang broadcast, Telegram giữ/ép ẩn danh/từ chối? **Chưa biết, thêm vào câu hỏi mở cùng spike 9** (`06-lo-trinh.md`); không tự sửa `public_voters`, không chặn, gửi như hiện nay | Không áp dụng: không có chỗ caption |
| Quiz, Location/Venue/Contact/Geo/Dice | Không có khác biệt nào biết trước | Không áp dụng: không có chỗ caption |
| Game/Invoice | Vẫn bị bỏ như hiện nay, không đổi bởi phase 8 | Không áp dụng (tin không được gửi) |
| Service message | Luôn bị bỏ qua, không phụ thuộc loại đích | Không áp dụng |

Không thêm logic chặn hay tự sửa hành vi Telegram cho trường hợp "chưa biết" (dòng poll): gửi như bình thường và để spike 9 trả lời sau, giống cách project xử lý mọi hành vi Telegram khác chưa kiểm chứng.

## Unit và Batch

- **Unit**: một tin đơn, hoặc **toàn bộ album** (cùng `grouped_id`). Đơn vị không bao giờ bị tách.
- **Batch**: tập Unit gửi trong một lời gọi (tổng tin <= `batch_size`), tất cả cùng một chiến lược (`Batch.strategy`). Album lớn hơn `batch_size` vẫn gửi nguyên trong một batch (tối đa 10 tin/album nên không vượt 100). Unit đi đường tải lên lại luôn là một batch riêng, phát ngay khi tới (không đợi unit sau).
- Filter đánh giá trên Unit (xem `03-filters.md`). Unit không khớp thành một `Skip(count, last_id)` (planner); batcher cộng dồn `Skip` vào `Batch.skipped`/`Batch.upto` của batch đang được phát: con trỏ của batch (`Batch.last_id`) được vượt qua chúng vì chúng đều đứng trước unit chưa vào batch. Một batch đang chờ được phát ra sau mỗi `FLUSH_AFTER` = 500 tin bị loại (kể cả batch chỉ có `Skip`, không unit nào), để quãng dài không có tin khớp vẫn lưu tiến độ và nghe được pause/stop.

## Vòng lặp runner

Bản dưới là pseudo-code; cài đặt thật ở `engine/runner.py` + `engine/flood.py` (phase 4). Lần chạy được mở trước bởi `engine/runs.py::begin_run` (một transaction: tìm hoặc tạo mirror của cặp, chọn filter, chặn nếu tiến trình khác đang giữ cặp); runner rồi: nạp `limiter_state` → reconcile → **analyze** (`_analyze`, xem mục dưới) → vòng `planner` → `batcher` → (bỏ unit đã `done`) → `guard.pace` (limiter, ngủ ngắt được) → kiểm `pause`/`stop` (`_gate`: `stop` thì thoát; `pause` thì **giữ tại chỗ** — trạng thái `paused`, tiến trình và heartbeat vẫn sống — cho đến `run`/phím `r` hoặc `stop`) → write-ahead → `guard.write(copy_batch)` (FloodWait: log, AIMD, ngủ, gửi lại **đúng lời gọi đó**) → `commit_batch` (kèm `limiter_state`). Lần chạy có thể tải lên lại (`mode` reupload, hoặc `--caption` khác `keep`) đọc batch qua `Pipeline`: `_make` (đọc `done` để bỏ unit đã xong, `plan_unit`, giữ chỗ trong `Window`, `reader.prepare`) chạy ở nền cho batch kế trong lúc batch này gửi; consumer làm đúng thứ tự gate → (unit là tài liệu/video lớn: `guard.check_cap` rồi `guard.transfer(upload_prepared)`, đẩy byte, chưa có `pending`) → `pace` (trừ thời gian đã đẩy) → write-ahead → `guard.write(send_prepared)` (chỉ đăng nếu đã đẩy) → `commit_batch`, rồi xóa file và trả chỗ. Unit bị bỏ (`DROP`) không qua `pace`/write-ahead thật mà ghi ngay (`begin_batch` + `commit_batch` không gọi Telegram); placeholder qua `pace` và `guard.write(send_text)`. Lần chạy chỉ forward đọc trực tiếp như trước. Mọi lần đọc của runner (planner, reconcile, prepare) đi qua `guard.reader(gateway)`, cùng giãn cách và xử lý FloodWait (đọc tiếp từ tin cuối đã trao). `Runner._nap` ném `Interrupted` khi có `stop`/Ctrl+C nên không lời gọi nào đi tiếp sau đó; `pause` không cắt ngang lúc chờ mà có hiệu lực ở ranh giới batch kế tiếp.

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

## Analyze và tiến độ (2026-09-20)

Để hiện "x trên y" và tiến độ từng file, runner biết **bao nhiêu việc** và **file nào đang chạy tới đâu**. Người dùng chốt phạm vi: analyze chỉ **đếm số tin**, chạy mặc định; dung lượng không quét trước mà biết dần từng file (`SrcMessage.size` có sẵn khi đọc tin, tiến độ byte lấy từ chính lần truyền); không có IPC giữa các terminal (mỗi terminal là một process, `status` chỉ đọc DB).

- **Đếm** (`Runner._analyze`, sau reconcile, trước vòng đọc): `reader.count(src, min_id=plan.min_id, filters=plan.server)`, cùng khoảng và cùng phần lọc mà `iter_messages` sẽ dùng. Telethon: `messages.search` với `limit=1`, `q` và filter media của lần đọc (filter rỗng nếu không có gì để đẩy lên). **Telegram áp filter nhưng bỏ qua `min_id`/`max_id` khi tính `count`** (spike 10), nên khoảng được đếm theo *vị trí*: `offset_id_offset` của câu trả lời khi hỏi `offset_id = x` là số tin khớp có id ≥ x, và hiệu hai vị trí (`x = min_id + 1` và `x = max_id + 1`) là số tin trong khoảng: 1 request lấy tổng, thêm tối đa 2 cho hai đầu khoảng. Telegram không nói vị trí thì kết quả quay về tổng cả kênh (vẫn là cận trên). Ngày `since`/`until` đổi thành id như khi đọc (thêm tối đa hai request) nhưng **không** có lề album. Kết quả là **cận trên**: tin service được đếm, phần filter phía client loại đi không bị trừ; `min(count, src_last_id - min_id)` chặn nó bằng khoảng id đã ghi lúc bắt đầu (chặt nhất khi không có vị trí, ví dụ lần chạy không còn gì mới). Ghi vào `RunOptions.total_items` (`Store.set_total`); của riêng lần chạy, không vào `mirrors.options_json` (`for_pair`). `retry` không cần đếm: tổng là số tin lỗi.
- **Không qua `pace_read`**: là một lời gọi mở đầu lần chạy, cùng loại với các lần đọc chuẩn bị của `begin_run` (ngoại lệ luật 1); FloodWait vẫn được ghi, chờ hoặc dừng lần chạy như mọi lời gọi khác (`_GuardedReader.count`). Bucket đọc bắt đầu từ trang tin đầu tiên. Lỗi Telegram khác FloodWait/PeerFlood không làm hỏng lần chạy: tổng chưa biết (`total_items = 0`) và `status` quay về tính theo id.
- **Tiến độ = số tin đã xử lý trên tổng.** `Run.handled` = `done + failed + skipped_filter + skipped_unsupported + gone + already_done`. `already_done` là tin lần chạy vượt qua vì cặp đã có (đọc lại từ đầu sau khi đổi filter, `_without_done`); nếu không đếm thì phần trăm đứng yên ở đó. Tổng là cận trên nên lần chạy bỏ qua nhiều tin chỉ tới 100% khi `DONE`; `handled > total` thì tổng nâng lên bằng `handled`.
- **Cap ngày**: `cap_days = ceil((còn lại - số tin cap còn cho phép hôm nay) / daily_cap)`, in một lần sau khi đếm (`run.cap_days`) và ở `status`. Đây là điều quyết định thời gian thật của clone lớn, không phải tốc độ truyền.
- **Tiến độ truyền file**: gateway gọi `on_transfer(phase, msg_id, bytes_done, bytes_total)` mỗi part (Telethon `progress_callback`, hoặc phần của pool báo thẳng bằng byte). Album: mỗi tin tải lên riêng báo byte thật của chính nó; gateway cộng dồn qua các tin đã xong rồi báo dưới id tin đầu của album, trên tổng dung lượng cả album (`_album_share`) — không còn qua đếm file của Telethon (mục "Album" ở trên). `engine/transfer.py::TransferTracker` tính tốc độ trong 5 giây gần nhất (để chỗ đứng hình hiện ngay) rồi đưa `Transfer` cho `Reporter.transfer`. Không ghi DB, không nhớ qua lần chạy. `LineReporter` chỉ nói về file ≥ 8 MB: một dòng khi bắt đầu, mỗi 5 giây, và khi xong. Tải xuống và tải lên có thể chạy cùng lúc (tải unit kế trong lúc gửi unit này).
- **Trần cho việc tải trước** (tải nhanh hơn gửi thì không được dồn lên đĩa hay RAM): `Window` giữ chỗ **trước** khi tải và chỉ trả khi unit đã gửi xong hoặc bị bỏ; trần theo số unit (`prefetch + 1`) và byte (`tmp_budget_mb`). Hai kẽ hở đã vá: file không có `size` được giữ chỗ `UNKNOWN_SIZE` (1 MiB) thay vì 0 (`reserve_size`), và sau khi tải xong, byte thật trên đĩa được đối chiếu với chỗ đã giữ, phần dôi ra được ghi ngay (`Window.grow`) để unit kế phải chờ. Tải xuống của Telethon ghi thẳng ra đĩa từng part nên RAM chỉ cỡ một part; bộ lập lịch part về sau (`RequestBudget`, xem `06-lo-trinh.md`) phải giữ đúng trần này cho cả RAM: số request đang bay × cỡ part.

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
  engine/   endpoints.py  runs.py  planner.py  batcher.py  strategy.py  copy.py  reupload.py  flood.py  reconcile.py  preview.py  runner.py  status.py  topics.py  backup.py  backupdir.py  backup_reader.py
  filters/  model.py  parser.py  pushdown.py  matcher.py
  store/    schema.sql  db.py  runs.py  msgmap.py  floodlog.py  limiterstate.py  topicmap.py  backups.py
  cli/      app.py  wizard.py  filter_options.py  runtime.py  errors.py  interrupt.py  keys.py  commands/ (auth.py channels.py topics.py clone.py run.py retry.py status.py control.py history.py config.py backup.py restore.py ...)
  ui/       messages.py  prompts.py  tables.py  progress.py  tui.py
            menu/  (app full-screen: app, screen, prompter, widgets, run_screen, screens/)
tests/      fakes.py (FakeGateway, FakeAuth, ScriptedPrompter)  unit/  integration/
```

## Kiểm thử

- `FakeGateway` mô phỏng kênh (danh sách tin, album, lỗi FloodWait/PeerFlood theo kịch bản) và đồng hồ giả cho limiter.
- Test bắt buộc: resume sau khi kill giữa chừng không trùng/sót; album không bị tách; FloodWait làm tăng delay; PeerFlood dừng lần chạy; delta chỉ lấy tin mới; pause giữ tại chỗ rồi chạy tiếp. Phase 2 có `tests/integration/test_runner.py`, `tests/unit/test_store.py`, ...; phase 4 thêm `tests/unit/test_limiter.py` (đồng hồ giả) và `tests/integration/test_runner_flood.py` (kịch bản flood trên `FakeGateway`); phase 5 thêm `tests/integration/test_runner_retry.py` (retry: kill giữa chừng, bị từ chối, album, tin đã xóa), `tests/unit/test_status.py` (ước lượng tiến độ/ETA) và `tests/unit/test_cli_retry.py` (`retry`, `status`); phase 6 thêm `tests/integration/test_runner_reupload.py` (mỗi unit một lần gửi, tải trước, ngân sách đĩa, dừng khi đang tải, caption, tin không hỗ trợ, flood, kill trước/sau khi gửi), `tests/unit/test_reupload.py` (router, batcher, `plan_unit`, `Window`, `Pipeline`), `tests/unit/test_telethon_reupload.py` (tải/gửi/viết lại caption trên client giả) và `tests/unit/test_cli_reupload.py` (cờ, D3, wizard); phase 8 thêm `tests/integration/test_runner_topics.py` (cắt batch theo topic, ánh xạ + tạo topic, General không cần lời gọi, hashtag khi đích không phải forum), mở rộng `test_endpoints.py` (ma trận cross-kind), `test_planner.py` (cắt batch theo topic), `test_store.py` (`topic_map`, migration v2 `dst_kind`), `test_filter_model.py`/`test_filter_matcher.py`/`test_filter_parser.py` (`from_user`/`topic`), `test_telethon_gateway.py`/`test_telethon_messages.py`/`test_telethon_reupload.py` (tạo kênh theo `kind`, forward/gửi theo topic, hashtag không áp dụng cho tin tự chứa) và `test_cli_clone.py`/`test_cli_filters.py`/`test_cli_topics.py` (mới); test kiến trúc (`tests/unit/test_architecture.py`) giữ Telethon và SQL trong đúng chỗ.
- Phase 11a thêm `tests/unit/test_backupdir.py` (định dạng thư mục backup, thuần I/O: manifest, JSONL, dòng cuối hỏng), `tests/integration/test_backup.py` (album/topic/poll, D3, dừng rồi tiếp tục không trùng/sót, filter đổi bị từ chối, pause/stop từ store), mở rộng `tests/unit/test_store.py` (bảng `backups`, migration v3, `flood_log.backup_id` tách khỏi `run_id`) và `tests/unit/test_cli_backup.py` (cờ, D3, `--force-takeover`, `pause`/`stop` chạm tới backup).
- Phase 11b thêm `tests/unit/test_backup_reader.py` (`BackupReader` thuần, không gateway: thứ tự, `min_id`/`max_id`/ngày, quiz chưa biết đáp án), `tests/integration/test_restore.py` (`FakeGateway` làm đích, thư mục backup dựng tay làm nguồn: text/photo/album/topic/poll đúng thứ tự, dừng rồi tiếp tục không trùng/sót, D3 hỏi lại dù backup đã ack, `retry`, một restore và một clone trực tiếp cùng nguồn gốc chia sẻ một mirror/`msg_map`), `tests/unit/test_telethon_restore.py` (client Telethon giả: mọi loại media tự chứa và có file, album, HTML round-trip, strip-links) và `tests/unit/test_cli_restore.py` (cờ, D3, wizard song song với cờ, thư mục không phải backup bị từ chối).
- Không test tự động chống lại Telegram thật. Có script thủ công `scripts/smoke.py` dùng kênh test riêng.
- Kiểm tra đột biến thủ công: `scripts/mutation_check.py` (phase 6) phá từng cơ chế của chiến lược B/D3 trên **bản sao tạm** của `src/` và `tests/` rồi xem có test nào đỏ; mỗi lần chạy có timeout riêng nên một đột biến làm test treo cũng chỉ tính là "bị phát hiện". `--list`, `--only <chữ>`. Một dòng `SURVIVED` là cơ chế không test nào bảo vệ (hoặc đột biến tương đương): đọc nó, đừng chỉ làm nó xanh. Test có timeout 60 giây (`pytest-timeout`).
