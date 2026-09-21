# 05 — Chống FLOOD_WAIT & spam

> Telegram **không công bố** giới hạn chính xác và có thể thay đổi theo tài khoản, độ "tuổi" và hành vi. Các con số dưới đây là điểm khởi đầu bảo thủ, sẽ được chỉnh dựa trên `flood_log`. Không có cách nào đảm bảo tuyệt đối không bị giới hạn.

## Nguyên tắc

1. Ít lời gọi hơn: gom batch (chiến lược A), tránh `get_entity`/`GetHistory` thừa (cache entity).
2. Chậm và đều, có jitter: hành vi giống người dùng hơn bot.
3. Học từ phản hồi: mỗi FloodWait làm chậm lại; chạy êm thì nhích nhanh dần.
4. Dừng khi có dấu hiệu nặng (`PeerFlood`), không cố.
5. Một account, một lần chạy tại một thời điểm, tuần tự.

> **Trạng thái (phase 4)**: đã cài đặt đúng như dưới đây: `core/limiter.py` (AIMD, jitter, nghỉ dài, `daily_cap`, bucket đọc, giảm `batch_size`, `limiter_state`) và `engine/flood.py` (`FloodGuard`: log `flood_log`, chờ rồi retry cùng lời gọi, dừng lần chạy khi quá `max_auto_wait`). Chưa kiểm chứng trên Telegram thật: các số mặc định chưa được đo, xem `06-lo-trinh.md` (spike 6).

## Limiter (`core/limiter.py`)

Mọi lời gọi đọc/ghi của một lần `run` đi qua `await limiter.acquire(cost, kind)` (`kind` = `write` hoặc `read`), thông qua `FloodGuard` của runner. Các lệnh một lần do người dùng khởi động (`login`, `channels`, `new`: tạo kênh, `last_message_id`, `--preview`) không qua limiter: FloodWait ở đó in một câu và thoát mã 3, không retry.

- **Delay mục tiêu** `delay` (giây) giữa hai lời gọi ghi: bắt đầu `min_delay` (hoặc giá trị đã lưu, kẹp trong `[min_delay, max_delay]`). Lời gọi ghi đầu tiên của một lần chạy đi ngay.
- **AIMD**: khi flood → `delay = min(delay * 2, max_delay)`; sau mỗi 20 lời gọi thành công liên tiếp → `delay = max(delay * 0.9, min_delay)`.
- **Jitter**: mỗi lần chờ nhân với `uniform(1 - jitter, 1 + jitter)`.
- **Long pause**: sau mỗi `long_pause_every` tin, nghỉ ngẫu nhiên trong `long_pause_range` giây.
- **Daily cap**: đếm tin gửi trong ngày (`limiter_state.sent_today`, theo ngày **giờ máy**, lưu cùng transaction với batch); một batch mà `sent_today + cost > daily_cap` không được gửi (trừ khi hôm nay chưa gửi gì: batch đầu lớn hơn cap vẫn đi, để không kẹt vĩnh viễn). Chạm cap → `DailyCapReached`: lần chạy kết thúc ở `waiting_flood` với `fail_reason='daily_cap'` và `resume_at` là 00:00 ngày kế (giờ máy), thoát mã 3 kèm một câu giải thích; không phải lỗi. Bộ đếm là của cả account, không phải của một lần chạy. `--wait` không áp dụng cho daily cap.
- **Cost**: tin gửi tính theo số tin trong batch. Lời gọi đọc dùng bucket riêng, nhẹ hơn: `cost` là số request (một `iter_messages` = 1, cộng 1 cho mỗi đầu `--since`/`--until` vì gateway tra ngày → id; mỗi lần hoàn thiện album cũng là một `iter_messages`; `get_messages` của `retry` là 1 cho mỗi lô tối đa 100 id; `prepare` của chiến lược B là 1 cho mỗi unit), chờ `read_delay × (delay / min_delay) × jitter` giữa hai lần đọc (lần đọc đầu của một lần chạy đi ngay), nên đọc chậm theo cùng tốc độ với ghi khi bị flood. Việc lật trang bên trong một `iter_messages` do Telethon giãn cách bằng `wait_time` (`READ_WAIT` = 1 s). `daily_cap` chỉ áp cho ghi.
- **Upload (chiến lược B, phase 6)**: mỗi unit tải lên lại là một lần `pace` như mọi lần ghi (cùng `min_delay`, jitter, nghỉ dài, `daily_cap` tính theo tin), và `prepare` (đọc lại tin + tải xuống) là một request đọc qua bucket đọc. **Chưa có delay cộng thêm theo dung lượng**: một upload lớn vốn đã chậm hơn khoảng giãn cách, và chưa có số liệu để chọn một hệ số; sẽ xét khi có `flood_log` (spike 6). Chỉ nửa đọc chạy trước (`prefetch`): việc gửi luôn tuần tự, một lời gọi một lúc, nên không tăng số lời gọi ghi đồng thời. FloodWait khi tải (`prepare`) xử lý như đọc (chờ rồi lặp lại; file đã tải xong được giữ), khi gửi (`send_prepared`, `send_text`) như ghi.
- Trạng thái (`delay`, `day`, `sent_today`) lưu ở `limiter_state` theo `mirrors.account`: cùng transaction với mỗi batch (`commit_batch(limiter=...)`) và ngay sau mỗi flood. Không lưu: cửa sổ 10 phút và mức giảm batch (tạm thời).
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
| `max_auto_wait` | 900 s | FloodWait dài hơn → lần chạy kết thúc `waiting_flood`, không ngủ tiếp (trừ khi `--wait`) |
| `prefetch` | 1 | Số unit tải xuống trước trong lúc một unit đang tải lên (0 = không; tối đa 3). Tối đa `prefetch + 1` unit nằm trên đĩa. Thay cho `upload_concurrency` (chưa phát hành, bỏ hẳn: gửi vẫn một lúc một unit) |
| `tmp_budget_mb` | 2048 | Dung lượng đĩa tối đa cho các file đã tải xuống chờ gửi; unit lớn hơn ngân sách vẫn đi khi đĩa trống. Là trần cứng cho việc tải nhanh hơn gửi: chỗ được giữ trước khi tải; file không có `size` giữ 1 MiB; byte thật trên đĩa được đối chiếu sau khi tải (`01-kien-truc.md`, "Analyze và tiến độ") |
| `max_requests` | 4 | Số request truyền file đang bay cùng lúc trong cả tiến trình, chia chung cho tải xuống và tải lên (`RequestBudget`, `core/pool.py`); 0 tắt pool (Telethon tự tải, một request một lúc). Ngân sách **bắt đầu ở 2**, tăng một bậc sau mỗi 32 part liền không gặp lực cản (và không trong 30 giây sau lần gặp), **giảm một nửa** khi Telegram phản kháng. Đã bị `HTTP 429` ở tầng truyền với 16 request (spike 12) rồi với 8 (lần chạy thật đầu tiên, 2026-09-20), trong khi 4 request đang bay đã cho ~30 MB/s tải xuống: nên mặc định 4, muốn nhanh hơn thì tăng từng bước và theo dõi `flood_log` |
| `upload_connections` | 2 | Số kết nối (tối đa 3) **riêng** mà phần tải lên chia part ra, tới cùng DC (dùng chung auth key), không tính kết nối chính. Không tạo được thì dùng ít hơn, không tạo được cái nào thì dùng kết nối chính. Tải xuống luôn một kết nối **riêng** của nó (spike 12: thêm kết nối không nhanh hơn). Dữ liệu lớn không bao giờ đi chung kết nối chính với các lời gọi đọc và đăng (lần chạy thật đầu tiên bị 429 khi tải xuống và tải lên cùng đi trên kết nối chính) |
| `pool_min_mb` | 10 | File nhỏ hơn ngưỡng này giữ cách tải của Telethon (ít part, pool không có lợi). Phần tải lên chỉ dùng pool từ 10 MB (API big-file của Telegram) và chỉ cho tin đơn là tài liệu/video, album và ảnh giữ đường cũ |

Gửi bằng mã file (`Strategy.REFERENCE`): `fetch` là một request đọc có pace (như `prepare`), `send_by_reference` là một lần ghi qua `guard.write` tính vào `daily_cap` và giãn cách như mọi lần ghi; một unit là một lần ghi, vì vậy nút cổ chai của loại này là pace và cap chứ không phải tốc độ truyền. Lần thử lại sau `FileRefExpired` cộng thêm một `fetch` (một request đọc).

Request `count` mở đầu lần chạy (analyze) **không** qua bucket đọc: một lời gọi (vài request nếu có ngày) như các lần đọc chuẩn bị của `begin_run`; FloodWait của nó vẫn được ghi vào `flood_log` (`method='count'`) và xử lý như mọi FloodWait.

### Pace tính theo khoảng cách giữa hai lần đăng (2026-09-20, người dùng duyệt)

Điều Telegram giới hạn là tần suất **đăng tin**, không phải việc đẩy byte. Với unit lớn (`Strategy.REUPLOAD`, có file), runner đẩy byte trước (`guard.transfer("upload_prepared")`, không pace, không tính vào `daily_cap`, FloodWait được ghi và chờ như mọi lần), đo bao lâu (`Runner._mono`), rồi `guard.pace(cost, credit)`: `Limiter.acquire(..., credit=)` trừ thời gian đó khỏi **delay** (`max(delay × jitter − credit, 0)`), còn nghỉ dài (`long_pause_every`) giữ nguyên đầy đủ. Vậy khoảng cách từ lúc đăng xong unit trước đến lúc đăng unit này vẫn ≥ `delay` (việc đẩy byte chỉ bắt đầu sau khi unit trước đã đăng xong; nếu đẩy lâu hơn delay thì không phải chờ thêm), và lần đăng đầu của một lần chạy vẫn đi ngay. Video 55 MB: mỗi unit ≈ 4,8 s → ≈ 2,8 s; file rất lớn hầu như không đổi; ảnh nhỏ vẫn bị pace chặn. `daily_cap` được kiểm bằng `check_cap` **trước** khi đẩy byte. Đường copy (forward), gửi bằng mã file, album và file nhỏ không có credit: pace như cũ.

### Pool request (2026-09-20)

Tải file bằng một request một lúc bị chặn bởi độ trễ (4 MB/s xuống, 3,5 MB/s lên trên máy thử); với vài request đang bay tải xuống được 30 MB/s (spike 12). `core/pool.py`:

- `RequestBudget`: một con số chung cho mọi request truyền file đang bay; `acquire(priority)` (số nhỏ đi trước: tải lên thuộc unit cũ hơn nên ưu tiên hơn tải xuống của unit kế), AIMD như limiter: `succeeded()` tăng, `pressure()` giảm một nửa (tối thiểu 1) và tạm ngừng tăng 30 giây.
- `run_parts(count, work, budget)`: các part được nhận theo thứ tự bởi tối đa `max_requests` worker, worker rảnh lấy part kế nên kết nối chậm tự làm ít hơn; mỗi part đúng một lần. `TransportPressure` giảm ngân sách và: **HTTP 429 ở tầng truyền không lặp tại chỗ** mà kết thúc cả lần truyền bằng `FloodWait(60, transport=True)` (`TRANSPORT_WAIT`), để `FloodGuard` ghi `flood_log` với loại **`transport_429`**, tăng delay (AIMD), chờ rồi lặp lại `prepare`/`upload_prepared` (lần chạy thật đầu tiên: thử lại trong 15 giây chỉ làm hỏng lần chạy); kết nối bị đóng, không có trả lời trong 30 giây, part sai độ dài thì lặp part sau `1s × 2^n` (±25%), tối đa 5 lần rồi `Transient`; `FloodWait` giảm ngân sách và kết thúc cả lần truyền tức thì (`FloodGuard` chờ rồi lặp lại `prepare`/`send_prepared` từ đầu: chưa nhớ part đã xong); lỗi khác hủy các worker còn lại.
- RAM: mỗi worker giữ tối đa một part (1 MiB xuống, 512 KiB lên), nên tối đa `max_requests` MiB.
- **Tải xuống không làm lại từ đầu sau flood hay mất kết nối**: file `.part` và tập part đã ghi được giữ (`_partial`), lần `prepare` lặp lại nhặt tiếp và tiến độ bắt đầu từ chỗ cũ; lỗi khác thì xóa file dở. Tải lên chưa nhớ part (đẩy lại từ đầu).
- **Tạo được ít kết nối hơn số xin cũng là một cảnh báo** (`_upload_senders`/`_download_sender`, logger `tgmirror.core.telethon_gateway`, một lần cho mỗi gateway): không tạo được kết nối riêng thì pool lùi về ít kết nối hơn, hay về kết nối chính, và đó là chậm hẳn (1 kết nối ~3 MB/s lên, 2–4 kết nối 18–28 MB/s, spike 2026-09-21); trước đây lùi âm thầm.
- **Mỗi lần ngân sách giảm là một cảnh báo** (`RequestBudget.pressure(reason)`, logger `tgmirror.core.pool`; CLI in `[cảnh báo] transfer requests in flight: 4 -> 2 (lý do); growing again after 32 clean parts` ra stderr): một lần truyền bỗng chạy chậm gấp nhiều lần thường là ngân sách đã tụt về 1 request, và đây là cách thấy điều đó.
- Ngân sách chỉ sống trong tiến trình.

## Xử lý FloodWait

Client tạo với `flood_sleep_threshold=0` nên Telethon **luôn raise** `FloodWaitError`, để limiter:

1. Ghi `flood_log` (method, seconds, delay hiện tại, batch_size).
2. `on_flood(seconds)` → tăng delay (AIMD).
3. `seconds <= max_auto_wait`: in một dòng thông báo (đếm ngược động là phase 7), ngủ `seconds + uniform(1, 5)`, rồi retry **cùng lời gọi** (batch vẫn `pending`, không dựng lại, con trỏ không nhúc nhích). Ghi (`copy_messages`) và đọc (`iter_messages`) đều vậy; đọc bị cắt giữa chừng thì đọc tiếp từ tin đã trao cuối cùng, nên không sót, không lặp, album không bị tách.
4. `seconds > max_auto_wait`: xóa `pending` của batch bị từ chối (Telegram không tạo gì), đặt `waiting_flood` + `resume_at = now + seconds`, thoát mã 3. Với `run --wait`: vẫn ngủ tiếp (ngắt được bằng pause/stop/Ctrl+C).
   Cùng một lời gọi bị FloodWait liền 5 lần (`MAX_FLOODS_PER_CALL`) thì cũng dừng như trên, thay vì ngủ vô hạn (lựa chọn khi cài đặt, không phải D1–D9).
   Ngủ luôn ngắt được bởi stop: `stop`/phím `q`/Ctrl+C trong lúc chờ kết thúc lần chạy `stopped`, batch chưa gửi được xóa khỏi `pending`. `pause` không cắt ngang lúc chờ: nó có hiệu lực sau lượt chờ, ở ranh giới batch kế tiếp.
5. Nếu gặp >= 3 FloodWait trong 10 phút: giảm `batch_size` một nửa (mỗi lần đủ 3 flood thêm một nấc, tối thiểu 1) và nâng sàn của delay lên `min_delay × 2^nấc` (chặn bởi `max_delay`) cho tới khi qua 10 phút không có flood; có thông báo `throttled`. Batcher hỏi `batch_size` hiện hành cho từng unit nên có hiệu lực ngay từ batch kế tiếp.

`flood_log.kind`: `flood_wait`, `slow_mode` (SlowModeWait, xử lý như FloodWait), `peer_flood`. `delay_ms` là delay **trước** khi tăng (delay đã dẫn tới flood), `batch_size` là cỡ batch hiện hành.

## Dấu hiệu bị đánh dấu spam

- `PeerFloodError`: dừng ngay, `failed(peer_flood)`, khuyến cáo nghỉ >= 24h, đề nghị kiểm tra @SpamBot. Không retry, không tự tăng tốc lại.
- Đích hay báo `ChatWriteForbidden`/bị ban tạm: dừng và báo.

## Vệ sinh chung (in trong `tgmirror doctor` và README)

- Dùng tài khoản đã có lịch sử; tài khoản mới tinh dễ bị siết.
- Không chạy nhiều tool/nhiều session cùng lúc trên một account.
- Ưu tiên tin nhỏ (text/ảnh) trước, file lớn sau (tùy chọn `--order small-first` nhưng mặc định giữ thứ tự thời gian, D4).
- Đừng vượt quá `daily_cap` bằng cách chạy lại liên tục; chạy tay `tgmirror run` theo từng đợt nhỏ (delta) thay vì clone dồn một lượt cực lớn.
- Kênh đích mới: nên để vài phút sau khi tạo mới bắt đầu gửi; đặt tên/about hợp lệ; không spam link.
- Clone theo thứ tự thời gian, không đảo lộn.

## Cách đo và tinh chỉnh

`tgmirror status` (phase 5) hiển thị delay hiện tại và số tin đã gửi hôm nay (`limiter_state`), tốc độ trung bình (tin/giây), số lần Telegram giới hạn trong 24 giờ và lần gần nhất của lần chạy (`flood_log`); `tgmirror history n` liệt kê các lần giới hạn của một lần chạy. `flood_log` là nguồn dữ liệu để chỉnh mặc định ở các bản phát hành sau. Mọi thay đổi mặc định phải kèm ghi chú vì sao (dữ liệu nào).
