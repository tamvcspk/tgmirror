# 04 — State, checkpoint, delta

Một file SQLite (`WAL` mode, `foreign_keys=ON`, `busy_timeout=5000` để `pause`/`stop` từ process khác không bị lỗi khóa). Phiên bản schema nằm ở `PRAGMA user_version`; `store/schema.sql` là phiên bản 1, các thay đổi sau thêm migration đánh số ở `store/db.py` (mỗi migration chạy trong một transaction, có test nâng cấp từ phiên bản trước).

## Schema

```sql
CREATE TABLE jobs (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  account       TEXT NOT NULL,              -- tên session
  src_id        INTEGER NOT NULL,           -- peer id nguồn
  src_title     TEXT,
  src_kind      TEXT NOT NULL,              -- broadcast|supergroup|forum|group
  dst_id        INTEGER NOT NULL,
  dst_title     TEXT,
  mode          TEXT NOT NULL,              -- auto|copy|reupload
  filters_json  TEXT NOT NULL,
  options_json  TEXT NOT NULL,              -- batch_size, dst_base_id, pushdown (xem dưới); key lạ bị bỏ qua
  status        TEXT NOT NULL,              -- created|running|paused|stopped|waiting_flood|done|failed
  control       TEXT NOT NULL DEFAULT 'none', -- none|pause|stop  (do CLI đặt, runner đọc)
  cursor_src_id INTEGER NOT NULL DEFAULT 0, -- id nguồn lớn nhất đã xử lý xong (done/failed/filter-skip)
  resume_at     TEXT,                       -- khi waiting_flood
  fail_reason   TEXT,
  stats_json    TEXT NOT NULL DEFAULT '{}', -- done, failed, skipped_filter, bytes, ...
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE msg_map (
  job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  src_msg_id   INTEGER NOT NULL,
  dst_msg_id   INTEGER,                     -- NULL nếu pending/failed
  grouped_id   INTEGER,
  src_topic_id INTEGER,                     -- NULL nếu nguồn không phải forum
  status       TEXT NOT NULL,               -- pending|done|failed|skipped
  reason       TEXT,
  batch_id     INTEGER,
  ts           TEXT NOT NULL,
  PRIMARY KEY (job_id, src_msg_id)
);
CREATE INDEX msg_map_status ON msg_map(job_id, status);

CREATE TABLE topic_map (                    -- chỉ job forum
  job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  src_topic_id INTEGER NOT NULL,            -- id tin gốc của topic (General = 1)
  dst_topic_id INTEGER NOT NULL,
  title        TEXT,
  PRIMARY KEY (job_id, src_topic_id)
);

CREATE TABLE flood_log (
  id        INTEGER PRIMARY KEY,
  job_id    INTEGER,
  ts        TEXT NOT NULL,
  kind      TEXT NOT NULL,                  -- flood_wait|slow_mode|peer_flood
  seconds   INTEGER,
  method    TEXT,
  delay_ms  INTEGER,                        -- delay của limiter tại thời điểm đó
  batch_size INTEGER
);

CREATE TABLE limiter_state (               -- persist AIMD giữa các lần chạy
  account   TEXT PRIMARY KEY,
  delay_ms  INTEGER NOT NULL,
  day       TEXT NOT NULL,                  -- YYYY-MM-DD (local)
  sent_today INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
```

`options_json.dst_base_id`: id tin mới nhất của kênh đích lúc tạo job (`gateway.last_message_id`). Reconcile chỉ đọc đích sau `max(dst_msg_id của các hàng done, dst_base_id)`.

`options_json.pushdown` (mặc định `true`): `false` thì đọc mọi tin sau cursor và chỉ lọc ở máy (`new --no-pushdown`, xem `03-filters.md`).

`jobs.filters_json`: JSON chuẩn hóa của `FilterSpec` (`{}` = không lọc), nạp lại bằng `FilterSpec.from_json` mỗi lần `run`; hỏng thì job `failed` với lý do `FilterError: ...`.

Tin bị **filter loại** không được ghi vào `msg_map` (hàng triệu hàng vô ích); chỉ tăng bộ đếm `stats.skipped_filter` (đếm theo tin, album tính đủ mọi tin) và đẩy `cursor_src_id` tiến lên. Số đếm và con trỏ đi cùng transaction với batch mà chúng được cộng vào (`commit_batch(extra_stats=...)`), hoặc riêng một transaction `advance_cursor(extra_stats=...)` khi batch không có gì để gửi (chỉ có tin bị loại, hoặc mọi unit đã `done`); không bao giờ vượt qua một unit chưa xử lý. Khi Telegram thu hẹp theo nội dung (`media`/`search`) thì tin bị loại ở server không được thấy nên không được đếm và con trỏ chỉ tới unit khớp cuối cùng; lần `run` sau đọc lại từ đó (rẻ, vì vẫn thu hẹp).

Tin **không hỗ trợ** (game, invoice, quiz chưa trả lời, poll khi thiếu `--reset-polls`; xem `01-kien-truc.md`) khác filter: người dùng muốn clone nhưng không thể, nên có ghi `msg_map` với `status='skipped'` + `reason='unsupported:<loại>'` và tăng `skipped_unsupported`. `retry` chỉ thử lại `failed`, không thử `skipped`.

`topic_map` được ghi cùng transaction với việc tạo topic đích (tạo topic xong phải lưu ngay, kẻo resume tạo trùng).

## Quy tắc transaction

1. **Trước** khi gọi Telegram cho một batch: `INSERT msg_map(... status='pending', batch_id)` (write-ahead), commit.
2. **Sau** khi Telegram trả kết quả: trong **một transaction** — cập nhật các hàng thành `done` (kèm `dst_msg_id`) hoặc `failed` (kèm `reason`), cập nhật `cursor_src_id`, `stats_json`, `updated_at`, `limiter_state`.
3. `cursor_src_id` chỉ tiến (`MAX(cursor_src_id, ?)` trong SQL), và chỉ tiến tới id lớn nhất của batch đã kết thúc hoàn toàn (không còn `pending`; `commit_batch` từ chối nếu còn hàng `pending` nào khác).

Kết quả của một lời gọi copy:

- Lời gọi trả về bình thường: từng id có `dst_id` → `done`; id `None` (Telegram không tạo tin, thường vì đã xóa ở nguồn) → `failed` với `reason='not_copied'`.
- `PerMessage` (Telegram từ chối chính các id, không tạo gì): xóa `pending` của batch, gửi lại **từng unit một** (mỗi unit là một batch nhỏ có write-ahead và commit riêng). Unit đơn lẻ vẫn bị từ chối → cả unit `failed` với lý do đó.
- `FloodWait`, `PeerFlood`, `NoPermission`, `ForwardsRestricted`, lỗi RPC khác: Telegram đã từ chối nên không tạo gì → xóa `pending` của batch rồi dừng job (xem `01-kien-truc.md`).
- `Transient` (kết nối đứt sau khi gửi): kết quả **không rõ** → giữ nguyên `pending`, job `failed('transient')`; lần `run` sau reconcile.

## Resume

Khi `tgmirror run` khởi động lại job:

1. Nếu có hàng `pending` (lần trước chết giữa lời gọi) → **reconcile** (`engine/reconcile.py` quyết định, `engine/runner.py` đọc và ghi):
   - Đọc lại các tin nguồn đang `pending` (một lần quét ngắn từ `min(pending) - 1`) để biết loại media và cấu trúc album.
   - Đọc đuôi kênh đích: các tin không phải service có id > `max(dst_msg_id của các hàng done, options.dst_base_id)`.
   - Số tin, loại media từng tin và cấu trúc album (tin nào cùng album, đánh số theo lần xuất hiện đầu; `grouped_id` ở đích là mới nên không so trực tiếp) đều khớp với batch `pending` → coi là đã gửi, gán `dst_msg_id` theo thứ tự, đánh `done` và đẩy `cursor_src_id` (một transaction).
   - Không có tin mới ở đích → xóa `pending`, gửi lại batch.
   - Mơ hồ (đích có tin mới nhưng không khớp, hoặc một tin `pending` đã biến mất khỏi nguồn nên không so được) → cảnh báo người dùng, xóa `pending` và gửi lại (ưu tiên "không sót" hơn "không trùng"), nên đích có thể có vài tin trùng.
2. Lặp lại `iter_messages(src, min_id=cursor_src_id, reverse=True)` cùng filter đã lưu (`plan_read`, `03-filters.md`).
3. Bỏ qua bất kỳ unit nào đã có một `src_msg_id` `done` trong `msg_map` (an toàn khi `--refilter`; một album đã `done` một phần thì phần còn lại là việc của `retry`, không gửi lại cả album). Một batch mà mọi unit đều đã `done` chỉ đẩy `cursor_src_id`, không gọi Telegram, không chờ limiter.

Ngữ nghĩa: **at-least-once có reconcile**; trùng lặp chỉ có thể xảy ra ở cửa sổ crash rất hẹp và được phát hiện ở bước 1.

## Đổi filter (`tgmirror run --refilter`)

`Store.replace_filters(job_id, filters_json)` là **chỗ duy nhất `cursor_src_id` được lùi** (quy tắc 3 chỉ cấm ở mọi nơi khác): một transaction thay `filters_json`, đặt `cursor_src_id = 0` và xóa `stats.skipped_filter` (lần quét lại đếm lại). `done`/`failed` của `msg_map` giữ nguyên: bước 3 của Resume bỏ qua unit đã `done`, nên không sao chép hai lần; tin `failed` khớp filter mới thì được thử lại. Từ chối (`JobBusy`) khi job đang `running` với heartbeat còn mới. Tin khớp mới được thêm vào cuối kênh đích (thứ tự đích không còn theo thời gian).

## Delta (`tgmirror sync`)

`iter_messages(src, min_id=cursor_src_id, reverse=True)` cùng filter đã lưu. Nếu không có tin mới → thoát nhanh, mã 0. Job `done` chuyển lại `running` rồi `done`. Từ phase 2 `tgmirror run` đã làm đúng việc này với job `done` (cùng đường code); `sync` ở phase 5 là lệnh bọc và `sync --all`.

Phase sau (không thuộc v1): đồng bộ edit (so `edit_date` với `ts`) và delete (kiểm tra sự tồn tại ID định kỳ).

## Điều khiển

- CLI khác process ghi `jobs.control='pause'|'stop'` (chỉ khi job đang `running`; job không chạy thì lệnh báo "không đang chạy", mã 1, và không ghi gì); runner poll giữa các batch (rẻ, chỉ một `SELECT`) và mỗi `poll_interval` (2 giây) trong lúc ngủ của limiter, nên dừng được trong lúc chờ. Dừng xong: `status` là `paused`/`stopped` và `control` về `none`.
- Khi runner nhận Ctrl+C: đặt cờ nội bộ, hoàn tất batch hiện tại, commit, thoát (`stopped`, mã 130). Ctrl+C lần hai thoát ngay; job còn `running` với `pending` dở dang, lần `run` sau (`--force-takeover` nếu chưa quá 2 phút) reconcile.
- Một job chỉ có một runner: `claim` là một transaction `BEGIN IMMEDIATE` đặt `status='running'` và xóa `control`; runner ghi heartbeat `updated_at` mỗi 30 giây (task nền) và sau mỗi lần commit. Runner mới chỉ chiếm được nếu heartbeat cũ hơn 2 phút hoặc người dùng dùng `--force-takeover`.
- `run` khởi động được từ `created`, `paused`, `stopped`, `done`, `failed`, `running` (khóa hết hạn). Từ chối: `waiting_flood` trước `resume_at`; `failed(peer_flood)` trong 24 giờ kể từ `updated_at`.
- Trong một process, runner và task heartbeat dùng chung một connection SQLite; `Store` khóa mọi phương thức bằng một `asyncio.Lock` để task này không chạy lệnh giữa transaction của task kia.
- Một session Telethon chỉ nên được dùng bởi một process cùng lúc (SQLite session sẽ khóa), do đó một account chạy một job tại một thời điểm.
