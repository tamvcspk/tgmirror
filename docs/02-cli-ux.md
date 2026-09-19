# 02 — CLI & UX

Lệnh chính: `tgmirror` (entry point của package `tgmirror`).

## Lệnh

| Lệnh | Mô tả |
|---|---|
| `tgmirror --version` | In phiên bản rồi thoát |
| `tgmirror login` | Nhập `api_id`/`api_hash` (lưu config), đăng nhập (phone, code, 2FA), tạo session. Cần terminal (mã gửi qua Telegram); `--phone` điền sẵn số. Nếu đã đăng nhập thì chỉ báo lại, không cần terminal |
| `tgmirror logout` / `tgmirror whoami` | Xóa session / xem account hiện tại |
| `tgmirror channels` | Liệt kê kênh/group/forum đã join (cột: loại, tên + @username, id, số thành viên, noforwards, quyền post). `--search TEXT` lọc theo tên/username, `--writable` chỉ giữ chỗ user là admin và đăng được, `--json` |
| `tgmirror new` | Wizard tạo job (xem dưới). Hỗ trợ đủ flag để chạy không tương tác. **Phase 2**: bước 1–2 (chọn nguồn, chọn/tạo đích) rồi lưu job và hỏi có chạy ngay không; chưa có filter/tùy chọn (phase 3+). Cờ: `--name`, `--mode auto\|copy` (`reupload` từ phase 6, hiện báo mã 2), `--batch-size 1..100` (mặc định `[limits] batch_size`), `--run/--no-run` (mặc định: hỏi trên terminal, không chạy nếu không có terminal hoặc có `--yes`) |
| `tgmirror run <job>` | Chạy hoặc tiếp tục job (foreground). `<job>` là id hoặc tên chính xác. `--force-takeover`: chạy dù job có vẻ đang do process khác giữ (chỉ khi chắc nó đã chết). Từ chối job `waiting_flood` trước `resume_at` và job `failed(peer_flood)` trong 24 giờ (mã 3). Job `done` chạy lại sẽ nhặt tin mới |
| `tgmirror pause <job>` / `tgmirror stop <job>` | Đặt cờ `control` trong DB; runner đang chạy sẽ dừng sau batch hiện tại (`paused`/`stopped`). Job không chạy thì báo "không đang chạy", mã 1. Tiếp tục bằng `tgmirror run` |
| `tgmirror sync <job>` | Delta clone: chỉ lấy tin mới hơn `cursor` |
| `tgmirror sync --all` | Sync mọi job `done`/`paused`, chạy tay (không có chế độ chạy nền) |
| `tgmirror status [job]` | Tiến độ, tốc độ, ETA, số lỗi, lần flood gần nhất |
| `tgmirror jobs` | Danh sách job |
| `tgmirror retry <job>` | Thử lại các tin `failed` |
| `tgmirror rm <job>` | Xóa job (không xóa tin đã clone trong kênh đích) |
| `tgmirror config [get\|set]` | Xem/sửa config (`[limits]`, đường dẫn, ...) |
| `tgmirror doctor` | Kiểm tra: session hợp lệ, cryptg đã cài, quyền kênh đích, cảnh báo an toàn |

Tùy chọn chung: `--version`, `--debug` (hiện traceback thay vì một câu lỗi).

Mã thoát: `0` ok, `1` lỗi chung, `2` dùng sai, `3` job dừng vì flood/peer_flood (hoặc `run` bị từ chối vì phải chờ), `4` thiếu quyền (kể cả nguồn cấm forward), `130` Ctrl+C (đã lưu, job `stopped`).

## Wizard `tgmirror new`

```
1. Chọn nguồn           ← danh sách dialogs: channel/supergroup/forum/group (gõ để lọc); cảnh báo nếu noforwards
2. Chọn đích            ← (a) có sẵn, cùng loại với nguồn, có quyền post
                           (b) Tạo mới: nhập tên [+ about] [+ copy avatar]; nguồn forum → tự tạo topic tương ứng
3. Chọn filter          ← checkbox: media types / hashtag / từ khóa / khoảng ngày / dung lượng
                           hoặc "Nạp từ file YAML"
4. Tùy chọn             ← mode (auto/copy/reupload), caption handling, batch_size
5. Xem trước            ← số tin ước lượng, số tin khớp filter (mẫu 100 tin đầu), ETA theo limiter
6. Xác nhận             ← lưu job, hỏi có chạy ngay không
```

Tương đương không tương tác:

```bash
tgmirror new --src "@ten_kenh" --dst-new "Ten kenh moi" \
         --media video,photo --hashtag "#xxx" --since 2024-01-01 \
         --caption keep --yes
tgmirror new --src -1001234567890 --dst -1009876543210 --filter-file filters.yaml --yes
```

Wizard chỉ thu thập giá trị rồi gọi cùng một hàm `create_job(spec)` như flag. Không được có logic chỉ tồn tại ở một nhánh.

Từ phase 2 cả hai nhánh đi qua `engine/endpoints.py` (`find_channel` → `plan_endpoints` → `materialize`) rồi `engine/jobs.py` (`create_job`, kiểm `--mode`, mỗi cặp nguồn/đích chỉ một job: cặp đã có job thì mã 2 kèm id job) và `run`. Quy tắc kiểm tra:

- `--src`/`--dst` nhận `@username`, id (`-100...` hoặc số trần) hoặc **tên chính xác** (không phân biệt hoa thường, phải duy nhất; không đoán theo một phần tên); một từ không khớp tên nào thì thử làm username không có `@`. Chỉ khớp trong các chat đã join. **PowerShell**: phải viết `"@ten_kenh"` trong dấu nháy, vì `@ten_kenh` trần bị shell hiểu là splatting và biến mất (khi đó `--src` nuốt cờ kế tiếp và lệnh báo "unexpected extra argument").
- Nguồn bật `noforwards` và user không phải admin → từ chối (D3), thoát mã 4; là admin → cảnh báo (tắt tạm hoặc `--mode reupload` từ phase 6).
- Đích có sẵn: cùng loại với nguồn, user là admin và đăng được, khác nguồn (mã 4 nếu thiếu quyền, mã 2 nếu sai loại/trùng nguồn). Wizard chỉ liệt kê các đích thỏa điều kiện.
- Đích mới (`--dst-new`, tên 1–128 ký tự, `--about` tối đa 255): hiện chỉ tạo được kênh broadcast, nên nguồn supergroup/forum/group phải chọn đích có sẵn cho đến phase 8. Tạo kênh là thao tác ghi: hỏi xác nhận, hoặc `--yes` khi không có terminal (thiếu `--yes` thì thoát mã 2).
- Không có terminal mà thiếu `--src`/`--dst`/`--dst-new` → mã 2, nêu tên cờ thiếu.

## Tin đặc thù (chỉ áp dụng khi job dùng chiến lược B)

Chi tiết và lý do ở `01-kien-truc.md` (mục "Tin đặc thù"). Cờ của `new`/`run`:

```bash
tgmirror new --src ... --dst ... --mode reupload \
             --ignore-unsupported \   # bỏ qua game/invoice (và quiz chưa trả lời), chỉ in warning
             --reset-polls \          # tạo lại poll/quiz dù mất số vote
             --placeholder            # thay mỗi tin bị bỏ bằng một tin text "không thể sao chép"
```

- Không có `--ignore-unsupported`: bước xem trước của wizard báo số tin không hỗ trợ và hỏi xác nhận; không tương tác thì lỗi mã `2`. Job không bao giờ bỏ tin "âm thầm".
- Không có `--reset-polls`: poll/quiz bị bỏ (kèm warning) thay vì tạo lại với 0 vote, vì đó là thay đổi dữ liệu người dùng nên phải chọn rõ.
- `--placeholder` ngầm bao gồm `--ignore-unsupported`. Không áp dụng cho poll bị bỏ do thiếu `--reset-polls` (đó là lựa chọn của người dùng, không phải giới hạn kỹ thuật).
- Location, contact luôn được giữ. Với chiến lược A không có cờ nào ở đây (Telegram tự giữ loại tin).

## Điều khiển khi đang chạy

**Phase 2** chỉ có dòng chữ thường (không ANSI, dùng được khi chuyển hướng): `Job 3: 4,180 messages copied, 3 failed (source up to id …)` tối đa một dòng mỗi 5 giây, cộng các thông báo (reconcile, flood) và một dòng kết quả. Ctrl+C: lần một hoàn tất batch hiện tại, lưu, thoát mã 130; lần hai thoát ngay. `pause`/`stop` từ terminal khác dùng được. Bản TUI dưới đây (phím `p`/`q`, thanh tiến độ, ETA) là phase 7.

Foreground TUI (Rich Live, phase 7):

```
tgmirror ▸ job 7  "Kenh A → Kenh A (copy)"        mode=copy   delay=2.4s
██████████░░░░░░░░░░  4,210 / 9,800   43%   2.1 msg/s   ETA 44m
done 4,180 · failed 3 · skipped(filter) 27,911 · flood 2 (last 38s ago)
[p] pause   [q] quit (save & exit)   [+/-] no manual speed override
```

- `p`: pause (hoàn tất batch hiện tại, lưu, chờ; bấm `p` để tiếp tục).
- `q` hoặc Ctrl+C: hoàn tất batch hiện tại, lưu, thoát. Ctrl+C lần hai: thoát ngay (batch dở dang được xử lý theo quy tắc reconcile ở `04-state-checkpoint.md`).
- Không cho phép người dùng ép tốc độ thấp hơn `min_delay` (bảo vệ tài khoản); có thể tăng delay.

## Caption handling (`--caption`)

`keep` (mặc định) · `strip-links` (bỏ link/mention trỏ về kênh nguồn) · `append "<text>"` · `none`.
Chiến lược A (copy) chỉ hỗ trợ `keep` (và `none` qua `drop_media_captions`, Telethon >= 1.45 có tham số này); các chế độ khác buộc dùng chiến lược B cho tin có caption → cảnh báo người dùng về tốc độ.

## Config

Đường dẫn dữ liệu qua `platformdirs`:

```
<data_dir>/tgmirror.db              state
<data_dir>/sessions/<name>.session  Telethon session (bí mật, chmod 600 nếu được)
<config_dir>/config.toml            api_id, api_hash, [limits]
```

`api_id`/`api_hash` có thể lấy từ biến môi trường `TGMIRROR_API_ID` / `TGMIRROR_API_HASH` (ưu tiên hơn file). `tgmirror login` hỏi và ghi hai khóa này lên đầu `config.toml` (giữ nguyên phần còn lại và chú thích của file, không bao giờ in `api_hash`).

`TGMIRROR_LANG=en` đổi lời nhắc/thông báo sang tiếng Anh (mặc định tiếng Việt). Đầu ra luôn là UTF-8, kể cả khi bị chuyển hướng trên Windows.

```toml
[limits]            # xem 05-chong-flood.md
batch_size = 20
min_delay = 2.0
max_delay = 60.0
jitter = 0.3
long_pause_every = 200
long_pause_range = [30, 90]
daily_cap = 5000
max_auto_wait = 900
upload_concurrency = 1
```
