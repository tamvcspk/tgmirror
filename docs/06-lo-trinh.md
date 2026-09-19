# 06 — Lộ trình & câu hỏi mở

## Trạng thái hiện tại

Cập nhật bởi skill `doc-sync` khi một phase bắt đầu/kết thúc.

- [x] Phase 0 — Scaffold (2026-09-19)
- [x] Phase 1 — Login, channels, tạo kênh (2026-09-19)
- [ ] Phase 2 — Copy + state + pause/resume
- [ ] Phase 3 — Filters
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

- **`new` chưa lưu job**: bảng `jobs` và runner thuộc phase 2. Phase 1 dừng sau khi chốt cặp nguồn/đích (và tạo đích nếu được yêu cầu, luôn có xác nhận hoặc `--yes`); lệnh in cảnh báo "chưa lưu job". Phase 2 sẽ nối `plan_endpoints`/`materialize` vào `create_job(spec)`.
- **Chưa có limiter**: `core/limiter.py` là phase 4. Các lời gọi phase 1 (`iter_dialogs`, `get_entity`, `CreateChannelRequest`, đăng nhập) là thao tác đơn lẻ do người dùng khởi động; FloodWait/PeerFlood được ánh xạ sang `FloodWait`/`PeerFlood`, in một câu và thoát mã 3, không retry. Luật 1 (mọi lời gọi qua limiter) chỉ thực sự áp dụng từ phase 4; không thêm lời gọi ghi hàng loạt trước đó.
- **Đã kiểm chứng với Telegram thật (2026-09-19, người dùng chạy tay trên Windows/PowerShell)**: `login`, `channels`, `new --src ... --dst-new ...` (kênh broadcast được tạo, có hỏi xác nhận). Còn chưa thử trên account thật: nguồn/đích supergroup, forum, group thường (nên `can_post`/`is_admin` suy từ entity, spike 4, mới xác nhận cho broadcast). Lưu ý PowerShell: `@name` phải đặt trong dấu nháy.

Lưu ý phase 2: schema đã có `jobs.src_kind`, `msg_map.src_topic_id`, `topic_map` (xem `04-state-checkpoint.md`) để phase 8 không cần migration. Đường code phase 1–7 vẫn viết với `kind` trong đầu, dù chỉ kiểm thử với broadcast.

## Việc cần xác minh sớm (spike, phase 0–1)

1. ~~Phiên bản Telethon cài đặt có tham số `drop_author` của `forward_messages` không?~~ **Xong 2026-09-19:** có. Telethon 1.45.0 `forward_messages(..., drop_author=, drop_media_captions=, as_album=)`. `pyproject.toml` đặt `telethon>=1.45` nên không cần fallback `ForwardMessagesRequest`.
2. Forward một danh sách id có giữ nguyên album khi `drop_author=True` không (thử với album nhiều cỡ)?
3. `iter_messages(..., reverse=True, search=..., filter=...)` kết hợp `min_id` cho kết quả đúng thứ tự tăng dần?
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

- 2026-09-19: Thêm skill `doc-sync` và luật "mỗi task đều xét cập nhật docs/skills" (CLAUDE.md, luật 9).
- 2026-09-19: Bản thiết kế đầu tiên. D3 đổi so với brainstorm ban đầu (brainstorm cho phép reupload cho mọi kênh `noforwards`; bản này tôn trọng cài đặt bảo vệ nội dung của chủ kênh).
