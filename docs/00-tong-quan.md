# 00 — Tổng quan

## Bài toán

Người dùng đã join một kênh Telegram. Họ muốn tạo bản sao của kênh đó (hoặc một phần nội dung, theo filter) vào một kênh đích, bằng chính tài khoản của mình qua API Telegram (`api_id`/`api_hash` tự đăng ký tại my.telegram.org). Nó chạy như mọi lệnh khác: người dùng gõ `tgmirror clone ...`, việc sao chép chạy ngay trong terminal đó (foreground), Ctrl+C thì dừng. Có thể dừng/tiếp tục và chạy lại để lấy phần nội dung mới (delta).

## Mục tiêu

1. Chọn nguồn từ danh sách đã join: broadcast channel, supergroup/group, hoặc forum (nhiều topic).
2. Chọn đích có sẵn, hoặc tạo mới (nhập tên; copy about/avatar tùy chọn). Với forum, topic nguồn được ánh xạ sang topic tương ứng ở đích (xem `01-kien-truc.md`).
3. Filter nội dung: loại media, hashtag, regex/keyword, khoảng ngày/ID, dung lượng, ...
4. Pause / stop / resume có checkpoint (phím `p`/`r`/`q` khi đang chạy, hoặc `tgmirror pause|stop|run` từ terminal khác); chạy lại cùng cặp nguồn/đích là delta clone.
5. Tốc độ tốt: ưu tiên copy phía server, gom batch.
6. Tránh FLOOD_WAIT và hạn chế bị đánh dấu spam.

## Không phải mục tiêu (v1)

- Đồng bộ chỉnh sửa/xóa từ nguồn sang đích (để phase sau).
- Chạy nền liên tục (daemon / mirror realtime) hoặc lên lịch. "Chạy tay" nghĩa là người dùng gõ một lệnh như mọi lệnh khác và nó chạy ngay trong terminal của họ; không có process nền, không có lịch. Muốn lấy thêm tin mới thì gõ lại lệnh (delta).
- Quản lý "job": không có job được đặt tên, liệt kê hay xóa. Mỗi lần chạy chỉ để lại một dòng nhật ký (`tgmirror history`); trạng thái tối thiểu để delta và resume (con trỏ, bảng tin đã sao chép) là chi tiết nội bộ theo cặp nguồn/đích.
- Chạy nhiều account hoặc chia tải: chỉ dùng đúng một account.
- Bypass "Restrict saving content" **theo mặc định**: chỉ khi chính user tuyên bố và chịu trách nhiệm (xem D3).
- GUI. Chỉ CLI/TUI.

## Quyết định đã chốt

| # | Quyết định | Lý do |
|---|---|---|
| D1 | Dùng **Telethon** (+ `cryptg`) | Còn được duy trì; Pyrogram bản gốc gần như ngừng, chỉ còn fork |
| D2 | Chiến lược chính: `forward_messages(..., drop_author=True)` (copy phía server) | Không tốn băng thông upload, nhanh, giữ album, không hiện "Forwarded from" |
| D3 | **`noforwards`: mặc định tôn trọng, ngoại lệ do user tự chịu trách nhiệm** (đổi 2026-09-20). Nguồn bật "Restrict saving content" thì không sao chép, trừ khi user dùng `--mode reupload` kèm lời tuyên bố của chính mình: cờ `--yes-i-administer-this-channel` (mọi tài khoản, kể cả tài khoản không phải admin của nguồn: user là chủ kênh bằng tài khoản khác), hoặc câu hỏi xác nhận (chỉ tài khoản là admin). `--yes` không thay được cờ. Mỗi lần chạy dựa trên lời tuyên bố in một cảnh báo trách nhiệm. tgmirror không kiểm tra được quyền sở hữu | Tính năng này là ý muốn của chủ kênh nên không bao giờ bị bỏ qua âm thầm; nhưng chủ kênh thường có nhiều tài khoản, và người quyết định cuối cùng là user, người chịu toàn bộ trách nhiệm về việc mình tuyên bố |
| D4 | Duyệt tin **cũ → mới** | Giữ đúng thứ tự trong kênh đích |
| D5 | **Một file SQLite** (`tgmirror.db`) cho nhật ký các lần chạy và trạng thái theo cặp nguồn/đích | `tgmirror history` đơn giản, transaction rõ ràng (2026-09-20: đổi lý do từ "`tgmirror jobs`" khi bỏ khái niệm job, xem `06-lo-trinh.md`) |
| D6 | Client tạo với `flood_sleep_threshold=0`; limiter của mình xử lý mọi FloodWait | Có log, có thích nghi, có thể tự dừng lần chạy |
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
