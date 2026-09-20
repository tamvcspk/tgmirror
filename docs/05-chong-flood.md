# 05 — Chống FLOOD_WAIT & spam

> Telegram **không công bố** giới hạn chính xác và có thể thay đổi theo tài khoản, độ "tuổi" và hành vi. Các con số dưới đây là điểm khởi đầu bảo thủ, sẽ được chỉnh dựa trên `flood_log`. Không có cách nào đảm bảo tuyệt đối không bị giới hạn.

## Nguyên tắc

1. Ít lời gọi hơn: gom batch (chiến lược A), tránh `get_entity`/`GetHistory` thừa (cache entity).
2. Chậm và đều, có jitter: hành vi giống người dùng hơn bot.
3. Học từ phản hồi: mỗi FloodWait làm chậm lại; chạy êm thì nhích nhanh dần.
4. Dừng khi có dấu hiệu nặng (`PeerFlood`), không cố.
5. Một account, một job, tuần tự.

> **Trạng thái (phase 4)**: đã cài đặt đúng như dưới đây: `core/limiter.py` (AIMD, jitter, nghỉ dài, `daily_cap`, bucket đọc, giảm `batch_size`, `limiter_state`) và `engine/flood.py` (`FloodGuard`: log `flood_log`, chờ rồi retry cùng lời gọi, dừng job khi quá `max_auto_wait`). Chưa kiểm chứng trên Telegram thật: các số mặc định chưa được đo, xem `06-lo-trinh.md` (spike 6).

## Limiter (`core/limiter.py`)

Mọi lời gọi đọc/ghi của một lần `run` đi qua `await limiter.acquire(cost, kind)` (`kind` = `write` hoặc `read`), thông qua `FloodGuard` của runner. Các lệnh một lần do người dùng khởi động (`login`, `channels`, `new`: tạo kênh, `last_message_id`, `--preview`) không qua limiter: FloodWait ở đó in một câu và thoát mã 3, không retry.

- **Delay mục tiêu** `delay` (giây) giữa hai lời gọi ghi: bắt đầu `min_delay` (hoặc giá trị đã lưu, kẹp trong `[min_delay, max_delay]`). Lời gọi ghi đầu tiên của một lần chạy đi ngay.
- **AIMD**: khi flood → `delay = min(delay * 2, max_delay)`; sau mỗi 20 lời gọi thành công liên tiếp → `delay = max(delay * 0.9, min_delay)`.
- **Jitter**: mỗi lần chờ nhân với `uniform(1 - jitter, 1 + jitter)`.
- **Long pause**: sau mỗi `long_pause_every` tin, nghỉ ngẫu nhiên trong `long_pause_range` giây.
- **Daily cap**: đếm tin gửi trong ngày (`limiter_state.sent_today`, theo ngày **giờ máy**, lưu cùng transaction với batch); một batch mà `sent_today + cost > daily_cap` không được gửi (trừ khi hôm nay chưa gửi gì: batch đầu lớn hơn cap vẫn đi, để không kẹt vĩnh viễn). Chạm cap → `DailyCapReached`: job chuyển `waiting_flood` với `fail_reason='daily_cap'` và `resume_at` là 00:00 ngày kế (giờ máy), thoát mã 3 kèm một câu giải thích; không phải lỗi. Bộ đếm là của cả account, không phải của một job. `--wait` không áp dụng cho daily cap.
- **Cost**: tin gửi tính theo số tin trong batch. Lời gọi đọc dùng bucket riêng, nhẹ hơn: `cost` là số request (một `iter_messages` = 1, cộng 1 cho mỗi đầu `--since`/`--until` vì gateway tra ngày → id; mỗi lần hoàn thiện album cũng là một `iter_messages`), chờ `read_delay × (delay / min_delay) × jitter` giữa hai lần đọc (lần đọc đầu của một lần chạy đi ngay), nên đọc chậm theo cùng tốc độ với ghi khi bị flood. Việc lật trang bên trong một `iter_messages` do Telethon giãn cách bằng `wait_time` (`READ_WAIT` = 1 s). `daily_cap` chỉ áp cho ghi.
- **Upload**: tin cần upload (strategy B) cộng thêm delay theo dung lượng và `upload_concurrency = 1` mặc định.
- Trạng thái (`delay`, `day`, `sent_today`) lưu ở `limiter_state` theo `jobs.account`: cùng transaction với mỗi batch (`commit_batch(limiter=...)`) và ngay sau mỗi flood. Không lưu: cửa sổ 10 phút và mức giảm batch (tạm thời).
- Thời gian và `sleep` được tiêm vào (`clock`, `sleep`): test dùng đồng hồ giả, không ngủ thật.

### Giá trị mặc định

| Key | Mặc định | Ý nghĩa |
|---|---|---|
| `batch_size` | 20 | Số tin/lời gọi forward (tối đa 100) |
| `min_delay` | 2.0 s | Sàn của delay giữa các lời gọi ghi |
| `max_delay` | 60 s | Trần |
| `read_delay` | 0.5 s | Sàn giữa hai request đọc (nhân với `delay / min_delay`); nửa `min_delay` vì đọc nhẹ hơn ghi, chưa đo |
| `jitter` | 0.3 | ±30% |
| `long_pause_every` | 200 | tin |
| `long_pause_range` | 30–90 s | |
| `daily_cap` | 5000 | tin/ngày/account |
| `max_auto_wait` | 900 s | FloodWait dài hơn → job thành `waiting_flood`, không ngủ tiếp (trừ khi `run --wait`) |
| `upload_concurrency` | 1 | File upload đồng thời |

## Xử lý FloodWait

Client tạo với `flood_sleep_threshold=0` nên Telethon **luôn raise** `FloodWaitError`, để limiter:

1. Ghi `flood_log` (method, seconds, delay hiện tại, batch_size).
2. `on_flood(seconds)` → tăng delay (AIMD).
3. `seconds <= max_auto_wait`: in một dòng thông báo (đếm ngược động là phase 7), ngủ `seconds + uniform(1, 5)`, rồi retry **cùng lời gọi** (batch vẫn `pending`, không dựng lại, con trỏ không nhúc nhích). Ghi (`copy_messages`) và đọc (`iter_messages`) đều vậy; đọc bị cắt giữa chừng thì đọc tiếp từ tin đã trao cuối cùng, nên không sót, không lặp, album không bị tách.
4. `seconds > max_auto_wait`: xóa `pending` của batch bị từ chối (Telegram không tạo gì), đặt `waiting_flood` + `resume_at = now + seconds`, thoát mã 3. Với `run --wait`: vẫn ngủ tiếp (ngắt được bằng pause/stop/Ctrl+C).
   Cùng một lời gọi bị FloodWait liền 5 lần (`MAX_FLOODS_PER_CALL`) thì cũng dừng như trên, thay vì ngủ vô hạn (lựa chọn khi cài đặt, không phải D1–D9).
   Ngủ luôn ngắt được: pause/stop/Ctrl+C trong lúc chờ trả job `paused`/`stopped`, batch chưa gửi được xóa khỏi `pending`.
5. Nếu gặp >= 3 FloodWait trong 10 phút: giảm `batch_size` một nửa (mỗi lần đủ 3 flood thêm một nấc, tối thiểu 1) và nâng sàn của delay lên `min_delay × 2^nấc` (chặn bởi `max_delay`) cho tới khi qua 10 phút không có flood; có thông báo `throttled`. Batcher hỏi `batch_size` hiện hành cho từng unit nên có hiệu lực ngay từ batch kế tiếp.

`flood_log.kind`: `flood_wait`, `slow_mode` (SlowModeWait, xử lý như FloodWait), `peer_flood`. `delay_ms` là delay **trước** khi tăng (delay đã dẫn tới flood), `batch_size` là cỡ batch hiện hành.

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
