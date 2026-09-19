# 00 — Tổng quan

## Bài toán

Người dùng đã join một kênh Telegram. Họ muốn tạo bản sao của kênh đó (hoặc một phần nội dung, theo filter) vào một kênh đích, bằng chính tài khoản của mình qua API Telegram (`api_id`/`api_hash` tự đăng ký tại my.telegram.org). Có thể dừng/tiếp tục và chạy lại để lấy phần nội dung mới (delta).

## Mục tiêu

1. Chọn nguồn từ danh sách đã join: broadcast channel, supergroup/group, hoặc forum (nhiều topic).
2. Chọn đích có sẵn, hoặc tạo mới (nhập tên; copy about/avatar tùy chọn). Với forum, topic nguồn được ánh xạ sang topic tương ứng ở đích (xem `01-kien-truc.md`).
3. Filter nội dung: loại media, hashtag, regex/keyword, khoảng ngày/ID, dung lượng, ...
4. Pause / stop / resume có checkpoint; delta clone.
5. Tốc độ tốt: ưu tiên copy phía server, gom batch.
6. Tránh FLOOD_WAIT và hạn chế bị đánh dấu spam.

## Không phải mục tiêu (v1)

- Đồng bộ chỉnh sửa/xóa từ nguồn sang đích (để phase sau).
- Chạy nền liên tục (daemon / mirror realtime). Chỉ có `sync` do người dùng chạy tay.
- Chạy nhiều account hoặc chia tải: chỉ dùng đúng một account.
- Bypass "Restrict saving content" (xem D3).
- GUI. Chỉ CLI/TUI.

## Quyết định đã chốt

| # | Quyết định | Lý do |
|---|---|---|
| D1 | Dùng **Telethon** (+ `cryptg`) | Còn được duy trì; Pyrogram bản gốc gần như ngừng, chỉ còn fork |
| D2 | Chiến lược chính: `forward_messages(..., drop_author=True)` (copy phía server) | Không tốn băng thông upload, nhanh, giữ album, không hiện "Forwarded from" |
| D3 | **Tôn trọng `noforwards`**: nếu nguồn bật "Restrict saving content" và user không phải creator/admin của nguồn → dừng và giải thích. Nếu là admin → hướng dẫn tắt tùy chọn tạm thời, hoặc dùng `--mode reupload` kèm xác nhận | Tính năng này là ý muốn của chủ kênh; tool không nên vô hiệu hóa nó |
| D4 | Duyệt tin **cũ → mới** | Giữ đúng thứ tự trong kênh đích |
| D5 | **Một file SQLite** (`tgmirror.db`) cho mọi job | `tgmirror jobs` đơn giản, transaction rõ ràng |
| D6 | Client tạo với `flood_sleep_threshold=0`; limiter của mình xử lý mọi FloodWait | Có log, có thích nghi, có thể tự pause job |
| D7 | Typer + questionary + Rich | CLI + wizard + progress gọn nhẹ |
| D8 | Python >= 3.11, `uv` | Hiện đại, cài đặt nhanh |
| D9 | Engine chỉ phụ thuộc `TelegramGateway` (protocol) | Test được bằng `FakeGateway`, không cần mạng |

## Pháp lý & sử dụng có trách nhiệm

- Chỉ clone nội dung của chính bạn hoặc nội dung được phép sao chép (backup, migrate kênh).
- Copy nội dung của người khác có thể vi phạm bản quyền và Điều khoản Telegram.
- Tự động hóa user account có rủi ro bị giới hạn. Tool giảm rủi ro, không loại bỏ hoàn toàn. README và `tgmirror doctor` phải nói rõ điều này.
- `api_hash` và file `*.session` tương đương quyền truy cập tài khoản: không commit, không log.

## Tên dự án

**tgmirror** — "tg" (Telegram) + "mirror" (bản sao). Lệnh CLI: `tgmirror`. Package: `tgmirror`.
Tên ban đầu `twingram` đã có người dùng trên PyPI/GitHub nên đổi sang `tgmirror` (2026-09-19, xem nhật ký quyết định ở `06-lo-trinh.md`).
