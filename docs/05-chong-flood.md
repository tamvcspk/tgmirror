# 05 — Chống FLOOD_WAIT & spam

> Telegram **không công bố** giới hạn chính xác và có thể thay đổi theo tài khoản, độ "tuổi" và hành vi. Các con số dưới đây là điểm khởi đầu bảo thủ, sẽ được chỉnh dựa trên `flood_log`. Không có cách nào đảm bảo tuyệt đối không bị giới hạn.

## Nguyên tắc

1. Ít lời gọi hơn: gom batch (chiến lược A), tránh `get_entity`/`GetHistory` thừa (cache entity).
2. Chậm và đều, có jitter: hành vi giống người dùng hơn bot.
3. Học từ phản hồi: mỗi FloodWait làm chậm lại; chạy êm thì nhích nhanh dần.
4. Dừng khi có dấu hiệu nặng (`PeerFlood`), không cố.
5. Một account, một lần chạy tại một thời điểm, tuần tự.

> **Trạng thái (phase 4)**: đã cài đặt đúng như dưới đây: `core/limiter.py` (AIMD theo làn, jitter, nghỉ dài, ngân sách học được theo cửa sổ trượt, `daily_cap` chỉ cảnh báo, bucket đọc, sàn slow mode, giảm `batch_size`, `limiter_state`) và `engine/flood.py` (`FloodGuard`: log `flood_log`, chờ rồi retry cùng lời gọi, dừng lần chạy khi quá `max_auto_wait`). Chưa kiểm chứng trên Telegram thật: các số mặc định chưa được đo, xem `06-lo-trinh.md` (spike 6).

## Limiter (`core/limiter.py`)

Mọi lời gọi đọc/ghi của một lần `run` đi qua `await limiter.acquire(cost, kind)` (`kind` = `write` hoặc `read`), thông qua `FloodGuard` của runner. `tgmirror backup` (phase 11a) dùng chung `FloodGuard`, chỉ mỗi `kind = read` (backup không ghi Telegram): `FloodGuard` được tổng quát hóa bằng `FloodOwner.of_run`/`of_backup` để ghi đúng cột (`flood_log.run_id` hay `backup_id`), xem `01-kien-truc.md`. `tgmirror inspect` (phase 18a) cũng dùng `FloodGuard`, chỉ `kind = read`, với `FloodOwner.of_inspect` (không có dòng `runs`/`backups` nên `flood_log` để trống cả `run_id` và `backup_id`; limiter state vẫn lưu theo account, nên lần chạy sau học được từ lần quét): một lần quét kênh lớn là hàng nghìn request đọc, không phải lệnh một lần. Các lệnh một lần do người dùng khởi động (`login`, `channels`, `new`: tạo kênh, `last_message_id`, `--preview`, `begin_backup`) không qua limiter: FloodWait ở đó in một câu và thoát mã 3, không retry.

- **Delay mục tiêu** `delay` (giây) giữa hai lời gọi ghi: bắt đầu `min_delay` (hoặc giá trị đã lưu, kẹp trong `[min_delay, max_delay]`). Lời gọi ghi đầu tiên của một lần chạy đi ngay.
- **AIMD**: khi flood → `delay = min(delay * 2, max_delay)`; sau mỗi 20 lời gọi thành công liên tiếp → `delay = max(delay * 0.9, min_delay)`.
- **Làn (lane, Phase 19d1, 2026-10-15)**: mỗi nhóm lời gọi có `delay` AIMD riêng, nên một flood chỉ làm chậm đúng nhóm bị chặn. Làn ghi là `(action, đích)` với `action` ∈ `forward` (`copy_messages`), `send` (`send_prepared`/`send_text`/`send_by_reference`/`upload_prepared`/`create_topic`), và từ Phase 20 `delete` (`delete_messages`, `tgmirror delete`; đích của làn là chính kênh bị xoá, `FloodOwner.of_op`); làn đọc là `action` ∈ `history` (`iter_messages`, `get_messages`, `count`, `list_topics`, `media_counters`, `fetch`), `file` (`prepare`, `export_unit`). Bảng ánh xạ phương thức → làn là `engine/flood.py::LANES`. Hệ quả: `on_flood` chỉ nhân đôi delay của làn đó; 20 thành công liên tiếp *của làn đó* mới giảm nó; sàn giảm vẫn là `min_delay × 2^nấc` chung. Ba flood trong 10 phút (giảm batch) và `daily_cap` vẫn tính cho cả account, không theo làn. `limiter_state.delay_ms` là delay cao nhất của các làn ghi (cái `status` hiển thị); một DB từ trước 19d1 chưa có làn nào thì mọi làn xuất phát từ con số duy nhất đó một lần.
- **Sàn slow mode**: đầu lần chạy, nếu đích là supergroup/forum, đọc `slowmode_seconds` của nó (`gateway.slowmode_seconds`, một request như `last_message_id`, ngoại lệ một-lần của luật 1; lỗi thì coi như không biết). Từ đó mọi lần đăng vào đích đó cách nhau **ít nhất** chừng ấy giây, kể cả khi jitter kéo delay xuống thấp hơn; thời gian đã mất cho việc tải lên được tính vào khoảng giãn cách như mọi lần `pace`. Áp cho cả tài khoản là admin (Telegram miễn slow mode cho admin, nhưng chưa kiểm trên tài khoản thật nên chọn phía an toàn). Có thông báo `slow_mode` một lần.
- **Đếm theo ô 5 phút**: tin gửi được cộng vào ô 5 phút (`unix // 300`) của từng làn ghi (`sent_buckets`, giữ 24 giờ; bảng tên `sent_hours`, ô 1 giờ, cho tới 2026-10-17, xem dưới). Mỗi hàng `flood_log` mới ghi `sent_1h`/`sent_24h` của làn bị chặn; từ 19d2 chính các ô này là bộ đếm của cửa sổ trượt mà ngân sách so sánh (`window_sent`: ô nằm trọn trong cửa sổ tính đủ, ô ở mép tính theo phần còn trong cửa sổ). Ô 1 giờ làm cửa sổ 1 giờ nhả tin trễ tới một giờ (lần chạy 69, 2026-10-17: dừng 02:32, `resume_at` 04:00, đợi 1 giờ 45 phút thay vì khoảng 1 giờ 5 phút); ô 5 phút giảm độ trễ tối đa còn 5 phút.
- **Jitter**: mỗi lần chờ nhân với `uniform(1 - jitter, 1 + jitter)`.
- **Long pause**: sau mỗi `long_pause_every` tin, nghỉ ngẫu nhiên trong `long_pause_range` giây.
- **Phân loại FloodWait (Phase 19d2, 2026-10-15)**: một FloodWait **dài hơn `short_flood` giây** (`[limits] short_flood`, mặc định 60, tạm cho tới khi có dữ liệu `flood_log`) trên làn ghi là *hạn mức*, không phải đợt bùng: nó **không** nhân đôi delay mà đặt/cắt **ngân sách** của làn đó. Chỉ `FLOOD_WAIT` thường mới là hạn mức; slow mode, `transport_429` và `premium_wait` luôn chỉ nhân đôi delay (chúng nói về luật của chat hay của đường truyền, không phải số tin tài khoản được gửi). Làn đọc luôn chỉ AIMD.
- **Ngân sách (làn ghi)**: một làn **chưa có ngân sách cho tới flood dài đầu tiên** của nó, và không bao giờ bị chặn bởi số đã gửi trước đó (chỉ pacing và FloodWait giới hạn nó). Flood dài đầu tiên đặt ngân sách = 80% số tin làn đó đã gửi trong cửa sổ mà `X` gợi ý (`X` ≤ 3600 s: cửa sổ 1 giờ; dài hơn: 24 giờ), không dưới `MIN_BUDGET` = 20 tin; các flood dài sau nhân 0,5 (giữ nguyên cửa sổ, cùng sàn). Mỗi 24 giờ liền không có flood dài, ngân sách tăng 10% (`grow_budget`, cũng là cách `status` đọc nó). Đó là giả thuyết (làm theo kế hoạch), kiểm bằng `flood_log` sau phát hành. Ngân sách và lần flood đặt ra nó lưu ở `limiter_lanes` (migration 5). **Từ 19d4** ngân sách còn nhớ `bad`/`ok` và tăng 10% có trần 95% của `bad` (xem "Thăm dò giới hạn" dưới).
- **Cửa sổ trượt**: một batch mà `đã gửi trong cửa sổ + cost > ngân sách` không được gửi (trừ khi làn chưa gửi gì trong cửa sổ: batch đầu lớn hơn ngân sách vẫn đi, để không kẹt vĩnh viễn). Chạm ngân sách → `BudgetReached` (cũ: `DailyCapReached`): lần chạy kết thúc ở `waiting_flood` với `fail_reason='daily_cap'` (giữ giá trị cũ để `history` không vỡ), thoát mã 3 kèm một câu giải thích; `resume_at` là lúc các xô giờ cũ nhất hết hạn đủ chỗ cho batch đó (một xô tính nguyên cho tới khi cả giờ của nó rời cửa sổ, nên không bao giờ sớm). Không còn mốc 00:00. `--wait` không áp dụng.
- **`daily_cap` chỉ cảnh báo**: khi số tin *đã đăng* của cả account (mọi làn ghi cộng lại, **trừ `delete`**: xoá không phải gửi) trong 24 giờ qua vượt `daily_cap`, in một cảnh báo `run.cap_warning` (một lần cho mỗi lần chạy, và một lần nữa khi vượt gấp đôi) rồi chạy tiếp. Mặc định 5000. Không ghi thêm vào dòng kết quả của lần chạy (chỉ là thông báo trên luồng).
- **Cost**: tin gửi tính theo số tin trong batch. Lời gọi đọc dùng bucket riêng, nhẹ hơn: `cost` là số request (một `iter_messages` = 1, cộng 1 cho mỗi đầu `--since`/`--until` vì gateway tra ngày → id; mỗi lần hoàn thiện album cũng là một `iter_messages`; `get_messages` của `retry` là 1 cho mỗi lô tối đa 100 id; `prepare` của chiến lược B là 1 cho mỗi unit), chờ `read_delay × (delay_làn / min_delay) × jitter` giữa hai lần đọc (lần đọc đầu của một lần chạy đi ngay), với `delay_làn` là delay của làn đọc (`history` hay `file`): từ 19d1 đọc chỉ chậm khi chính các lần đọc bị flood, không còn chậm theo flood của việc ghi (trước đó dùng chung một `delay`). Việc lật trang bên trong một `iter_messages` do Telethon giãn cách bằng `wait_time` (`READ_WAIT` = 1 s). `daily_cap` chỉ áp cho ghi.
- **Upload (chiến lược B, phase 6)**: mỗi unit tải lên lại là một lần `pace` như mọi lần ghi (cùng `min_delay`, jitter, nghỉ dài, `daily_cap` tính theo tin), và `prepare` (đọc lại tin + tải xuống) là một request đọc qua bucket đọc. **Chưa có delay cộng thêm theo dung lượng**: một upload lớn vốn đã chậm hơn khoảng giãn cách, và chưa có số liệu để chọn một hệ số; sẽ xét khi có `flood_log` (spike 6). Chỉ nửa đọc chạy trước (`prefetch`): việc gửi luôn tuần tự, một lời gọi một lúc, nên không tăng số lời gọi ghi đồng thời. FloodWait khi tải (`prepare`) xử lý như đọc (chờ rồi lặp lại; file đã tải xong được giữ), khi gửi (`send_prepared`, `send_text`) như ghi.
- Trạng thái (`delay`, `day`, `sent_today`, và từ 19d1 delay của từng làn `limiter_lanes` cùng ô 5 phút `sent_buckets`) lưu theo `mirrors.account`: cùng transaction với mỗi batch (`commit_batch(limiter=...)`) và ngay sau mỗi flood. Không lưu: cửa sổ 10 phút và mức giảm batch (tạm thời), sàn slow mode (đọc lại mỗi lần chạy).
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
| `daily_cap` | 5000 | Ngưỡng **nhắc** (không chặn) trên số tin cả account đã gửi trong 24 giờ qua, cửa sổ trượt. Từ Phase 19d2 chỉ ngân sách học được từ flood dài mới dừng lần chạy |
| `short_flood` | 60 s | FloodWait dài hơn con số này là hạn mức: đặt/cắt ngân sách của làn thay vì nhân đôi delay. Tạm, chỉnh từ `flood_log` |
| `max_auto_wait` | 900 s | FloodWait dài hơn → lần chạy kết thúc `waiting_flood`, không ngủ tiếp (trừ khi `--wait`) |
| `prefetch` | 0 | Số unit tải xuống trước trong lúc một unit đang tải lên (0 = không; tối đa 3). Tối đa `prefetch + 1` unit nằm trên đĩa. Thay cho `upload_concurrency` (chưa phát hành, bỏ hẳn: gửi vẫn một lúc một unit). Mặc định đổi 1 → 0 sau spike "chạy lại với hình dạng đúng" (2026-09-22, dưới): đo đúng hình dạng sản xuất (kết nối dùng chung, tái sử dụng) thì nhiều file/unit di chuyển cùng lúc vỡ **sớm hơn và nặng hơn** một unit chạy một mình, cả hai chiều |
| `tmp_budget_mb` | 2048 | Dung lượng đĩa tối đa cho các file đã tải xuống chờ gửi; unit lớn hơn ngân sách vẫn đi khi đĩa trống. Là trần cứng cho việc tải nhanh hơn gửi: chỗ được giữ trước khi tải; file không có `size` giữ 1 MiB; byte thật trên đĩa được đối chiếu sau khi tải (`01-kien-truc.md`, "Analyze và tiến độ") |
| `download_requests` | 4 | Số request tải xuống đang bay cùng lúc, ngân sách **riêng** của chiều tải xuống (`RequestBudget`, `core/pool.py`); 0 tắt pool cho tải xuống (Telethon tự tải, một request một lúc). Ngân sách **bắt đầu ở 2**, tăng một bậc sau mỗi 32 part liền không gặp lực cản (và không trong 30 giây sau lần gặp), **giảm một nửa** khi Telegram phản kháng. Trước 2026-09-23 tải xuống và tải lên dùng chung một ngân sách (`max_requests`, mặc định 8, đo sạch trên một video đơn không có lỗi mạng thật); một lần `--mode reupload` thật gặp `HTTP 429` ở tầng truyền liên tục rồi mất kết nối hẳn khi tải xuống ở 8 request đang bay, trong khi tải lên ở 8 vẫn ổn cùng lần chạy — nên tách ra hai ngân sách độc lập và hạ tải xuống xuống 4; tải lên giữ 8 (`upload_requests`, dưới) |
| `upload_requests` | 8 | Như `download_requests` nhưng cho chiều tải lên, ngân sách riêng. `upload_connections = 8` / `upload_requests = 8` (một request mỗi kết nối), một file tại một thời điểm, không có tải xuống nào chạy cùng, đo sạch **hai lần liền** (~24–28 MB/s, 2026-09-23); vượt quá theo bất kỳ hướng nào (nhiều request hơn kết nối, hay nhiều kết nối hơn 8) đều vỡ cả hai lần thử — xem "Kế hoạch tăng tốc" trong `06-lo-trinh.md` |
| `upload_connections` | 8 | Số kết nối (tối đa 16, đo an toàn tới 8) **riêng** mà phần tải lên chia part ra, tới cùng DC (dùng chung auth key), không tính kết nối chính. Không tạo được thì dùng ít hơn, không tạo được cái nào thì dùng kết nối chính. Tải xuống luôn một kết nối **riêng** của nó (spike 12: thêm kết nối không nhanh hơn). Dữ liệu lớn không bao giờ đi chung kết nối chính với các lời gọi đọc và đăng (lần chạy thật đầu tiên bị 429 khi tải xuống và tải lên cùng đi trên kết nối chính). 16 kết nối đã vỡ (TIMEOUT) trong lần đo 2026-09-23, nên đừng vượt 8 |
| `pool_min_mb` | 10 | File nhỏ hơn ngưỡng này giữ cách tải của Telethon (ít part, pool không có lợi). Phần tải lên chỉ dùng pool từ 10 MB (API big-file của Telegram) và chỉ cho tin đơn là tài liệu/video, album và ảnh giữ đường cũ |

Gửi bằng mã file (`Strategy.REFERENCE`): `fetch` là một request đọc có pace (như `prepare`), `send_by_reference` là một lần ghi qua `guard.write` (làn `send`, tính vào ngân sách và vào số cảnh báo `daily_cap`) và giãn cách như mọi lần ghi; một unit là một lần ghi, vì vậy nút cổ chai của loại này là pace và ngân sách chứ không phải tốc độ truyền. Lần thử lại sau `FileRefExpired` cộng thêm một `fetch` (một request đọc).

Request `count` mở đầu lần chạy (analyze) **không** qua bucket đọc: một lời gọi (vài request nếu có ngày) như các lần đọc chuẩn bị của `begin_run`; FloodWait của nó vẫn được ghi vào `flood_log` (`method='count'`) và xử lý như mọi FloodWait.

### Pace tính theo khoảng cách giữa hai lần đăng (2026-09-20, người dùng duyệt)

Điều Telegram giới hạn là tần suất **đăng tin**, không phải việc đẩy byte. Với unit lớn (`Strategy.REUPLOAD`, có file), runner đẩy byte trước (`guard.transfer("upload_prepared")`, không pace, không tính vào ngân sách hay `daily_cap`, FloodWait được ghi và chờ như mọi lần), đo bao lâu (`Runner._mono`), rồi `guard.pace(cost, credit)`: `Limiter.acquire(..., credit=)` trừ thời gian đó khỏi **delay** (`max(delay × jitter − credit, 0)`), còn nghỉ dài (`long_pause_every`) giữ nguyên đầy đủ. Vậy khoảng cách từ lúc đăng xong unit trước đến lúc đăng unit này vẫn ≥ `delay` (việc đẩy byte chỉ bắt đầu sau khi unit trước đã đăng xong; nếu đẩy lâu hơn delay thì không phải chờ thêm), và lần đăng đầu của một lần chạy vẫn đi ngay. Video 55 MB: mỗi unit ≈ 4,8 s → ≈ 2,8 s; file rất lớn hầu như không đổi; ảnh nhỏ vẫn bị pace chặn. Ngân sách của làn được kiểm bằng `check_budget` **trước** khi đẩy byte. Đường copy (forward), gửi bằng mã file, album và file nhỏ không có credit: pace như cũ.

### Pool request (2026-09-20)

Tải file bằng một request một lúc bị chặn bởi độ trễ (4 MB/s xuống, 3,5 MB/s lên trên máy thử); với vài request đang bay tải xuống được 30 MB/s (spike 12). `core/pool.py`:

- `RequestBudget`: một con số chung cho mọi request truyền file đang bay; `acquire(priority)` (số nhỏ đi trước: tải lên thuộc unit cũ hơn nên ưu tiên hơn tải xuống của unit kế), AIMD như limiter: `succeeded()` tăng, `pressure()` giảm một nửa (tối thiểu 1) và tạm ngừng tăng 30 giây.
- `run_parts(count, work, budget)`: các part được nhận theo thứ tự bởi tối đa `download_requests`/`upload_requests` (tuỳ chiều) worker, worker rảnh lấy part kế nên kết nối chậm tự làm ít hơn; mỗi part đúng một lần. `TransportPressure` giảm ngân sách và: **HTTP 429 ở tầng truyền không lặp tại chỗ** mà kết thúc cả lần truyền bằng `FloodWait(60, transport=True)` (`TRANSPORT_WAIT`), để `FloodGuard` ghi `flood_log` với loại **`transport_429`**, tăng delay (AIMD), chờ rồi lặp lại `prepare`/`upload_prepared` (lần chạy thật đầu tiên: thử lại trong 15 giây chỉ làm hỏng lần chạy); kết nối bị đóng, không có trả lời trong 30 giây, part sai độ dài thì lặp part sau `1s × 2^n` (±25%), tối đa 5 lần rồi `Transient`; `FloodWait` giảm ngân sách và kết thúc cả lần truyền tức thì (`FloodGuard` chờ rồi lặp lại `prepare`/`send_prepared` từ đầu: chưa nhớ part đã xong); lỗi khác hủy các worker còn lại.
- RAM: mỗi worker giữ tối đa một part (1 MiB xuống, 512 KiB lên), nên tối đa `download_requests` MiB xuống cộng `upload_requests × 0.5` MiB lên.
- **Tải xuống không làm lại từ đầu sau flood hay mất kết nối**: file `.part` và tập part đã ghi được giữ (`_partial`), lần `prepare` lặp lại nhặt tiếp và tiến độ bắt đầu từ chỗ cũ; lỗi khác thì xóa file dở. Tải lên chưa nhớ part (đẩy lại từ đầu).
- **Tạo được ít kết nối hơn số xin cũng là một cảnh báo** (`_upload_senders`/`_download_sender`, logger `tgmirror.core.telethon_gateway`, một lần cho mỗi gateway): không tạo được kết nối riêng thì pool lùi về ít kết nối hơn, hay về kết nối chính, và đó là chậm hẳn (1 kết nối ~3 MB/s lên, 2–4 kết nối 18–28 MB/s, spike 2026-09-21); trước đây lùi âm thầm.
- **Mỗi lần ngân sách giảm là một cảnh báo** (`RequestBudget.pressure(reason)`, logger `tgmirror.core.pool`; CLI in `[cảnh báo] transfer requests in flight: 4 -> 2 (lý do); growing again after 32 clean parts` ra stderr): một lần truyền bỗng chạy chậm gấp nhiều lần thường là ngân sách đã tụt về 1 request, và đây là cách thấy điều đó.
- Ngân sách chỉ sống trong tiến trình.

## Xử lý FloodWait

Client tạo với `flood_sleep_threshold=0` nên Telethon **luôn raise** `FloodWaitError`, để limiter:

1. Ghi `flood_log` (method, seconds, delay hiện tại, batch_size).
2. `on_flood(seconds)` → tăng delay (AIMD).
3. `seconds <= max_auto_wait`: in một dòng thông báo tĩnh (`run.flood_waiting`; đếm ngược động không được làm ở đợt TUI 2026-09-23, ngoài phạm vi mock của `02-cli-ux.md`, xem `06-lo-trinh.md` "Phase 7 — ghi chú"), ngủ `seconds + uniform(1, 5)`, rồi retry **cùng lời gọi** (batch vẫn `pending`, không dựng lại, con trỏ không nhúc nhích). Ghi (`copy_messages`) và đọc (`iter_messages`) đều vậy; đọc bị cắt giữa chừng thì đọc tiếp từ tin đã trao cuối cùng, nên không sót, không lặp, album không bị tách.
4. `seconds > max_auto_wait`: xóa `pending` của batch bị từ chối (Telegram không tạo gì), đặt `waiting_flood` + `resume_at = now + seconds`, thoát mã 3. Với `run --wait`: vẫn ngủ tiếp (ngắt được bằng pause/stop/Ctrl+C).
   Cùng một lời gọi bị FloodWait liền 5 lần (`MAX_FLOODS_PER_CALL`) thì cũng dừng như trên, thay vì ngủ vô hạn (lựa chọn khi cài đặt, không phải D1–D9).
   Ngủ luôn ngắt được bởi stop: `stop`/phím `q`/Ctrl+C trong lúc chờ kết thúc lần chạy `stopped`, batch chưa gửi được xóa khỏi `pending`. `pause` không cắt ngang lúc chờ: nó có hiệu lực sau lượt chờ, ở ranh giới batch kế tiếp.
5. Nếu gặp >= 3 FloodWait trong 10 phút: giảm `batch_size` một nửa (mỗi lần đủ 3 flood thêm một nấc, tối thiểu 1) và nâng sàn của delay lên `min_delay × 2^nấc` (chặn bởi `max_delay`) cho tới khi qua 10 phút không có flood; có thông báo `throttled`. Batcher hỏi `batch_size` hiện hành cho từng unit nên có hiệu lực ngay từ batch kế tiếp.

`flood_log.kind`: `flood_wait`, `slow_mode` (SlowModeWait, xử lý như FloodWait), `premium_wait` (`FLOOD_PREMIUM_WAIT_X`, từ 19d1: lúc truyền file trên tài khoản không Premium; hiện vẫn xử lý như FloodWait, tức vẫn tăng delay của làn), `transport_429`, `peer_flood`. `delay_ms` là delay của làn **trước** khi tăng (delay đã dẫn tới flood), `batch_size` là cỡ batch hiện hành. Từ 19d1 mỗi hàng còn có `action` (làn), `dst` (đích, chỉ làn ghi), `sent_1h`/`sent_24h` (làn ghi đã gửi bao nhiêu tin trong 1 giờ/24 giờ qua, xấp xỉ theo xô giờ) và `premium` (tài khoản có Premium không; `NULL` = không biết, ví dụ `inspect`). Hàng cũ để trống các cột đó.

## Dấu hiệu bị đánh dấu spam

- `PeerFloodError`: dừng ngay, `failed(peer_flood)`, khuyến cáo nghỉ >= 24h, đề nghị kiểm tra @SpamBot. Không retry, không tự tăng tốc lại.
- Đích hay báo `ChatWriteForbidden`/bị ban tạm: dừng và báo.

## Vệ sinh chung (in trong `tgmirror doctor` và README)

- Dùng tài khoản đã có lịch sử; tài khoản mới tinh dễ bị siết.
- Không chạy nhiều tool/nhiều session cùng lúc trên một account.
- Ưu tiên tin nhỏ (text/ảnh) trước, file lớn sau (tùy chọn `--order small-first` nhưng mặc định giữ thứ tự thời gian, D4).
- Đừng bỏ qua cảnh báo `daily_cap` bằng cách chạy lại liên tục; chạy tay `tgmirror run` theo từng đợt nhỏ (delta) thay vì clone dồn một lượt cực lớn.
- Kênh đích mới: nên để vài phút sau khi tạo mới bắt đầu gửi; đặt tên/about hợp lệ; không spam link.
- Clone theo thứ tự thời gian, không đảo lộn.

## Cách đo và tinh chỉnh

`tgmirror status` (phase 5) hiển thị delay hiện tại, số tin đã gửi trong 24 giờ qua (ô 5 phút `sent_buckets`) và ngân sách học được của từng làn ghi vào đích của lần chạy (hay "chưa học") cùng lần flood đặt ra nó (19d2), tốc độ trung bình (tin/giây), số lần Telegram giới hạn trong 24 giờ và lần gần nhất của lần chạy (`flood_log`); `tgmirror history n` liệt kê các lần giới hạn của một lần chạy (từ 19d1 kèm làn, đích và số tin làn đó đã gửi trong 1 giờ/24 giờ; `status` ghi `loại/làn`). `flood_log` là nguồn dữ liệu để chỉnh mặc định ở các bản phát hành sau. Mọi thay đổi mặc định phải kèm ghi chú vì sao (dữ liệu nào).

## Kế hoạch: rate limit thích nghi (Phase 19d; 19d1 và 19d2 đã cài, 19d3 chưa)

> Người dùng duyệt hướng ngày 2026-10-15 (phương án B bên dưới). **19d1 (đo, làn, sàn slow mode) và 19d2 (ngân sách tự học, cửa sổ trượt, `daily_cap` chỉ cảnh báo) đã có code** (cả hai thuộc 0.2.0), mô tả hành vi hiện tại ở phần "Làn", "Sàn slow mode", "Đếm theo giờ", "Phân loại FloodWait", "Ngân sách" và "Cửa sổ trượt" phía trên; phần dưới là lý do và thiết kế gốc. Chỉ Takeout (19d3) là chưa. Lát cắt, tiêu chí hoàn thành và câu hỏi còn chờ trả lời nằm ở `06-lo-trinh.md`, "Phase 19d — kế hoạch". Không đổi D1–D10 (D6 giữ nguyên: mọi FloodWait vẫn vào limiter của mình).

### Vì sao

1. **Một `delay` chung cho mọi lời gọi ghi.** Telegram tính flood theo *phương thức và tham số* (`FLOOD_WAIT_X`), vậy mà forward, gửi lại media và lần đăng sau khi upload đang bị phạt cùng nhau. Việc đọc cũng chậm theo delay ghi (`read_delay × delay / min_delay`).
2. **Mọi FloodWait được xử lý như nhau** (nhân đôi delay), dù `X = 8 s` (vượt tốc độ tức thời) và `X = 20 000 s` (chạm hạn mức theo cửa sổ thời gian) là hai tín hiệu khác hẳn nhau.
3. **`daily_cap = 5000` là số đoán và không bao giờ tự chỉnh.** Tài khoản tốt bị chậm vô ích, tài khoản yếu vẫn có thể bị chặn trước khi chạm cap. Mốc 00:00 giờ máy chắc chắn không phải cách Telegram đếm.
4. **Bỏ qua những số biết chắc**: slow mode của nhóm đích (đọc được trước khi gửi), và việc tài khoản có Premium hay không (`FLOOD_PREMIUM_WAIT_X` khi truyền file).
5. **`flood_log` thiếu ngữ cảnh để học**: không ghi kênh đích, loại hành động, hay số tin đã gửi trong 1 giờ/24 giờ lúc bị chặn.

### Yếu tố ảnh hưởng tới giới hạn

| Yếu tố | Đo trực tiếp được? | Dùng thế nào |
|---|---|---|
| Loại hành động: forward / gửi lại (media, album, text, tham chiếu) / đọc lịch sử / tải file / tải lên | Có | Mỗi loại một trạng thái limiter riêng |
| Kênh đích (giả thuyết: giới hạn ghi tính theo từng chat; bước 1 kiểm lại) | Có | Kênh đích là một phần của khóa trạng thái ghi |
| Slow mode của nhóm đích (`slowmode_seconds`) | Có | Sàn cứng cho delay ghi vào đích đó, khỏi phải đợi `SLOWMODE_WAIT` mới biết |
| Premium (`get_me().premium`) | Có | Chỉ ảnh hưởng truyền file: `FLOOD_PREMIUM_WAIT_X` đè lên `RequestBudget`, không đụng delay ghi |
| Cỡ lời gọi (album 10 tin, forward 100 id) | Có | Chi phí tính cả số lời gọi lẫn số tin (như nay: pace theo lời gọi, cap theo tin) |
| Độ lớn của `X` | Có | Tín hiệu chính để phân loại flood (dưới) |
| Tuổi và uy tín tài khoản, IP/VPN, tải của DC | Không | Chỉ suy ra được qua FloodWait, nên phải **học** từ `flood_log` |

### Thiết kế (phương án B: ngân sách tự học theo từng khóa)

- **Khóa trạng thái.** Ghi: `(account, action, dst)` với `action` ∈ `forward` (`copy_messages`), `send` (`send_prepared`/`send_text`/`send_by_reference`). Đọc: `(account, action)` với `action` ∈ `history` (`iter_messages`, `get_messages`, `count`, `list_topics`, `media_counters`, `fetch`), `file` (`prepare`/`export_unit`). Mỗi khóa có delay AIMD riêng, được lưu lại; đọc không còn nhân theo delay ghi. Byte tải lên/xuống vẫn do `RequestBudget` lo như hiện nay.
- **Sàn biết trước.** Khi mở lần chạy, đọc `slowmode_seconds` của đích (nằm trong phần chuẩn bị một lần, như `last_message_id`). Sàn delay của khóa ghi vào đích đó = `max(min_delay, slowmode_seconds)`. Ghi Premium vào `flood_log`. `FLOOD_PREMIUM_WAIT_X` có `kind` riêng (`premium_wait`): giảm `RequestBudget` rồi chờ, không tăng delay ghi.
- **Phân loại FloodWait theo `X`** (ngưỡng `short_flood`, tạm là 60 s và là một khóa `[limits]`; vì 19d2 thuộc 0.2.0 nên không đợi dữ liệu của 19d1, ngưỡng sẽ được chỉnh từ `flood_log` sau phát hành):
  - **ngắn**: vượt tốc độ tức thời. AIMD trên delay *của đúng khóa đó*, như nay.
  - **dài**: chạm hạn mức theo cửa sổ. Ghi `sent_1h`/`sent_24h` của khóa lúc bị chặn, rồi hạ ngân sách của cửa sổ tương ứng (giả thuyết: `X` ≤ 1 giờ là cửa sổ 1 giờ, dài hơn là cửa sổ 24 giờ) xuống khoảng 80% số đã gửi trong cửa sổ đó.
- **AIMD trên chính ngân sách cửa sổ, và đó là cái phanh duy nhất chặn lần chạy.** Một làn **chưa có ngân sách cho tới flood dài đầu tiên** (chỉ pacing và FloodWait giới hạn nó). Flood dài đầu tiên đặt ngân sách khoảng 80% số đã gửi trong cửa sổ, các lần sau nhân 0,5, không xuống dưới một sàn nhỏ. Cứ 24 giờ liền không có flood dài thì ngân sách tăng khoảng 10%. Việc hồi phục dần để một lần flood do DC quá tải không phạt mãi mãi. Không lấy 5000 làm điểm xuất phát, vì như vậy sẽ chặn đúng như hôm nay.
- **Cửa sổ trượt 24 giờ thay cho mốc 00:00 giờ máy** (người dùng chốt 2026-10-15). Bộ đếm là ô 5 phút `sent_buckets` (ban đầu là xô giờ `sent_hours` của 19d1, đổi ở migration 6). `resume_at` = lúc đủ số tin cũ trong cửa sổ hết hạn để batch kế tiếp đi được.
- **`daily_cap` thành trần an toàn chỉ cảnh báo, không chặn** (người dùng chốt 2026-10-15): khi số tin của account trong 24 giờ qua vượt nó, in một cảnh báo rồi chạy tiếp. Mặc định giữ 5000.
- **Hiển thị.** `status`, dòng mở đầu lần chạy và pre-flight nói ngân sách hiện hành của khóa và lý do (lần flood nào làm nó giảm), để người dùng không thấy một con số bí ẩn.
- **Takeout (tùy chọn, bước 3).** Đọc của `backup`/`inspect`/chỉ mục đi qua `invokeWithTakeout` (giới hạn flood nới hơn, chỉ cho xuất dữ liệu), vẫn qua gateway và `FloodGuard`. Không dùng cho phía ghi của clone, vì Takeout không áp dụng cho việc đăng tin.

### Đánh đổi

- **Phải chạm flood mới học được**, và một lần học có thể là vài giờ bị chặn. Giảm thiệt hại bằng cách xuất phát bảo thủ (5000), tăng chậm (+10%/ngày), và giữ cách dừng lần chạy khi `X > max_auto_wait`.
- **Nhiễu**: một lần flood do tải của DC có thể bị hiểu nhầm thành hạn mức của tài khoản. Vì vậy chỉ flood dài mới cắt ngân sách, và ngân sách hồi phục theo thời gian.
- **Phức tạp hơn**: migration (`limiter_state` theo khóa, thêm cột cho `flood_log`), nhiều trạng thái hơn. Vẫn test được bằng đồng hồ giả, không cần mạng.
- **Hai giả thuyết chưa kiểm** (giới hạn ghi theo từng đích; `X` dài ứng với hạn mức cửa sổ). Bước 1 thu dữ liệu để kiểm trước khi bước 2 dựa vào chúng.
- **Phương án đã loại**: A — profile tĩnh theo loại tài khoản (vẫn là đoán, chỉ nhiều số hơn); C — bỏ hẳn cap, chỉ phản ứng (không có phanh trước hạn mức ngày, mỗi lần chạm là một lần bị chặn dài).

### Kiểm lại một bản tổng hợp "rate limit chính thức" (2026-10-15)

Người dùng đưa một bản tổng hợp về flood control của MTProto. Những gì đã đối chiếu, để khỏi kiểm lại lần sau:

- **Đúng**: Telegram không công bố số giới hạn. Các mã `FLOOD_WAIT_X`, `SLOWMODE_WAIT_X`, `FLOOD_PREMIUM_WAIT_X`, `PEER_FLOOD`, `TAKEOUT_INIT_DELAY_X` đều có thật. Giới hạn file 2 GB (free) / 4 GB (Premium) cũng đúng.
- **Sai**: part tải lên tối đa **512 KB**, không phải 1 MB; chỉ tải xuống mới được 1 MB (code đã dùng 512 KiB cho chiều lên).
- **Không có trong docs chính thức**: "an toàn là 3–5 kết nối" (số đo của mình: tải lên 8, tải xuống 4, xem bảng mặc định), và "retry sớm thì `X` tăng theo cấp số nhân" (lời đồn; dù sao tgmirror cũng không bao giờ retry trước khi hết `X`).
- **Takeout**: đúng là giới hạn nới hơn, nhưng chỉ cho việc đọc/xuất dữ liệu, không cho việc ghi vào kênh đích.

## Thăm dò giới hạn (Phase 19d4; phần kèm 0.2.0 đã cài, `--probe` chưa)

> Người dùng chốt hướng ngày 2026-10-17 (ba điểm ở `06-lo-trinh.md`, "Phase 19d4 — kế hoạch"). **Đã cài (0.2.0)**: hai mốc `ok`/`bad`, `checked_at`, `probe_fails` theo làn ghi (migration 7); trần 95% của `bad` và hết hạn của `bad` cho tăng theo đồng hồ; flood trên mức `ok` đưa ngân sách về `ok`; `tgmirror limits`, `tgmirror limits reset [--dst ĐÍCH | --all]`, `--reset-limits` trên `clone`/`run`/`retry`; khóa `[limits] probe_after_hours` (168). **Chưa cài (sau 0.2.0)**: `--probe`, thăm dò một bước, nhích `ok` khi làn chạm mức đã tăng mà không flood, `probe_step`, câu hỏi tự động, cột `flood_log.probe`; `probe_fails` và `checked_at` vì vậy chỉ được ghi bởi flood dài, và `ok` chỉ có giá trị từ migration (hoặc bị quên). Làm tròn: trần là `floor(0.95 × bad)`, nên với `bad = 1882` là 1787 (kế hoạch ghi 1788). Không đổi D1–D10.

### Điểm mù

Ngân sách của 19d2 chỉ nhận thông tin từ một nguồn: một flood dài của Telegram, lúc đó nó đặt mức bằng 80% số đã gửi (hoặc nhân một nửa). Sau đó chính nó là cái phanh, nên lần chạy 69 và 70 tự dừng ở 1506 tin/giờ, trước khi Telegram kịp phản ứng, và hệ thống không học được gì mới. Tăng duy nhất là +10% mỗi 24 giờ theo đồng hồ (`grow_budget`), bất kể làn có chạm phanh hay không: lên gấp đôi mất khoảng 7 ngày, gấp mười mất khoảng một tháng. Nếu tài khoản già đi hay Telegram nới giới hạn thì tgmirror không bao giờ tận dụng, và nếu hạn mức thật thấp hơn thì chỉ phát hiện bằng cách bị chặn thêm. Ta đã có thông tin mà ngân sách không dùng: lần chạy 69, 70 chạy sạch 1506 tin/giờ, lần chạy 68 bị chặn ở 1882 tin/giờ.

### Thiết kế

Giống một bộ điều khiển tắc nghẽn: tăng chỉ khi có bằng chứng, giảm khi bị chặn, và nhớ cả hai phía.

- **Hai mốc theo làn ghi**: `ok` (mức cao nhất đã chạy sạch một cửa sổ khi làn thực sự bị ngân sách giữ) và `bad` (mức thấp nhất từng gây flood dài, kèm `bad_at`), cộng `checked_at` (lần cuối giới hạn được đem ra thử: một flood dài hoặc một lần thăm dò sạch) và `probe_fails`. Ví dụ số: `ok = 1506`, `bad = 1882`.
- **Thăm dò một bước** (khi người dùng đồng ý, dưới): trần của lần chạy lên điểm giữa `ok` và `bad` (≈1694 nếu `ok` còn 1506; khi tăng chậm đã đưa `ok` tới sát 95% của `bad` thì bước tới chính `bad`: sạch nghĩa là `bad` chỉ là nhất thời và được xoá). Không có `bad`, hoặc `bad` đã hết hạn sau `probe_after_hours`: `ok × probe_step` (1,25). Chạm trần mà không flood thì `ok` = trần, `checked_at` = bây giờ (≈1694, rồi ≈1788, hội tụ khi hai mốc cách nhau dưới 5%). Một lần chạy chỉ thăm dò một bước, để giá của một thăm dò hỏng có chặn trên: một lần chờ flood (cỡ 30 phút như lần chạy 68, hoặc dừng ở `max_auto_wait`).
- **Thăm dò hỏng** (flood dài khi trần lớn hơn `ok`): `bad` = số đã gửi lúc đó, ngân sách về `ok` (không cắt một nửa: ta vừa biết thêm chứ không phải giới hạn xấu đi), `probe_fails += 1`, `checked_at` = bây giờ. **Flood khi chưa vượt `ok`** mới là bằng chứng giới hạn xấu đi: cắt như hiện nay (80% hoặc ×0,5) và `ok` quên.
- **Tăng chậm theo đồng hồ, có trần và có chỗ dừng** (người dùng chốt 2026-10-17): giữ +10% mỗi 24 giờ của 19d2, nhưng không bao giờ vượt **95% của `bad`** (`floor(0.95 × bad)`). Chạm trần thì **giữ nguyên** mức đó, không tăng nữa, cho tới khi người dùng chọn thăm dò hoặc làn bị chặn. Với `ok = 1506`, `bad = 1882`: 1657 sau một ngày, rồi 1788 (trần), rồi đứng yên. Khi làn thực sự chạm mức đã tăng đó mà không flood thì `ok` nhích lên bằng mức đó (có bằng chứng). Flood khi ngân sách đang cao hơn `ok` được xử lý như một thăm dò hỏng (`bad` = số đã gửi, ngân sách về `ok`).
- **Hết hạn của `bad`**: sau `probe_after_hours` (mặc định 168, tức 7 ngày) mà không flood nào nữa thì `bad` không còn là cận trên. **Mức đang dùng giữ nguyên** (người dùng chốt): nó không bị hạ, cũng không còn tự tăng (không còn cận trên để tăng tới), cho tới khi người dùng chọn thăm dò hoặc làn bị chặn. Chỉ có bước thăm dò kế tiếp đổi: không có `bad` thì nhắm `ok × probe_step`.

### Cờ, lệnh, câu hỏi

- `--probe` trên `clone`/`run`/`retry`: thăm dò một bước, coi như đã đồng ý nhận một flood nếu hỏng. `--reset-limits`: quên ngân sách (và `ok`/`bad`) của các làn ghi vào đích này, làn quay về "chưa học" (không có phanh cho tới flood dài kế tiếp). Cả hai chủ động, **không hỏi lại**.
- `tgmirror limits` hiện từng làn (`ok`, `bad`, ngân sách, `checked_at`, số lần thăm dò hỏng); `tgmirror limits reset [--dst ĐÍCH | --all]` cũng không hỏi.
- **Cờ là tùy chọn của một lần chạy**: không vào `RunOptions.for_pair`, không tạo `mirrors` mới, không đổi `filters_json` hay con trỏ. Chạy lại cùng cặp với `--probe` là một dòng `runs` mới của cùng cặp, tiếp từ chỗ đang dừng. (Đúng với nguyên tắc chung đã có: chỉ filter đổi hoặc `--fresh` mới làm lại từ đầu; một tùy chọn khác không đổi cặp.)
- **Câu hỏi** (chỉ khi có terminal; cùng một đường cho `clone`/`run` dạng lệnh, wizard và menu): hỏi **ở đầu lần chạy**, sau bước phân tích, không giữa lúc đang chạy. Điều kiện: không có cờ **và** lần chạy này sẽ chạm ngân sách của một làn (tổng còn lại lớn hơn chỗ trống trong cửa sổ, hoặc làn đang kín sẵn) **và** `checked_at` cũ hơn `probe_after_hours` (nhân 2 sau mỗi thăm dò hỏng, tối đa 30 ngày). Ba lựa chọn: giữ ngân sách / thăm dò một bước / quên giới hạn. Nếu giới hạn vừa được thử gần đây thì không hỏi, chỉ in lần thử cuối và cách dùng `--probe`, để khỏi hỏi lại mỗi lần chạy như lần 69, 70.
- Không có terminal: không bao giờ hỏi, không bao giờ tự thăm dò. `--yes` **không** thay cho `--probe` (cùng tinh thần D3: sự đồng ý chấp nhận rủi ro phải được nói ra); với câu hỏi nó trả lời "giữ ngân sách".
- `flood_log` thêm cột `probe` để tách flood do thăm dò khỏi flood tự nhiên khi phân tích spike 15.

### Đánh đổi

- **Một thăm dò hỏng là một flood thật** (cỡ nửa giờ chờ, hoặc dừng ở `max_auto_wait`) và cộng vào bộ đếm 3 flood/10 phút của throttle. Chặn trên: một bước mỗi lần chạy, hỏi thưa dần sau mỗi lần hỏng.
- **Hai mốc chỉ có ý nghĩa nếu giới hạn đứng yên đủ lâu** để một cửa sổ sạch chứng minh được gì; `bad` hết hạn là để chống ngược lại.
- **Tăng theo đồng hồ vẫn còn, nên vẫn có một vùng người dùng không đồng ý trước**: từ `ok` tới 95% của `bad`. Vùng đó đã được Telegram đo ở trên (đã bị chặn ở `bad`) nên rủi ro thấp, và trần 95% cùng chỗ dừng đảm bảo không bao giờ tự đi vào vùng đã hỏng hay vùng chưa biết. Đổi lại, một flood trong vùng này (ví dụ do tải của DC) bị tính thành thăm dò hỏng: hạ `bad`, ngân sách về `ok`.
- **Lỗ hổng của 19d2 hiện tại** (phát hiện khi cân nhắc điều này): `grow_budget` không có trần. Với ngân sách 1506 đặt lúc 2026-10-16 17:05 UTC và flood ở 1882, nó sẽ vượt 1882 sau khoảng ba ngày (1657, 1822, 2004: từ 2026-10-19 17:05 UTC) và một lần chạy dài lúc đó có thể tự đi vào vùng đã biết là hỏng. Phần trần 95% vì vậy thuộc phần của 19d4 kèm 0.2.0, không để sau. **Đã vá** (`grow_budget` có trần, test `test_growth_by_the_clock_stops_at_95_percent_of_bad_and_stays_there`).
- Dữ liệu hiện chỉ có một đích và một điểm flood. Hai giả thuyết (cửa sổ 1 giờ, giới hạn theo đích) vẫn chưa kiểm; `ok`/`bad` theo từng làn ghi chính là cách đo thêm.
- **Phương án đã cân và không chọn**: C (phục hồi nhanh về `peak` kiểu CUBIC; chồng chéo với `ok`), F (trải đều pace thay vì chạy dồn rồi chờ; là cải thiện trải nghiệm riêng, không trả lời điểm mù, và chưa có bằng chứng Telegram phân biệt tốc độ dồn với trải đều: lần 68 bị chặn ở 61 tin/phút, lần 69, 70 chạy sạch ở 83 tin/phút).
