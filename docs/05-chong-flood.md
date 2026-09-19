# 05 — Chống FLOOD_WAIT & spam

> Telegram **không công bố** giới hạn chính xác và có thể thay đổi theo tài khoản, độ "tuổi" và hành vi. Các con số dưới đây là điểm khởi đầu bảo thủ, sẽ được chỉnh dựa trên `flood_log`. Không có cách nào đảm bảo tuyệt đối không bị giới hạn.

## Nguyên tắc

1. Ít lời gọi hơn: gom batch (chiến lược A), tránh `get_entity`/`GetHistory` thừa (cache entity).
2. Chậm và đều, có jitter: hành vi giống người dùng hơn bot.
3. Học từ phản hồi: mỗi FloodWait làm chậm lại; chạy êm thì nhích nhanh dần.
4. Dừng khi có dấu hiệu nặng (`PeerFlood`), không cố.
5. Một account, một job, tuần tự.

## Limiter (`core/limiter.py`)

Mọi lời gọi gateway đi qua `await limiter.acquire(cost, kind)`.

- **Delay mục tiêu** `delay` (giây) giữa hai lời gọi ghi: bắt đầu `min_delay`.
- **AIMD**: khi flood → `delay = min(delay * 2, max_delay)`; sau mỗi 20 lời gọi thành công liên tiếp → `delay = max(delay * 0.9, min_delay)`.
- **Jitter**: mỗi lần chờ nhân với `uniform(1 - jitter, 1 + jitter)`.
- **Long pause**: sau mỗi `long_pause_every` tin, nghỉ ngẫu nhiên trong `long_pause_range` giây.
- **Daily cap**: đếm tin gửi trong ngày (`limiter_state`); chạm `daily_cap` → job chuyển `waiting_flood` (`reason=daily_cap`) với `resume_at` là 00:00 ngày kế; không phải lỗi.
- **Cost**: tin gửi tính theo số tin trong batch; lời gọi đọc (`iter_messages`) dùng bucket đọc riêng, nhẹ hơn, đặt `wait_time` rõ ràng.
- **Upload**: tin cần upload (strategy B) cộng thêm delay theo dung lượng và `upload_concurrency = 1` mặc định.
- Trạng thái (`delay`, đếm ngày) được lưu lại để lần chạy sau không "quên" bài học.

### Giá trị mặc định

| Key | Mặc định | Ý nghĩa |
|---|---|---|
| `batch_size` | 20 | Số tin/lời gọi forward (tối đa 100) |
| `min_delay` | 2.0 s | Sàn của delay giữa các lời gọi ghi |
| `max_delay` | 60 s | Trần |
| `jitter` | 0.3 | ±30% |
| `long_pause_every` | 200 | tin |
| `long_pause_range` | 30–90 s | |
| `daily_cap` | 5000 | tin/ngày/account |
| `max_auto_wait` | 900 s | FloodWait dài hơn → tự pause job, không ngủ tiếp |
| `upload_concurrency` | 1 | File upload đồng thời |

## Xử lý FloodWait

Client tạo với `flood_sleep_threshold=0` nên Telethon **luôn raise** `FloodWaitError`, để limiter:

1. Ghi `flood_log` (method, seconds, delay hiện tại, batch_size).
2. `on_flood(seconds)` → tăng delay (AIMD).
3. `seconds <= max_auto_wait`: hiển thị đếm ngược, ngủ `seconds + uniform(1, 5)`, rồi retry **cùng batch** (đã ghi pending, không mất tiến độ).
4. `seconds > max_auto_wait`: đặt `waiting_flood` + `resume_at`, lưu state, thoát mã 3 (hoặc ngủ tiếp nếu `--wait`).
5. Nếu gặp >= 3 FloodWait trong 10 phút: giảm `batch_size` một nửa và tăng `min_delay` tạm thời.

## Dấu hiệu bị đánh dấu spam

- `PeerFloodError`: dừng ngay, `failed(peer_flood)`, khuyến cáo nghỉ >= 24h, đề nghị kiểm tra @SpamBot. Không retry, không tự tăng tốc lại.
- Đích hay báo `ChatWriteForbidden`/bị ban tạm: dừng và báo.

## Vệ sinh chung (in trong `tgmirror doctor` và README)

- Dùng tài khoản đã có lịch sử; tài khoản mới tinh dễ bị siết.
- Không chạy nhiều tool/nhiều session cùng lúc trên một account.
- Ưu tiên tin nhỏ (text/ảnh) trước, file lớn sau (tùy chọn `--order small-first` nhưng mặc định giữ thứ tự thời gian, D4).
- Đừng vượt quá `daily_cap` bằng cách chạy lại liên tục; dùng `tgmirror sync --all` chạy tay theo từng đợt nhỏ thay vì clone dồn một lượt cực lớn.
- Kênh đích mới: nên để vài phút sau khi tạo mới bắt đầu gửi; đặt tên/about hợp lệ; không spam link.
- Clone theo thứ tự thời gian, không đảo lộn.

## Cách đo và tinh chỉnh

`tgmirror status` hiển thị: delay hiện tại, msg/s, số flood 24h. `flood_log` là nguồn dữ liệu để chỉnh mặc định ở các bản phát hành sau. Mọi thay đổi mặc định phải kèm ghi chú vì sao (dữ liệu nào).
