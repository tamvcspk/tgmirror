# 02 — CLI & UX

Lệnh chính: `tgmirror` (entry point của package `tgmirror`).

## Lệnh

| Lệnh | Mô tả |
|---|---|
| `tgmirror --version` | In phiên bản rồi thoát |
| `tgmirror login` | Nhập `api_id`/`api_hash` (lưu config), đăng nhập (phone, code, 2FA), tạo session. Cần terminal (mã gửi qua Telegram); `--phone` điền sẵn số. Nếu đã đăng nhập thì chỉ báo lại, không cần terminal |
| `tgmirror logout` / `tgmirror whoami` | Xóa session / xem account hiện tại |
| `tgmirror channels` | Liệt kê kênh/group/forum đã join (cột: loại, tên + @username, id, số thành viên, noforwards, quyền post). `--search TEXT` lọc theo tên/username, `--writable` chỉ giữ chỗ user là admin và đăng được, `--json` |
| `tgmirror clone` | Sao chép một nguồn vào một đích **ngay bây giờ**, trong terminal này (foreground): không chạy nền, không lên lịch; Ctrl+C dừng (tiến độ đã lưu). Wizard (bước 1–3 chọn nguồn/đích/filter, bước 5 xem trước, một câu xác nhận) hoặc cờ không tương tác. Chạy lại cho cùng cặp nguồn/đích thì chỉ lấy tin mới hơn con trỏ (delta), dùng lại filter của lần trước; đưa filter khác thì quét lại từ đầu (bỏ qua tin đã sao chép), `--no-filter` bỏ filter cũ (xem `03-filters.md`). Cờ: `--src`, `--dst` \| `--dst-new` (+ `--about`), cờ lọc (`--media`, `--hashtag`, `--contains`, `--regex`, `--exclude-regex`, `--exclude-media`, `--since`, `--until`, `--min-size`, `--max-size`, `--album`, `--filter-file`), `--no-filter`, `--pushdown/--no-pushdown`, `--preview/--no-preview`, `--mode auto\|copy` (`reupload` từ phase 6, hiện báo mã 2), `--batch-size 1..100` (mặc định `[limits] batch_size`), `--wait`, `--force-takeover`, `--yes` (bỏ câu xác nhận). Chưa có bước 4 (tùy chọn) và `--caption` |
| `tgmirror run [n]` | Chạy lại cặp nguồn/đích của lần chạy `n` (mặc định lần gần nhất; số lấy từ `tgmirror history`) với filter và tùy chọn của lần đó: chỉ lấy tin mới hơn, hoặc tiếp tục chỗ Ctrl+C/lỗi đã dừng. Nếu lần chạy đó đang **tạm dừng ở terminal khác** thì cho nó chạy tiếp ở đó thay vì mở lần thứ hai. `--force-takeover`: chạy dù có vẻ tiến trình khác đang giữ (chỉ khi chắc nó đã chết). `--wait`: chờ hết mọi FloodWait thay vì kết thúc khi chờ dài hơn `max_auto_wait` (không áp dụng cho `daily_cap`). FloodWait ngắn hơn `max_auto_wait` luôn được chờ rồi gửi lại đúng batch đó. Từ chối khi lần chạy trước là `waiting_flood` trước `resume_at` (kể cả nghỉ vì `daily_cap`, có câu riêng) hoặc `failed(peer_flood)` trong 24 giờ (mã 3). Đổi filter: dùng `tgmirror clone` với cùng `--src/--dst` |
| `tgmirror pause` / `tgmirror stop` | Từ terminal khác: đặt cờ `control` trên lần chạy đang chạy (chỉ có tối đa một mỗi account). `pause` giữ nó **tại chỗ** sau batch hiện tại (tiến trình vẫn sống và giữ terminal của nó) cho tới khi `tgmirror run` hoặc phím `r`; `stop` kết thúc nó (`stopped`) như Ctrl+C. Không có gì đang chạy thì báo "Không có clone nào đang chạy", mã 1 |
| `tgmirror history [n]` | Nhật ký các lần chạy, mới nhất trước: số lần, lúc bắt đầu, nguồn → đích, trạng thái, số tin đã sao chép/lỗi/bị filter loại (`--limit`, `--json`). `history n`: chi tiết một lần: thời gian, khoảng tin nguồn, filter, tin lỗi kèm lý do (tối đa 20), các lần Telegram giới hạn (flood) |
| `tgmirror retry [n]` | (Phase 5) thử lại các tin `failed` của lần chạy `n` |
| `tgmirror status` | (Phase 5) tiến độ, tốc độ, ETA, số lỗi, lần flood gần nhất của lần đang chạy |
| `tgmirror config [get\|set]` | Xem/sửa config (`[limits]`, đường dẫn, ...) |
| `tgmirror doctor` | Kiểm tra: session hợp lệ, cryptg đã cài, quyền kênh đích, cảnh báo an toàn |

Tùy chọn chung: `--version`, `--debug` (hiện traceback thay vì một câu lỗi).

Mã thoát: `0` ok, `1` lỗi chung, `2` dùng sai (kể cả filter sai: cờ, file YAML, regex; kiểm tra trước khi hỏi hay ghi gì), `3` lần chạy dừng vì flood/peer_flood/chạm `daily_cap` (hoặc `clone`/`run` bị từ chối vì phải chờ), `4` thiếu quyền (kể cả nguồn cấm forward), `130` Ctrl+C (đã lưu, lần chạy `stopped`). `clone`/`run` trả mã của lần chạy (`0` xong hoặc dừng bằng phím `q`/`tgmirror stop`, `3`, `130`, ...).

## Wizard `tgmirror clone`

```
1. Chọn nguồn           ← danh sách dialogs: channel/supergroup/forum/group (gõ để lọc); cảnh báo nếu noforwards
2. Chọn đích            ← (a) có sẵn, cùng loại với nguồn, có quyền post
                           (b) Tạo mới: nhập tên [+ about] [+ copy avatar]; nguồn forum → tự tạo topic tương ứng
3. Chọn filter          ← "Không lọc" / "Chọn tiêu chí" (checkbox media types, rồi hỏi hashtag, từ khóa,
                           từ ngày, đến ngày, dung lượng min/max) / "Nạp từ file YAML"; với cặp đã clone còn có
                           lựa chọn đầu "Giữ filter của lần chạy trước". Trả lời sai thì hỏi lại (tối đa 3 lần)
                           Chỉ hỏi khi cần wizard cho cả phần còn lại (thiếu --src hoặc đích) và không có cờ lọc/--yes
4. Tùy chọn             ← mode (auto/copy/reupload), caption handling, batch_size
5. Xem trước            ← số tin khớp filter trong 100 tin đầu của khoảng đã chọn + vài caption mẫu
                           (phase 3; ước lượng tổng số tin và ETA theo limiter chưa có). Chạy trước khi tạo đích
6. Xác nhận             ← MỘT câu "Sao chép <nguồn> → <đích> ngay bây giờ?" (đích mới ghi rõ sẽ được tạo), rồi
                           tạo đích (nếu cần) và sao chép ngay. Trả lời không: thoát mã 1, chưa tạo/ghi gì
```

Tương đương không tương tác:

```bash
tgmirror clone --src "@ten_kenh" --dst-new "Ten kenh moi" \
         --media video,photo --hashtag "#xxx" --since 2024-01-01 \
         --caption keep --yes
tgmirror clone --src -1001234567890 --dst -1009876543210 --filter-file filters.yaml --yes
```

Wizard chỉ thu thập giá trị (kể cả filter: cùng `FlagFilters` như cờ, rồi `from_flags`) rồi gọi cùng một hàm `begin_run(...)` như flag. Không được có logic chỉ tồn tại ở một nhánh.

Cả hai nhánh đi qua `engine/endpoints.py` (`find_channel` → `plan_endpoints` → `materialize`) rồi `engine/runs.py::begin_run` (kiểm `--mode`; từ chối nếu lần chạy trước của cặp đang chờ flood/`daily_cap`/PeerFlood, kiểm **ngay sau khi chọn nguồn/đích**, trước bước filter, xem trước và mọi câu hỏi; cặp đã clone trước đó thì tiếp tục từ con trỏ, không phải lỗi) và `execute` (dùng chung với `run`). Không tương tác: có `--yes` thì không hỏi; không có `--yes` và đích có sẵn thì vẫn chạy (cờ đầy đủ là sự đồng ý); không có `--yes` mà phải tạo kênh mới thì mã 2. Quy tắc kiểm tra:

- `--src`/`--dst` nhận `@username`, id (`-100...` hoặc số trần) hoặc **tên chính xác** (không phân biệt hoa thường, phải duy nhất; không đoán theo một phần tên); một từ không khớp tên nào thì thử làm username không có `@`. Chỉ khớp trong các chat đã join. **PowerShell**: phải viết `"@ten_kenh"` trong dấu nháy, vì `@ten_kenh` trần bị shell hiểu là splatting và biến mất (khi đó `--src` nuốt cờ kế tiếp và lệnh báo "unexpected extra argument").
- Nguồn bật `noforwards` và user không phải admin → từ chối (D3), thoát mã 4; là admin → cảnh báo (tắt tạm hoặc `--mode reupload` từ phase 6).
- Đích có sẵn: cùng loại với nguồn, user là admin và đăng được, khác nguồn (mã 4 nếu thiếu quyền, mã 2 nếu sai loại/trùng nguồn). Wizard chỉ liệt kê các đích thỏa điều kiện.
- Đích mới (`--dst-new`, tên 1–128 ký tự, `--about` tối đa 255): hiện chỉ tạo được kênh broadcast, nên nguồn supergroup/forum/group phải chọn đích có sẵn cho đến phase 8. Tạo kênh là thao tác ghi: câu xác nhận duy nhất của `clone` nói rõ điều đó, hoặc `--yes` khi không có terminal (thiếu `--yes` thì thoát mã 2).
- Không có terminal mà thiếu `--src`/`--dst`/`--dst-new` → mã 2, nêu tên cờ thiếu.

## Tin đặc thù (chỉ áp dụng khi lần chạy dùng chiến lược B)

Chi tiết và lý do ở `01-kien-truc.md` (mục "Tin đặc thù"). Cờ của `clone`:

```bash
tgmirror clone --src ... --dst ... --mode reupload \
             --ignore-unsupported \   # bỏ qua game/invoice (và quiz chưa trả lời), chỉ in warning
             --reset-polls \          # tạo lại poll/quiz dù mất số vote
             --placeholder            # thay mỗi tin bị bỏ bằng một tin text "không thể sao chép"
```

- Không có `--ignore-unsupported`: bước xem trước của wizard báo số tin không hỗ trợ và hỏi xác nhận; không tương tác thì lỗi mã `2`. Một lần chạy không bao giờ bỏ tin "âm thầm".
- Không có `--reset-polls`: poll/quiz bị bỏ (kèm warning) thay vì tạo lại với 0 vote, vì đó là thay đổi dữ liệu người dùng nên phải chọn rõ.
- `--placeholder` ngầm bao gồm `--ignore-unsupported`. Không áp dụng cho poll bị bỏ do thiếu `--reset-polls` (đó là lựa chọn của người dùng, không phải giới hạn kỹ thuật).
- Location, contact luôn được giữ. Với chiến lược A không có cờ nào ở đây (Telegram tự giữ loại tin).

## Điều khiển khi đang chạy

Khi `clone`/`run` đang chạy trong một terminal (foreground), dòng chữ thường (không ANSI, dùng được khi chuyển hướng): `Lần chạy 3: 4,180 tin đã sao chép, 3 lỗi (tin nguồn tới id …)` tối đa một dòng mỗi 5 giây (thêm số tin bị filter loại khi có, và một dòng tổng kết cuối), cộng các thông báo (reconcile, flood, tạm dừng) và một dòng kết quả. Có terminal thì in thêm một dòng nhắc phím.

| Cách | Tác dụng |
|---|---|
| Phím `p` | **Tạm dừng tại chỗ**: xong batch hiện tại (nếu đang chờ FloodWait thì sau lượt chờ), lưu, rồi giữ — tiến trình vẫn sống, giữ terminal, ghi heartbeat, không gửi gì |
| Phím `r` | Chạy tiếp sau khi tạm dừng |
| Phím `q` | Dừng: xong batch hiện tại, lưu, thoát (lần chạy `stopped`, mã 0) |
| Ctrl+C | Như `q` nhưng thoát mã 130; lần hai thoát ngay (batch dở dang được xử lý theo quy tắc reconcile ở `04-state-checkpoint.md`) |
| `tgmirror pause` / `run` / `stop` từ terminal khác | Như `p` / `r` / `q` (qua cờ `control` trong DB) |

Dùng phím thường (không phải Ctrl+P/Ctrl+R) vì terminal tích hợp của VS Code giữ hai tổ hợp đó cho chính nó nên chúng không tới được chương trình. Phím chỉ hoạt động khi có terminal; Windows dùng `msvcrt`, POSIX dùng `termios` ở chế độ cbreak (Ctrl+C vẫn là SIGINT). Chúng và các lệnh `pause`/`stop`/`run` cùng điều khiển một `RunControl` (`engine/runner.py`), nên giao diện Rich sau này chỉ cần gọi cùng ba việc.

Foreground TUI (Rich Live, phase 7) — chỉ còn phần hiển thị, các phím đã có ở trên:

```
tgmirror ▸ lần chạy 7  "Kenh A → Kenh A (copy)"   mode=copy   delay=2.4s
██████████░░░░░░░░░░  4,210 / 9,800   43%   2.1 msg/s   ETA 44m
done 4,180 · failed 3 · skipped(filter) 27,911 · flood 2 (last 38s ago)
[p] pause   [r] run   [q] stop
```

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
read_delay = 0.5
jitter = 0.3
long_pause_every = 200
long_pause_range = [30, 90]
daily_cap = 5000
max_auto_wait = 900
upload_concurrency = 1
```
