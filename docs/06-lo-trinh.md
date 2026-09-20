# 06 — Lộ trình & câu hỏi mở

## Trạng thái hiện tại

Cập nhật bởi skill `doc-sync` khi một phase bắt đầu/kết thúc.

- [x] Phase 0 — Scaffold (2026-09-19)
- [x] Phase 1 — Login, channels, tạo kênh (2026-09-19)
- [x] Phase 2 — Copy + state + pause/resume (2026-09-20; đã chạy được trên Telegram thật với kênh cho phép forward, kênh `noforwards` mới thử phía không phải admin, xem "Phase 2 — ghi chú")
- [x] Phase 3 — Filters (2026-09-20; pushdown và cơ chế giữ album nguyên vẹn được kiểm bằng `FakeGateway`, chưa thử trên Telegram thật, xem "Phase 3 — ghi chú")
- [ ] Phase 4 — Limiter & flood
- [ ] Phase 5 — Delta sync
- [ ] Phase 6 — Reupload
- [ ] Phase 7 — TUI, doctor, đóng gói
- [ ] Phase 8 — Group, supergroup, forum topics

## Lộ trình

| Phase | Nội dung | Hoàn thành khi |
|---|---|---|
| 0 | Scaffold: `pyproject.toml` (uv), ruff, pytest, `TelegramGateway` protocol + `FakeGateway`, config/paths | `pytest` chạy được với FakeGateway |
| 1 | `tgmirror login`, `tgmirror channels`, tạo kênh đích mới, wizard chọn nguồn/đích | Đăng nhập thật, liệt kê kênh, tạo được kênh |
| 2 | Strategy A (copy) + SQLite state + pause/resume/stop; album; reconcile pending | Kill giữa chừng rồi resume không trùng/sót (test) |
| 3 | Filters: media/hashtag/regex/date/id/size + server pushdown | Bộ test filter + so sánh pushdown vs full-scan |
| 4 | Limiter AIMD, FloodWait handler, PeerFlood, daily cap, long pause, `flood_log` | Test với FakeGateway kịch bản flood |
| 5 | `tgmirror sync` (delta), `sync --all`, `retry`, `status`, `jobs` | Delta chỉ lấy tin mới |
| 6 | Strategy B (reupload) cho admin-owned `noforwards`/biến đổi caption; uploader song song | Clone kênh thử có video lớn |
| 7 | TUI đẹp hơn, `doctor`, đóng gói (pipx/uv tool), tài liệu người dùng | Cài được bằng một lệnh |
| 8 | Nguồn group/supergroup/forum: `ChatKind`, ánh xạ topic (`topic_map`), forward vào topic đích, filter `topic`/`from_user` | Clone thử một forum có nhiều topic, đúng topic và đúng thứ tự trong từng topic |
| 9+ | Đồng bộ edit/delete | Theo nhu cầu |

### Phase 1 — ghi chú

Đã có: `login`/`logout`/`whoami`, `channels` (`--search`, `--writable`, `--json`), `new` (bước 1–2 của wizard: chọn nguồn, chọn hoặc tạo đích), `TelethonGateway`/`TelethonAuth`, `--debug`, UI tiếng Việt/Anh (`TGMIRROR_LANG`). Tất cả test bằng `FakeGateway`/`FakeAuth`/`ScriptedPrompter` và stub client Telethon, không cần mạng.

- **`new` chưa lưu job (lúc đó)**: phase 1 dừng sau khi chốt cặp nguồn/đích (và tạo đích nếu được yêu cầu, luôn có xác nhận hoặc `--yes`). Phase 2 đã nối `plan_endpoints`/`materialize` vào `create_job` và lưu job.
- **Chưa có limiter**: `core/limiter.py` là phase 4. Các lời gọi phase 1 (`iter_dialogs`, `get_entity`, `CreateChannelRequest`, đăng nhập) là thao tác đơn lẻ do người dùng khởi động; FloodWait/PeerFlood được ánh xạ sang `FloodWait`/`PeerFlood`, in một câu và thoát mã 3, không retry. Luật 1 (mọi lời gọi qua limiter) chỉ thực sự áp dụng từ phase 4; không thêm lời gọi ghi hàng loạt trước đó. Phase 2 thêm lời gọi ghi hàng loạt đầu tiên (copy) nên có limiter tạm thời (ghi chú phase 2).
- **Đã kiểm chứng với Telegram thật (2026-09-19, người dùng chạy tay trên Windows/PowerShell)**: `login`, `channels`, `new --src ... --dst-new ...` (kênh broadcast được tạo, có hỏi xác nhận). Còn chưa thử trên account thật: nguồn/đích supergroup, forum, group thường (nên `can_post`/`is_admin` suy từ entity, spike 4, mới xác nhận cho broadcast). Lưu ý PowerShell: `@name` phải đặt trong dấu nháy.

### Phase 2 — ghi chú

Đã có: `store/` (`Store` trên SQLite: `jobs`, `msg_map`, `flood_log`; migration bằng `PRAGMA user_version`), `engine/` (`planner` → `batcher` → `copy` → `runner`, cùng `reconcile` và `jobs`), `TelethonGateway.iter_messages`/`copy_messages`/`last_message_id`, `core/limiter.py` (tạm thời), các lệnh `new` (lưu job; `--name`, `--mode`, `--batch-size`, `--run/--no-run`), `run` (`--force-takeover`), `pause`, `stop`. Tất cả test bằng `FakeGateway` cùng file SQLite thật trong `tmp_path`, không cần mạng. Tiêu chí "kill giữa chừng rồi resume không trùng/sót" được kiểm bằng `tests/integration/test_runner.py`: runner bị giết ngay sau write-ahead (trước lời gọi copy) hoặc ngay sau copy (trước commit), ở từng batch, rồi resume: mỗi tin xuất hiện đúng một lần ở đích, đúng thứ tự.

Các lựa chọn khi làm (không phải D1–D9):

- **Limiter tạm thời** (`core/limiter.py`): chỉ giãn cách batch (`min_delay` ± `jitter`, nghỉ dài mỗi `long_pause_every` tin). AIMD, `daily_cap`, `limiter_state` là phase 4 sau cùng giao diện `acquire(cost)`.
- **FloodWait chưa được chờ**: batch bị từ chối được xóa khỏi `pending` (Telegram không tạo gì), job thành `waiting_flood` + `resume_at`, ghi `flood_log`, thoát mã 3; `run` từ chối chạy lại trước `resume_at`. Chờ tự động (`max_auto_wait`) là phase 4. PeerFlood: job `failed(peer_flood)`, `run` từ chối trong 24h kể từ lúc đó.
- **Một job cho mỗi cặp nguồn/đích**: `new` từ chối tạo job thứ hai cho cùng cặp (mã 2, chỉ tới job đã có), vì nó sẽ copy mọi thứ hai lần.
- **`run` chạy được với job `done`**: nhặt tin mới sau `cursor_src_id`. Phase 5 chỉ thêm `sync` (và `sync --all`) bọc quanh cùng đường đó.
- **Tin `None` từ `copy_messages`** (id đã bị xóa ở nguồn): sau một lời gọi thành công nghĩa là Telegram không tạo tin nào, nên ghi `failed` với `reason='not_copied'` (không phải "chưa rõ"; chỉ lời gọi bị ngắt giữa chừng mới cần reconcile).
- **Batch bị `PerMessage`** (Telegram từ chối chính các id): xóa `pending` của batch rồi gửi lại từng unit một, để một tin hỏng không kéo theo cả batch. Unit đơn lẻ vẫn lỗi thì ghi `failed` cả unit (cả album).
- **Lời gọi bị ngắt (`Transient`)**: giữ nguyên `pending`, job `failed('transient')`; lần `run` sau reconcile.
- **`last_message_id`**: thêm vào protocol để `new` ghi `options.dst_base_id` (id tin mới nhất của đích lúc tạo job); reconcile chỉ đọc đích sau `max(dst_msg_id đã done, dst_base_id)`, không quét cả kênh đích cũ.
- **Tiến độ** là dòng chữ thường (`ui/progress.py`, không ANSI, tối đa một dòng/5 giây). TUI Rich có phím `p`/`q` là phase 7. Ctrl+C lần một: xong batch hiện tại, lưu, thoát mã 130; lần hai: thoát ngay.
- `MediaKind` thêm `geo`, `contact`, `game`, `invoice` cho khớp danh sách giá trị `media` của `03-filters.md`; loại lạ (dice, ...) tạm xếp vào `document`.

Đã kiểm chứng (2026-09-20, người dùng chạy tay): `new`/`run` copy được từ một kênh cho phép forward. Cũng đã chạy đúng trên tài khoản thật: **album** (forward cả danh sách id giữ nguyên album ở đích, xong spike 2) và **kill giữa chừng rồi resume** (reconcile trên đích thật, không trùng/sót). Nguồn `noforwards` là group mà user không phải admin: `new` từ chối đúng như thiết kế (thông báo `err.source_restricted`, D3, mã 4), tức là `Chat.noforwards`/`Channel.noforwards` được nhận ra ở group. Chưa thử: nguồn `noforwards` mà user là admin (cảnh báo `noforwards_admin`, rồi `run` gặp `ForwardsRestricted` → mã 4 mới chỉ test bằng fake). Còn lại chưa kiểm chứng (người dùng chạy tay):

- `forward_messages` trả `None` cho id đã xóa và ném `MessageIdInvalidError` khi mọi id đều đã xóa: đọc từ mã Telethon 1.45, chưa thử thật.
- `get_input_entity(marked id)` trong `run` dựa vào cache entity trong file session (do `iter_dialogs` của `new`/`channels` ghi). Nếu thiếu, gateway báo `NoPermission` "not accessible": chạy `tgmirror channels` để làm mới.
- Spike 7 (poll, quiz, ... khi forward) vẫn mở: phase 2 chuyển mọi tin, không tiền kiểm theo loại.

### Phase 3 — ghi chú

Đã có: package `filters/` (`model`, `parser`, `matcher`, `pushdown`), `engine/preview.py`, `Skip`/`Batch.skipped` ở planner/batcher, `ServerFilter.until`, `TelethonGateway.iter_messages` với pushdown thật và `src_message` điền `hashtags`/`size`/`duration`/`mime`/`views`, `Store.replace_filters`, cờ lọc của `new` và `run --refilter`, `--preview`, `--no-pushdown`, bước 3 của wizard (`Prompter.checkbox`). Thêm dependency `pyyaml` (file `--filter-file`) và `regex` (timeout chống regex bùng nổ mà `re` không có). Tiêu chí "bộ test filter + so sánh pushdown với quét đầy đủ": `tests/unit/test_filter_*.py` và `tests/integration/test_pushdown_equivalence.py` (kênh ngẫu nhiên có album, 17 filter × 12 kênh; đã thử đột biến: bỏ hoàn thiện album hoặc bỏ lề id thì đỏ), cùng `tests/integration/test_runner_filters.py` (kill/resume, pause trong quãng dài không khớp, `--refilter`).

Các lựa chọn khi làm (không phải D1–D9; chi tiết ở `03-filters.md`):

- **Pushdown không được cắt album**: lề `ALBUM_MARGIN` cho ranh giới id/date; khi đẩy `media`/`search` thì planner hoàn thiện album bằng một lần đọc không lọc. Không đẩy `contains` (search theo từ, matcher theo chuỗi con) và `document`/`sticker`/`webpage`.
- **Ngày → id ở gateway** (`get_messages(offset_date=..., reverse=True, limit=1)`), thêm `ServerFilter.until` để `--until` cũng giảm số tin phải đọc.
- **Cursor và `skipped_filter`**: tin bị loại được cộng vào batch đang phát; batcher phát batch sau mỗi 500 tin bị loại (`FLUSH_AFTER`) nên quãng dài không khớp vẫn lưu tiến độ và nghe được pause/stop. Tin bị server loại (pushdown) không được đếm.
- **`--refilter` là cách duy nhất đổi filter** vì mỗi cặp nguồn/đích chỉ một job và `rm` là phase 5; nó là chỗ duy nhất cursor lùi.
- Sửa một lỗi phát hiện khi viết test: `size` được chuẩn hóa thành số byte trần nhưng số trần bị từ chối khi nạp, nên filter đã lưu không nạp lại được; nay lưu dạng `"<byte>B"`.

Chưa kiểm chứng (người dùng chạy tay): tất cả hành vi thật của Telegram ở spike 3 (`search` + `filter` + `reverse` + `min_id`, `get_messages(offset_date, reverse)`, tokenize hashtag, entity hashtag của tin trong group).

Lưu ý cho phase 2 (đã áp dụng): schema đã có `jobs.src_kind`, `msg_map.src_topic_id`, `topic_map` (xem `04-state-checkpoint.md`) để phase 8 không cần migration. Đường code phase 1–7 vẫn viết với `kind` trong đầu, dù chỉ kiểm thử với broadcast.

## Việc cần xác minh sớm (spike, phase 0–1)

1. ~~Phiên bản Telethon cài đặt có tham số `drop_author` của `forward_messages` không?~~ **Xong 2026-09-19:** có. Telethon 1.45.0 `forward_messages(..., drop_author=, drop_media_captions=, as_album=)`. `pyproject.toml` đặt `telethon>=1.45` nên không cần fallback `ForwardMessagesRequest`.
2. ~~Forward một danh sách id có giữ nguyên album khi `drop_author=True` không?~~ **Xong 2026-09-20:** có, người dùng đã thử trên tài khoản thật (chưa đo riêng từng cỡ album).
3. `iter_messages(..., reverse=True, search=..., filter=...)` kết hợp `min_id` cho kết quả đúng thứ tự tăng dần? *(đọc mã Telethon 1.45: `min_id` thành `offset_id = min_id + 1`, `max_id` **loại trừ**; `search`/`filter` chuyển thành `messages.search` với `add_offset` âm, và `offset_date` trở thành `max_date` nên hỏng khi `reverse=True`, vì vậy ngày được đổi thành id bằng một lời gọi riêng. **Chưa thử trên Telegram thật**: chạy cùng một job với `--pushdown` và `--no-pushdown` rồi so số tin đã sao chép)*
4. Hành vi thực tế của quyền để xác định `can_post` và `is_admin` (broadcast, supergroup, group). **Phase 1** suy từ entity (`creator`, `admin_rights`, `banned_rights`, `default_banned_rights`, có xét `until_date`) thay vì `get_permissions`, vì gọi `get_permissions` cho từng dialog là một request mỗi kênh; đã khớp trên account thật với kênh broadcast, còn cần đối chiếu supergroup/group/forum trước khi tick.
5. Cách phát hiện `noforwards` đáng tin cậy (`Channel.noforwards`, và `Message.noforwards`). Phase 1 đọc `Channel.noforwards`/`Chat.noforwards` trong `channel_info`; `Message.noforwards` chưa dùng.
6. Số tin/lời gọi và delay nào chạy êm trên một account thử (đo, không đoán).
7. Với `drop_author=True`, Telegram xử lý thế nào các loại poll, quiz, location, contact, game, invoice khi forward (giữ, lỗi, hay đổi thành tin khác)? Quyết định chiến lược A có cần tiền kiểm theo loại tin hay không.
8. Quiz: admin/creator của nguồn có thấy đáp án đúng khi chưa trả lời không? (Telethon chỉ dựng lại được quiz khi có `results.results`.) Kiểm tra `poll.id`, `close_date`, `public_voters` có được chấp nhận khi gửi lại `InputMediaPoll`.
9. Forum: `ForwardMessagesRequest(top_msg_id=...)` có đưa tin vào đúng topic đích không; `CreateForumTopicRequest` (icon, General); `iter_messages` trả `reply_to.forum_topic`/`reply_to_top_id` đủ để định tuyến topic; `ToggleForumRequest` để tạo đích forum; danh sách topic (`GetForumTopicsRequest`) và topic đã đóng/ẩn.

## Câu hỏi mở

Hiện không có. Các câu hỏi phát sinh trong lúc thiết kế đều đã được trả lời (bảng dưới).

## Câu hỏi đã trả lời (2026-09-19)

| Câu hỏi | Trả lời | Nơi ghi |
|---|---|---|
| Nguồn là supergroup/group hay chỉ broadcast? | Tất cả: broadcast, supergroup, group, forum | `00-tong-quan.md` (mục tiêu), `01-kien-truc.md` |
| Forum: ánh xạ topic → topic ở đích? | Có | `01-kien-truc.md`, `04-state-checkpoint.md` (`topic_map`) |
| Chế độ chạy nền (daemon)? | Không. Chỉ `sync` chạy tay | `00-tong-quan.md` (không phải mục tiêu) |
| Nhiều account? | Không, một account | `00-tong-quan.md` (không phải mục tiêu) |
| Tên PyPI/GitHub còn trống? | Không (`twingram` đã bị chiếm). Chọn **tgmirror** | `00-tong-quan.md` (Tên dự án) |
| Poll/quiz/location/contact/invoice/game khi copy? | Location/contact: giữ. Poll: giữ nhưng mất vote (opt-in `--reset-polls`). Quiz: giữ nếu đã trả lời. Game/invoice: bỏ + cảnh báo (`--ignore-unsupported`) | `01-kien-truc.md`, `02-cli-ux.md`, `04-state-checkpoint.md` |
| Đích có sẵn của nguồn group/forum bắt buộc cùng loại (forum → forum)? | Có | `01-kien-truc.md` |
| Thêm tiền tố tên người gửi khi copy từ group (chiến lược B)? | Không cần | `01-kien-truc.md` (Hạn chế khi copy từ group) |
| Cờ gửi tin text thay thế cho tin bị bỏ? | Có: `--placeholder` | `01-kien-truc.md`, `02-cli-ux.md` |

## Nhật ký quyết định

Khi đổi một quyết định D1..D9 trong `00-tong-quan.md`, ghi ngày và lý do ở đây.

- 2026-09-19: Đổi tên dự án `twingram` → **tgmirror** (package `tgmirror`, lệnh `tgmirror`, DB `tgmirror.db`, biến môi trường `TGMIRROR_API_ID`/`TGMIRROR_API_HASH`), vì `twingram` đã bị chiếm trên PyPI/GitHub. Lệnh `twin` bỏ hẳn, không giữ alias.
- 2026-09-19: Mở rộng phạm vi v1 từ "chỉ broadcast" sang mọi loại nguồn (broadcast, supergroup, group, forum) kèm ánh xạ topic; thành phase 8. Không phải D1–D9 nhưng đổi danh sách "không phải mục tiêu". Ngược lại chốt **không** làm daemon và **không** làm multi-account.
- 2026-09-19: Chốt: đích có sẵn phải cùng loại nguồn; không thêm tên người gửi; có cờ `--placeholder` gửi tin text thay thế cho tin bị bỏ vì không hỗ trợ.
- 2026-09-19: Chốt xử lý tin đặc thù cho chiến lược B: poll/quiz/location/contact giữ (poll mất vote, opt-in), game/invoice bỏ + cảnh báo. Thêm trạng thái `msg_map.status='skipped'`. Đoạn code mẫu trong ghi chú nguồn (`PollAnswerSyntax`, tự lắp `InputMediaPoll`) **không dùng**: `PollAnswerSyntax` không tồn tại trong Telethon 1.45; dùng `send_message(file=message.media)`.

- 2026-09-20: Phase 3 xong. Không đổi D1–D9. Thêm dependency `pyyaml` và `regex`. Các lựa chọn nhỏ ghi ở "Phase 3 — ghi chú" và `03-filters.md`: `date` nửa mở, thiếu thuộc tính thì predicate sai, không đẩy `contains`, `--refilter` là cách đổi filter.
- 2026-09-20: Phase 2 xong. Không đổi D1–D9. Các lựa chọn nhỏ ghi ở "Phase 2 — ghi chú": limiter tạm thời, FloodWait dừng job thay vì chờ (cho tới phase 4), một job cho mỗi cặp nguồn/đích, `run` chạy được với job `done`, `last_message_id` vào protocol.
- 2026-09-19: Thêm skill `doc-sync` và luật "mỗi task đều xét cập nhật docs/skills" (CLAUDE.md, luật 9).
- 2026-09-19: Bản thiết kế đầu tiên. D3 đổi so với brainstorm ban đầu (brainstorm cho phép reupload cho mọi kênh `noforwards`; bản này tôn trọng cài đặt bảo vệ nội dung của chủ kênh).
