# 04 — State, checkpoint, delta

Một file SQLite (`WAL` mode, `foreign_keys=ON`, `busy_timeout=5000` để `pause`/`stop` từ process khác không bị lỗi khóa). Phiên bản schema nằm ở `PRAGMA user_version`; `store/schema.sql` là phiên bản 1, các thay đổi sau thêm migration đánh số đăng ký ở `store/db.py::default_migrations` (mỗi migration chạy trong một transaction, có test nâng cấp từ phiên bản trước).

## Hai thứ, chỉ một thứ người dùng thấy

| | Là gì | Người dùng thấy |
|---|---|---|
| **Run** (bảng `runs`) | Một lần thực thi `clone`/`run`: lúc bắt đầu/kết thúc, trạng thái, bộ đếm của riêng lần đó, filter đã dùng, lỗi, cờ `control`, heartbeat. **Đây là nhật ký.** | Có: `tgmirror history` |
| **Mirror** (bảng `mirrors`) | Điểm kiểm tra của một cặp nguồn → đích: con trỏ `cursor_src_id`, filter đang nhớ, `dst_base_id`. `msg_map` treo vào nó | **Không**: không có id, tên, không bao giờ được liệt kê hay sửa |

Vì sao phải giữ trạng thái theo cặp: delta cần con trỏ, chống crash cần các hàng `msg_map` ghi trước (write-ahead) và `dst_base_id` để reconcile, và không thứ nào suy ra được từ nhật ký mà không quét lại cả hai kênh. Nên "không lưu job" nghĩa là không có thực thể để người dùng quản lý, không phải không có trạng thái. Mirror được tạo ở lần `clone` đầu tiên của cặp.

## Schema

```sql
CREATE TABLE mirrors (                      -- điểm kiểm tra của một cặp nguồn/đích
  id            INTEGER PRIMARY KEY,
  account       TEXT NOT NULL,              -- tên session
  src_id        INTEGER NOT NULL,           -- peer id nguồn
  src_title     TEXT,
  src_kind      TEXT NOT NULL,              -- broadcast|supergroup|forum|group
  dst_id        INTEGER NOT NULL,
  dst_title     TEXT,
  mode          TEXT NOT NULL,              -- auto|copy|reupload (của lần tạo)
  filters_json  TEXT NOT NULL,              -- filter đang nhớ ({} = không lọc)
  options_json  TEXT NOT NULL,              -- dst_base_id (xem dưới); key lạ bị bỏ qua
  cursor_src_id INTEGER NOT NULL DEFAULT 0, -- id nguồn lớn nhất đã xử lý xong (done/failed/filter-skip)
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX mirrors_pair ON mirrors(src_id, dst_id);

CREATE TABLE runs (                         -- nhật ký: một dòng cho mỗi lần chạy
  id           INTEGER PRIMARY KEY,
  mirror_id    INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,
  mode         TEXT NOT NULL,
  filters_json TEXT NOT NULL,               -- filter lần này dùng
  options_json TEXT NOT NULL,               -- batch_size, dst_base_id, pushdown
  status       TEXT NOT NULL,               -- running|paused|stopped|waiting_flood|done|failed
  control      TEXT NOT NULL DEFAULT 'none',-- none|pause|stop  (do CLI đặt, runner đọc)
  cursor_from  INTEGER NOT NULL DEFAULT 0,  -- nơi lần này bắt đầu đọc nguồn
  cursor_to    INTEGER NOT NULL DEFAULT 0,  -- lần này đi tới đâu
  resume_at    TEXT,                        -- khi waiting_flood
  fail_reason  TEXT,                        -- lỗi, hoặc daily_cap / interrupted / taken_over
  stats_json   TEXT NOT NULL DEFAULT '{}',  -- done, failed, skipped_filter, already_done, ... của RIÊNG lần này
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  updated_at   TEXT NOT NULL                -- đồng thời là heartbeat khi đang chạy
);

CREATE TABLE msg_map (
  mirror_id    INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,
  src_msg_id   INTEGER NOT NULL,
  dst_msg_id   INTEGER,                     -- NULL nếu pending/failed
  grouped_id   INTEGER,
  src_topic_id INTEGER,                     -- NULL nếu nguồn không phải forum
  status       TEXT NOT NULL,               -- pending|done|failed|skipped
  reason       TEXT,                        -- lý do lỗi/bỏ qua; hàng `pending` từng lỗi giữ lý do cũ (xem "Hủy pending")
  batch_id     INTEGER,
  run_id       INTEGER,                     -- lần chạy đã kết thúc hàng này gần nhất (để `history n`/`retry n` liệt kê tin lỗi)
  ts           TEXT NOT NULL,
  PRIMARY KEY (mirror_id, src_msg_id)
);
CREATE INDEX msg_map_status ON msg_map(mirror_id, status);
CREATE INDEX msg_map_run ON msg_map(run_id, status);

CREATE TABLE topic_map (                    -- chỉ cặp forum
  mirror_id    INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,
  src_topic_id INTEGER NOT NULL,            -- id tin gốc của topic (General = 1)
  dst_topic_id INTEGER NOT NULL,
  title        TEXT,
  PRIMARY KEY (mirror_id, src_topic_id)
);

CREATE TABLE flood_log (
  id        INTEGER PRIMARY KEY,
  run_id    INTEGER,
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

**Không có migration từ mô hình job cũ** (2026-09-20): dự án chưa phát hành nên schema `runs` + `mirrors` được gộp thẳng vào `schema.sql` (phiên bản 1) thay cho bảng `jobs`; cơ sở dữ liệu cũ được xóa (`tgmirror.db`), không chuyển đổi. Hệ quả: một cặp đã clone bằng bản cũ mà DB bị xóa thì lần `clone` đầu tiên coi như cặp mới, nên nếu kênh đích còn các bản sao cũ, tin sẽ bị sao chép trùng; dùng đích mới hoặc dọn đích trước. Từ khi phát hành, mọi thay đổi schema phải là migration đánh số có test nâng cấp.

`options_json.dst_base_id`: id tin mới nhất của kênh đích lúc clone cặp lần đầu (`gateway.last_message_id`, chỉ gọi khi cặp mới). Mirror lưu một lần; mỗi run chép giá trị vào `runs.options_json`. Reconcile chỉ đọc đích sau `max(dst_msg_id của các hàng done, dst_base_id)`.

`options_json.src_last_id` (ghi ở `runs`, không ở `mirrors`; 0 = không biết): id tin mới nhất của nguồn lúc lần chạy bắt đầu (`begin_run` đọc bằng `last_message_id`, một request ở bước chuẩn bị), là tổng mà `status` đo tiến độ và ETA (`engine/status.py`); tin đăng thêm trong lúc chạy không nằm trong đó. `options_json.retry_of` (ghi ở `runs`): id lần chạy mà lần này gửi lại các tin `failed` (xem "Retry"); `None` là lần chạy thường. Hai khóa này chỉ thuộc về từng lần chạy: `RunOptions.for_pair` bỏ chúng khi ghi `mirrors.options_json`, và `tgmirror run` không thừa hưởng `retry_of` của lần chạy trước.

`options_json` cho chiến lược B (phase 6, ghi ở `runs` và nhớ ở mirror như `batch_size`; `tgmirror run`/`retry` dùng lại giá trị của lần trước): `caption` (`keep|strip-links|append|none`), `caption_text`, `reset_polls`, `ignore_unsupported`, `placeholder`, và `protected_ack` (user đã xác nhận D3 cho nguồn cấm lưu nội dung, xem `01-kien-truc.md`, "Kiểm lại nguồn khi chạy lại"). Lần chạy cũ không có các khóa này được đọc như mặc định (`keep`, tất cả cờ tắt).

`options_json.pushdown` (mặc định `true`, ghi ở `runs`): `false` thì đọc mọi tin sau cursor và chỉ lọc ở máy (`clone --no-pushdown`, xem `03-filters.md`). `batch_size` cũng là của từng lần chạy (`tgmirror run` dùng lại giá trị của lần trước).

`mirrors.filters_json` / `runs.filters_json`: JSON chuẩn hóa của `FilterSpec` (`{}` = không lọc), nạp lại bằng `FilterSpec.from_json` mỗi lần chạy; hỏng thì lần chạy `failed` với lý do `FilterError: ...`. Mirror giữ filter đang nhớ, run ghi filter lần đó dùng.

Tin bị **filter loại** không được ghi vào `msg_map` (hàng triệu hàng vô ích); chỉ tăng bộ đếm `stats.skipped_filter` của lần chạy (đếm theo tin, album tính đủ mọi tin) và đẩy `cursor_src_id` tiến lên. Số đếm và con trỏ đi cùng transaction với batch mà chúng được cộng vào (`commit_batch(extra_stats=...)`), hoặc riêng một transaction `advance_cursor(extra_stats=...)` khi batch không có gì để gửi (chỉ có tin bị loại, hoặc mọi unit đã `done`); không bao giờ vượt qua một unit chưa xử lý. Khi Telegram thu hẹp theo nội dung (`media`/`search`) thì tin bị loại ở server không được thấy nên không được đếm và con trỏ chỉ tới unit khớp cuối cùng; lần chạy sau đọc lại từ đó (rẻ, vì vẫn thu hẹp).

Tin **không hỗ trợ** (game, invoice, quiz chưa trả lời, poll khi thiếu `--reset-polls`; xem `01-kien-truc.md`) khác filter: người dùng muốn clone nhưng không thể, nên có ghi `msg_map` với `status='skipped'` + `reason='unsupported:<loại>'` và tăng `skipped_unsupported`. `retry` chỉ thử lại `failed`, không thử `skipped`. Hàng `skipped` của tin không hỗ trợ có `dst_msg_id` nếu đã đăng tin text thay thế (`--placeholder`); con trỏ đi qua nó như một tin đã xử lý xong, và bộ đếm là `stats.skipped_unsupported` của lần chạy (`commit_batch` đếm mọi kết quả `skipped`). Tin bị bỏ mà không cần gọi Telegram (poll không có `--reset-polls`, hay `--ignore-unsupported`) vẫn đi qua write-ahead rồi commit trong hai transaction liền nhau (`begin_batch` + `commit_batch`, không có lời gọi Telegram ở giữa): một crash ở giữa chỉ để lại hàng `pending` mà reconcile thấy không có gì ở đích và xóa. Cũng `skipped` là tin mà `retry` thấy đã bị xóa ở nguồn: `reason='gone_from_source'`, tăng `stats.gone` của lần retry (`Store.mark_gone`).

`topic_map` được ghi cùng transaction với việc tạo topic đích (tạo topic xong phải lưu ngay, kẻo resume tạo trùng).

## Quy tắc transaction

Các phương thức của `Store` nhận `run_id` (lần chạy đang làm việc) và tự tìm mirror của nó; thao tác nào chạm `msg_map`/con trỏ thì tác động lên mirror, còn bộ đếm và trạng thái thì lên run.

1. **Trước** khi gọi Telegram cho một batch: `INSERT msg_map(... status='pending', batch_id, run_id)` (write-ahead), commit.
2. **Sau** khi Telegram trả kết quả: trong **một transaction** — cập nhật các hàng thành `done` (kèm `dst_msg_id`) hoặc `failed` (kèm `reason`), cập nhật `mirrors.cursor_src_id`, `runs.cursor_to`, `runs.stats_json`, `updated_at`, `limiter_state` (`commit_batch(limiter=...)`: `sent_today` đi cùng các tin nó đếm; sau một flood `limiter_state` được lưu ngay bằng `save_limiter_state`; hàng theo `mirrors.account`, dùng chung cho mọi cặp của account). Batch được reconcile xác nhận (`confirm_pending`) không cộng vào `sent_today`: đếm hụt tối đa một batch sau một lần crash, chấp nhận được vì cap là ngân sách mềm.
3. `cursor_src_id` chỉ tiến (`MAX(cursor_src_id, ?)` trong SQL), và chỉ tiến tới id lớn nhất của batch đã kết thúc hoàn toàn (không còn `pending`; `commit_batch` từ chối nếu còn hàng `pending` nào khác). Ngoại lệ **duy nhất**, cả hai nằm trong `Store.start_run`: đặt lại về 0 khi filter đổi (xem "Filter khi chạy lại") và khi làm lại từ đầu (xem "Làm lại từ đầu").

Kết quả của một lời gọi copy:

- Lời gọi trả về bình thường: từng id có `dst_id` → `done`; id `None` (Telegram không tạo tin, thường vì đã xóa ở nguồn) → `failed` với `reason='not_copied'`.
- `PerMessage` (Telegram từ chối chính các id, không tạo gì): xóa `pending` của batch, gửi lại **từng unit một** (mỗi unit là một batch nhỏ có write-ahead và commit riêng). Unit đơn lẻ vẫn bị từ chối → cả unit `failed` với lý do đó.
- `FloodWait` ngắn hơn `max_auto_wait`: chờ rồi gửi lại **đúng lời gọi đó**; `pending` giữ nguyên trong lúc chờ (Telegram từ chối nên chưa tạo gì; nếu process chết lúc đó thì reconcile lần sau thấy đích không có gì và gửi lại). `FloodWait` quá dài hoặc quá nhiều lần, `PeerFlood`, `NoPermission`, `ForwardsRestricted`, lỗi RPC khác, hoặc stop/Ctrl+C trong lúc chờ: Telegram chưa tạo gì → xóa `pending` của batch rồi kết thúc lần chạy (xem `01-kien-truc.md`, `05-chong-flood.md`).
- **Hủy `pending`** (mọi chỗ trên nói "xóa `pending`": `discard_batch`, `discard_pending`): hàng `pending` chưa từng lỗi thì bị xóa, vì con trỏ sẽ đưa tin đó trở lại. Hàng từng `failed` (write-ahead giữ lại `reason` và `run_id` cũ của nó, xem `msgmap.insert_pending`) thì quay về `failed` với lý do và lần chạy cũ, **không** bị xóa: nó nằm dưới con trỏ, không ai đọc lại, nên xóa là mất dấu hẳn một tin chưa được sao chép. Đây là chỗ `retry` khác lần chạy thường (lỗi được phát hiện khi viết test kill giữa chừng cho retry). `run_id` chỉ chuyển sang lần chạy mới khi batch kết thúc (`finish_batch`) hoặc được reconcile xác nhận.
- Chiến lược B: mỗi unit là một batch (`Batch.strategy = REUPLOAD`), nên `pending` của nó là đúng một unit và reconcile so được số tin, loại media và cấu trúc album như thường. `prepare` (đọc và tải) xảy ra **trước** write-ahead nên lỗi ở đó (`PerMessage`: tin đã xóa, loại không dựng lại được) không để lại `pending`: unit được ghi `failed` ngay (`begin_batch` + `commit_batch`). Sau khi upload thành công mà chưa commit thì kết quả như `Transient`: reconcile lần sau tìm bản sao ở đích. `FloodWait` khi đang gửi lặp lại đúng lời gọi `send_prepared` với cùng các file đã tải (không tải lại).
- Daily cap (`DailyCapReached`) xảy ra **trước** write-ahead (ở `limiter.acquire`), nên không có gì để xóa: lần chạy `waiting_flood`, `fail_reason='daily_cap'`, `resume_at` = 00:00 ngày kế (giờ máy).
- `Transient` (kết nối đứt sau khi gửi): kết quả **không rõ** → giữ nguyên `pending`, lần chạy `failed('transient')`; lần chạy sau reconcile.

## Resume và delta

Cả hai là cùng một đường code: `tgmirror clone` (cùng cặp) hoặc `tgmirror run` mở một run mới trên mirror có sẵn (`Store.start_run`), rồi:

1. Nếu có hàng `pending` (lần trước chết giữa lời gọi) → **reconcile** (`engine/reconcile.py` quyết định, `engine/runner.py` đọc và ghi):
   - Đọc lại các tin nguồn đang `pending` (một lần quét ngắn từ `min(pending) - 1`) để biết loại media và cấu trúc album.
   - Đọc đuôi kênh đích: các tin không phải service có id > `max(dst_msg_id của các hàng done, options.dst_base_id)`.
   - Số tin, loại media từng tin và cấu trúc album (tin nào cùng album, đánh số theo lần xuất hiện đầu; `grouped_id` ở đích là mới nên không so trực tiếp) đều khớp với batch `pending` → coi là đã gửi, gán `dst_msg_id` theo thứ tự, đánh `done` và đẩy `cursor_src_id` (một transaction).
   - Không có tin mới ở đích → xóa `pending`, gửi lại batch.
   - Mơ hồ (đích có tin mới nhưng không khớp, hoặc một tin `pending` đã biến mất khỏi nguồn nên không so được) → cảnh báo người dùng, xóa `pending` và gửi lại (ưu tiên "không sót" hơn "không trùng"), nên đích có thể có vài tin trùng.
2. Lặp lại `iter_messages(src, min_id=cursor_from, reverse=True)` (`cursor_from` = con trỏ của mirror lúc bắt đầu, hoặc 0 khi filter đổi) cùng filter của run (`plan_read`, `03-filters.md`). Không có tin mới → kết thúc nhanh, `done`, mã 0.
3. Bỏ qua bất kỳ unit nào đã có một `src_msg_id` `done` trong `msg_map` (an toàn khi filter đổi và quét lại; một album đã `done` một phần thì phần còn lại là việc của `retry`, không gửi lại cả album). Một batch mà mọi unit đều đã `done` chỉ đẩy `cursor_src_id`, không gọi Telegram, không chờ limiter.

Ngữ nghĩa: **at-least-once có reconcile**; trùng lặp chỉ có thể xảy ra ở cửa sổ crash rất hẹp và được phát hiện ở bước 1. Kết quả của mỗi lần chạy nằm ở dòng nhật ký của nó (`history`), còn con trỏ và `msg_map` là của cả cặp, nên số đếm của một lần chạy chỉ gồm tin lần đó làm.

## Filter khi chạy lại

`start_run` nhận `filters_json` hoặc `None`:

- `None` (không có cờ lọc): dùng filter mirror đang nhớ, con trỏ giữ nguyên → delta. Cặp mới: không lọc.
- Giống hệt filter đang nhớ: như `None`.
- Khác: **một transaction** thay `mirrors.filters_json`, đặt `cursor_src_id = 0`, rồi mở run với `cursor_from = 0`. `done`/`failed` của `msg_map` giữ nguyên: bước 3 của Resume bỏ qua unit đã `done`, nên không sao chép hai lần; tin `failed` khớp filter mới thì được thử lại. Bộ đếm `skipped_filter` là của từng run nên tự bắt đầu lại. Tin khớp mới được thêm vào cuối kênh đích (thứ tự đích không còn theo thời gian).

Từ chối (`RunBusy`) khi cặp đang có run `running`/`paused` với heartbeat còn mới, trước khi đổi gì.

## Làm lại từ đầu

`start_run(spec, fresh=True)` (`clone --fresh`, `run --fresh`): trong **cùng một transaction** và **sau** khi kiểm tra cặp có bị giữ (`RunBusy` thì chưa xóa gì), đếm rồi xóa mọi hàng `msg_map` của mirror (mọi trạng thái, kể cả `pending`), đặt `cursor_src_id = 0` và ghi `dst_base_id` mới (tin mới nhất của đích lúc đó, do `begin_run` đọc bằng `last_message_id` — với cặp đã có mirror bình thường không đọc lại) vào `mirrors.options_json`. Lần chạy mới bắt đầu ở `cursor_from = 0`. Filter xử lý như thường (không đưa thì giữ filter đang nhớ). Cặp chưa có mirror thì `fresh` không có gì để quên: coi như lần chạy đầu. `StartedRun.forgot` là số tin `done` đã quên (`None` nếu không phải làm lại). `Store.count_copied(src, dst)` cho CLI đếm trước để hỏi. Các run cũ và bộ đếm của chúng giữ nguyên; danh sách tin lỗi của run cũ (`history n`) mất theo `msg_map`. Reconcile của lần chạy làm lại chỉ đọc đích sau `dst_base_id` mới nên không quét phần đã có từ trước.

## Retry

`tgmirror retry [n]` (`RunRequest.retry_of = n`) mở một run mới trên mirror của cặp (`start_run` như mọi lần chạy: cùng kiểm tra `RunBusy`/`check_runnable`, filter và con trỏ giữ nguyên, `cursor_from` = con trỏ hiện tại) với `options.retry_of = n`. Khác lần chạy thường ở nguồn `Unit` của runner (`Runner._failed_units`), còn lại (reconcile trước, gate, `pace`, write-ahead, `commit_batch`, FloodGuard) là cùng một đường:

1. Sau reconcile, chụp danh sách id `failed` của lần chạy `n` (`run_failures(n)`), tăng dần.
2. Đọc theo id, lô 100 (`planner.failed_units`); id không còn ở nguồn → `Store.mark_gone` (`skipped`, không gửi), một transaction cho mỗi lô; idempotent nên crash giữa chừng chỉ làm lô đó được xử lý lại.
3. Gom thành batch như thường; mỗi batch: write-ahead (hàng `failed` → `pending`, giữ lý do/run cũ), `copy_messages`, rồi `commit_batch`: `done` (`dst_msg_id` mới) hoặc `failed` (lý do mới), `run_id` = lần retry. `cursor_src_id` không đổi (`MAX` với id thấp hơn) và `runs.cursor_to` cũng vậy.

Crash-safety: kill trước copy → reconcile lần sau thấy đích chưa có tin → hủy `pending` → hàng quay về `failed` (xem "Hủy `pending`") và `retry n` gửi lại; kill sau copy → reconcile tìm thấy bản sao ở đích, ghi `done`. Bị FloodWait quá dài/`PeerFlood`/stop: batch bị hủy, hàng vẫn `failed` của lần `n`.

Bộ đếm là của lần retry (`done`: gửi lại được, `failed`: vẫn lỗi, `gone`: đã xóa ở nguồn). Tiến độ của `status` cho retry là `(done + failed + gone) / (đó + số hàng `failed` còn lại của lần `n`)`: hàng đã xử lý chuyển `run_id` sang lần retry nên tự rời khỏi danh sách của `n`.

## Tổng và tiến độ của lần chạy

`options_json` của lần chạy thêm khóa `src_protected` (nguồn cấm lưu nội dung theo lần đọc lại của `begin_run`; khi đúng, không gửi bằng mã file, D3) và khóa `total_items` (số tin lần chạy phải xem xét, cận trên, `0` = chưa biết; do `Runner._analyze` ghi bằng `Store.set_total` sau reconcile) và `stats_json` thêm `already_done` (tin vượt qua vì `msg_map` đã có, khi đọc lại từ đầu sau khi đổi filter). Cả hai là của riêng lần chạy: `for_pair` bỏ `total_items`, con trỏ và `msg_map` không đổi, nên **không cần migration**. `Run.handled` là tổng các bộ đếm mà tiến độ dựa vào (xem `01-kien-truc.md`, "Analyze và tiến độ"). Tiến độ truyền file (byte của file đang tải) không lưu: chỉ có trong tiến trình đang chạy.

## Điều khiển

Một lần chạy là một tiến trình foreground; điều khiển đến từ ba nguồn cùng đi vào `RunControl`/cờ `control`:

- **Cùng terminal**: phím `p`/`r`/`q` và Ctrl+C (`cli/keys.py`, `cli/interrupt.py`) → `RunControl` (an toàn giữa các luồng). Ctrl+C lần một = stop (xong batch hiện tại, commit, thoát mã 130, run `stopped`); lần hai thoát ngay, run còn `running` với `pending` dở dang, lần chạy sau (`--force-takeover` nếu chưa quá 2 phút) reconcile.
- **Terminal khác**: `tgmirror pause|stop|run` ghi `runs.control='pause'|'stop'|'none'` (chỉ khi run đang `running`/`paused`; không có run đang chạy thì báo "không có clone nào đang chạy", mã 1, không ghi gì). `tgmirror run` mà cặp đang `paused` ở terminal khác thì chỉ xóa cờ (cho chạy tiếp ở đó), không mở run thứ hai.
- Runner poll giữa các batch (rẻ, chỉ một `SELECT`) và mỗi `poll_interval` (2 giây) trong lúc ngủ (giãn cách của limiter và chờ FloodWait; ngủ ném `Interrupted` khi có **stop**), nên dừng được trong lúc chờ. `stop` → run `stopped`, `control` về `none`.
- **`pause` giữ tại chỗ** (`Runner._hold`): xong batch hiện tại, run chuyển `paused` (tiến trình sống, heartbeat vẫn ghi mỗi 30 giây, không gửi gì) và poll mỗi `poll_interval` cho tới khi hết cờ pause (resume) hoặc có stop. Pause không cắt ngang lúc ngủ/chờ flood; nó có hiệu lực ở ranh giới batch kế tiếp.

Sở hữu:

- Một cặp chỉ có một run sống: `start_run` (`BEGIN IMMEDIATE`) từ chối (`RunBusy`) khi run `running`/`paused` của mirror có heartbeat còn mới (dưới 2 phút) trừ khi `--force-takeover` (khi đó run cũ ghi `failed('taken_over')`). Run có heartbeat cũ hơn 2 phút được coi là đã chết: ghi `failed('interrupted')` (kết thúc lúc nó còn tín hiệu cuối) rồi mở run mới.
- Runner ghi heartbeat `updated_at` mỗi 30 giây (task nền) và sau mỗi lần commit.
- Từ chối chạy: `waiting_flood` trước `resume_at` (flood, hoặc `fail_reason='daily_cap'`); `failed(peer_flood)` trong 24 giờ kể từ khi kết thúc (`engine/runs.py::check_runnable`, đọc run gần nhất của cặp; kiểm trước khi kết nối Telegram). Run mới không mang `fail_reason`/`resume_at` của run cũ.
- Trong một process, runner và task heartbeat dùng chung một connection SQLite; `Store` khóa mọi phương thức bằng một `asyncio.Lock` để task này không chạy lệnh giữa transaction của task kia.
- Một session Telethon chỉ nên được dùng bởi một process cùng lúc (SQLite session sẽ khóa), do đó một account chạy một clone tại một thời điểm (`Store.active_run` cho `pause`/`stop`/`run` tìm ra nó).

Phase sau (không thuộc v1): đồng bộ edit (so `edit_date` với `ts`) và delete (kiểm tra sự tồn tại ID định kỳ).
