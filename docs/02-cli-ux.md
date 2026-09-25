# 02 — CLI & UX

Lệnh chính: `tgmirror` (entry point của package `tgmirror`).

## Lệnh

| Lệnh | Mô tả |
|---|---|
| `tgmirror --version` | In phiên bản rồi thoát |
| `tgmirror login` | Nhập `api_id`/`api_hash` (lưu config), đăng nhập (phone, code, 2FA), tạo session. Cần terminal (mã gửi qua Telegram); `--phone` điền sẵn số. Nếu đã đăng nhập thì chỉ báo lại, không cần terminal |
| `tgmirror logout` / `tgmirror whoami` | Xóa session / xem account hiện tại |
| `tgmirror channels` | Liệt kê kênh/group/forum đã join (cột: loại, tên + @username, id, số thành viên, noforwards, quyền post). `--search TEXT` lọc theo tên/username, `--writable` chỉ giữ chỗ user là admin và đăng được, `--json` |
| `tgmirror topics <src>` | Liệt kê topic của một nguồn forum (cột: id, tên, đã đóng), để tra id cho `--topic`/wizard. Từ chối nếu nguồn không phải forum (mã 2). `--json` |
| `tgmirror clone` | Sao chép một nguồn vào một đích **ngay bây giờ**, trong terminal này (foreground): không chạy nền, không lên lịch; Ctrl+C dừng (tiến độ đã lưu). Wizard (bước 1–3 chọn nguồn/đích/filter, bước 4 cách sao chép, bước 5 xem trước, một câu xác nhận) hoặc cờ không tương tác. Chạy lại cho cùng cặp nguồn/đích thì chỉ lấy tin mới hơn con trỏ (delta), dùng lại filter của lần trước; đưa filter khác thì quét lại từ đầu (bỏ qua tin đã sao chép), `--no-filter` bỏ filter cũ (xem `03-filters.md`). Cờ: `--src`, `--dst` \| `--dst-new` (+ `--about`), cờ lọc (`--media`, `--hashtag`, `--contains`, `--regex`, `--exclude-regex`, `--exclude-media`, `--since`, `--until`, `--min-size`, `--max-size`, `--from-user`, `--topic`, `--album`, `--filter-file`), `--no-filter`, `--fresh` (làm lại từ đầu, xem dưới), `--pushdown/--no-pushdown`, `--preview/--no-preview`, `--mode auto\|copy\|reupload` (xem "Tin đặc thù" và "Caption handling"), `--caption keep\|strip-links\|append\|none` (+ `--caption-text`), `--reset-polls`, `--ignore-unsupported`, `--placeholder`, `--yes-i-administer-this-channel`, `--topic-as-hashtag/--no-topic-as-hashtag` (phase 8, xem "Đích khác loại nguồn"), `--batch-size 1..100` (mặc định `[limits] batch_size`), `--wait`, `--force-takeover`, `--yes` (bỏ câu xác nhận) |
| `tgmirror run [n]` | Chạy lại cặp nguồn/đích của lần chạy `n` (mặc định lần gần nhất; số lấy từ `tgmirror history`) với filter và tùy chọn của lần đó: chỉ lấy tin mới hơn, hoặc tiếp tục chỗ Ctrl+C/lỗi đã dừng. Không cho số, có terminal, và lịch sử có **hơn một cặp**: hỏi chạy tiếp cặp nào (chọn từ danh sách, đã làm 2026-09-23); một cặp hoặc không có terminal thì vẫn tự lấy lần gần nhất như trước, không hỏi gì. Nếu lần chạy đó đang **tạm dừng ở terminal khác** thì cho nó chạy tiếp ở đó thay vì mở lần thứ hai. `--force-takeover`: chạy dù có vẻ tiến trình khác đang giữ (chỉ khi chắc nó đã chết). `--wait`: chờ hết mọi FloodWait thay vì kết thúc khi chờ dài hơn `max_auto_wait` (không áp dụng cho `daily_cap`). FloodWait ngắn hơn `max_auto_wait` luôn được chờ rồi gửi lại đúng batch đó. Từ chối khi lần chạy trước là `waiting_flood` trước `resume_at` (kể cả nghỉ vì `daily_cap`, có câu riêng) hoặc `failed(peer_flood)` trong 24 giờ (mã 3). `--fresh`: quên tiến độ của cặp và sao chép lại từ đầu (kèm `--yes` để không hỏi). Đổi filter: dùng `tgmirror clone` với cùng `--src/--dst` |
| `tgmirror pause` / `tgmirror stop` | Từ terminal khác: đặt cờ `control` trên lần chạy đang chạy (chỉ có tối đa một mỗi account). `pause` giữ nó **tại chỗ** sau batch hiện tại (tiến trình vẫn sống và giữ terminal của nó) cho tới khi `tgmirror run` hoặc phím `r`; `stop` kết thúc nó (`stopped`) như Ctrl+C. Không có gì đang chạy thì báo "Không có clone nào đang chạy", mã 1 |
| `tgmirror history [n]` | Nhật ký các lần chạy, mới nhất trước: số lần, lúc bắt đầu, nguồn → đích, trạng thái, số tin đã sao chép/lỗi/bị filter loại (`--limit`, `--json`). `history n`: chi tiết một lần: thời gian, khoảng tin nguồn, filter (lần thử lại: "Thử lại: tin lỗi của lần chạy n" thay cho hai dòng đó), số tin đã xóa ở nguồn, tin lỗi kèm lý do (tối đa 20), các lần Telegram giới hạn (flood) |
| `tgmirror retry [n]` | Thử lại các tin còn `failed` của lần chạy `n` (mặc định lần gần nhất; xem "Thử lại tin lỗi" dưới). Là một lần chạy riêng (có trong `history`, pause/stop được) trong foreground của terminal này. `--force-takeover`, `--wait` như `run`. Không có tin lỗi nào thì báo và thoát mã 0, không kết nối Telegram |
| `tgmirror status` | Tiến độ, tốc độ, ETA, số lỗi, delay hiện tại và số lần Telegram giới hạn trong 24 giờ của **lần đang chạy** (không có thì lần gần nhất). Chỉ đọc DB, không kết nối Telegram nên chạy được từ terminal thứ hai trong lúc clone đang giữ session. `--json`. Xem "Xem tiến độ" dưới |
| `tgmirror config get [KEY]` / `config set KEY VALUE` | Xem (mọi `[limits]` + đường dẫn, hoặc một khóa, `--json`) / sửa một khóa `[limits]`. Không kết nối Telegram; `get` không tham số giống `config get`. `set` kiểm tra giá trị (kể cả luật liên trường như `max_delay >= min_delay`) trước khi ghi, giữ nguyên phần còn lại của `config.toml` (comment, `api_id`/`api_hash`) như `login` đã làm |
| `tgmirror doctor` | Kiểm tra: session hợp lệ, cryptg đã cài, quyền kênh đích, cảnh báo an toàn |

Tùy chọn chung: `--version`, `--debug` (hiện traceback thay vì một câu lỗi).

Mã thoát: `0` ok, `1` lỗi chung, `2` dùng sai (kể cả filter sai: cờ, file YAML, regex; kiểm tra trước khi hỏi hay ghi gì), `3` lần chạy dừng vì flood/peer_flood/chạm `daily_cap` (hoặc `clone`/`run` bị từ chối vì phải chờ), `4` thiếu quyền (kể cả nguồn cấm forward), `130` Ctrl+C (đã lưu, lần chạy `stopped`). `clone`/`run`/`retry` trả mã của lần chạy (`0` xong hoặc dừng bằng phím `q`/`tgmirror stop`, `3`, `130`, ...).

## Giao diện full-screen (menu)

Gõ trơn `tgmirror` (không lệnh con) khi có terminal thật mở thẳng một app full-screen (`rich.live.Live(screen=True)`, khung header/footer cố định) thay vì gõ từng lệnh — vòng lặp menu chỉ sống trong đúng tiến trình đó, từ lúc mở tới lúc thoát, không có gì chạy nền hay chạy tiếp sau khi thoát. Không có terminal (chuyển hướng, script, CI) thì gõ trơn vẫn in help như trước.

Điều hướng: ↑/↓ chọn, Enter chọn/xác nhận, Esc lùi lại một bước (menu chính thì không có gì để lùi). Menu chính khi đã đăng nhập: Sao chép mới, Chạy tiếp, Thử lại tin lỗi, Trạng thái, Lịch sử, Kênh đã join, Tài khoản, Cấu hình, Thoát — hai mục "Chạy tiếp"/"Thử lại tin lỗi" chỉ hiện khi đã có ít nhất một cặp nguồn/đích. "Chạy tiếp"/"Thử lại tin lỗi" chỉ hỏi chọn cặp khi có hơn một cặp (danh sách trong khung, không phải câu hỏi kiểu wizard), rồi vào thẳng màn hình tiến độ — đúng khung tiến độ đã có ở "Điều khiển khi đang chạy" (dưới), phím `p`/`r`/`q` hoạt động y hệt, Ctrl+C giữa lúc này dừng run **và** thoát hẳn app (không lùi về menu). "Trạng thái" tự làm mới vài giây một lần (giống `tgmirror status` nhưng sống); "Lịch sử" là danh sách cuộn được, Enter xem chi tiết một lần chạy; "Kênh đã join" gõ để lọc tại chỗ; "Tài khoản" xem ai đang đăng nhập và đăng xuất (có hỏi lại).

Chưa đăng nhập (hoặc chưa lưu `api_id`/`api_hash`), app vẫn mở, với menu rút gọn: Đăng nhập, Trạng thái, Lịch sử, Cấu hình, Thoát.

**Sao chép mới** là đúng wizard của `tgmirror clone` không kèm cờ (cùng câu hỏi, cùng luật, cùng cảnh báo và xác nhận, kể cả câu hỏi D3), vẽ trong khung: danh sách dài gõ để lọc và cuộn theo mục đang chọn, hộp chọn nhiều dùng Space, các câu đã trả lời hiện mờ phía trên. **Esc lùi một câu hỏi**, kể cả sang bước trước (câu trả lời cũ được chọn sẵn); Esc ở câu đầu tiên, hoặc trả lời "Không" ở một câu xác nhận, bỏ wizard và về menu mà không ghi gì. Một lựa chọn bị từ chối (ví dụ tên kênh trống, đích không hợp lệ) hiện lý do và hỏi lại bước đó. Xác nhận xong thì vào thẳng màn hình tiến độ; Esc từ đó về menu chính.

**Đăng nhập** hỏi đúng những gì `tgmirror login` hỏi (`api_id`/`api_hash` nếu chưa có, số điện thoại, mã, mật khẩu hai bước); mã, mật khẩu và `api_hash` không hiện khi gõ, số điện thoại không hiện lại sau khi nhập. Esc bỏ đăng nhập ở bất kỳ câu nào (mỗi câu trả lời đã gửi ngay tới Telegram, nên không lùi từng bước). Đăng xuất từ "Tài khoản" đóng luôn kết nối; đăng nhập lại mở kết nối mới, không cần thoát app.

**Cấu hình** liệt kê các đường dẫn (`config.toml`, DB, thư mục session, chỉ để xem) rồi mọi khóa `[limits]` với giá trị hiện tại; Enter trên một khóa mở một câu hỏi (giá trị mới, ô đã điền sẵn giá trị cũ) rồi lưu bằng `core.config.set_limit` — đúng hàm `tgmirror config set` dùng, cùng luật kiểm tra trước khi ghi. Không kết nối Telegram nên có cả ở menu rút gọn khi chưa đăng nhập.

## Wizard `tgmirror clone`

```
1. Chọn nguồn           ← danh sách dialogs: channel/supergroup/forum/group (gõ để lọc); cảnh báo nếu noforwards
2. Chọn đích            ← (a) có sẵn, bất kỳ loại nào (phase 8: không còn phải cùng loại nguồn), có quyền post
                           (b) Tạo mới: nhập tên [+ about] [+ copy avatar] (tên sai thì chỉ hỏi lại tên); luôn cùng loại nguồn (group → supergroup vì
                           basic group không tạo mới được); nguồn forum → tự tạo topic tương ứng khi cần (kiểu lười)
    Ngay sau bước 2 (trước mọi câu hỏi khác): cặp đang chạy ở nơi khác / đang chờ FloodWait / chạm giới hạn ngày
    thì bị từ chối luôn (`CloneFlow._read_history`), không để user trả lời hết các bước rồi mới biết.
2b. Tiếp tục hay làm lại ← chỉ với cặp đã sao chép được tin: "Tiếp tục: chỉ lấy tin mới" / "Làm lại từ đầu: sao chép
                           lại tất cả" (= `--fresh`). Chỉ hỏi khi wizard cũng hỏi nguồn/đích, không có `--yes` và chưa có `--fresh`
3. Chọn filter          ← "Không lọc" / "Chọn tiêu chí" (checkbox media types, rồi hỏi hashtag, từ khóa,
                           từ ngày, đến ngày, dung lượng min/max, và — nguồn là forum — checkbox chọn topic) / "Nạp từ
                           file YAML"; với cặp đã clone còn có lựa chọn đầu "Giữ filter của lần chạy trước". Trả lời
                           sai thì chỉ hỏi lại đúng câu đó (tối đa 3 lần; các câu đã trả lời được giữ); lỗi giữa hai câu
                           (vd. từ ngày sau đến ngày) thì hỏi lại cả bước. Chỉ hỏi khi cần wizard cho cả phần còn lại (thiếu --src
                           hoặc đích) và không có cờ lọc/--yes
4. Cách sao chép       ← luôn hỏi **mode** (`--mode`): tự động (mặc định) / chỉ forward (`copy`) / tải xuống rồi tải lên lại
                           (`reupload`). `copy` xong ngay (forward không đổi được caption). Với `auto` và `reupload` còn MỘT câu
                           có/không (mặc định không): `auto` "Đổi caption của tin media?", `reupload` "Tùy chỉnh caption và cách
                           xử lý tin không sao chép được?"; trả lời có thì hỏi caption (giữ / bỏ link về nguồn / thêm chữ / bỏ),
                           và với `reupload` tick `--reset-polls`, `--ignore-unsupported`, `--placeholder`. Nguồn cấm lưu nội dung
                           mà user là admin: bỏ câu mode (chỉ còn tải lên lại) và hỏi thẳng caption + tick. Chỉ hỏi khi wizard cũng
                           hỏi nguồn/đích, không có `--yes` và chưa có cờ nào của bước này (giống bước filter).
                           Nguồn forum → đích không phải forum (phase 8, "topic_loss"): với auto (mọi caption) hoặc
                           reupload, hỏi thêm "Giữ tên topic dưới dạng hashtag?" (mặc định có; có thì các tin trong topic
                           được gửi lại thay vì forward); với copy, hỏi xác nhận riêng "Toàn bộ topic sẽ bị bỏ, tiếp tục?"
                           (mặc định không; `--yes` đủ để đồng ý, không có terminal thì mã 2) — xem `01-kien-truc.md`,
                           "Đích khác loại nguồn"
5. Xem trước            ← số tin khớp filter trong 100 tin đầu của khoảng đã chọn + vài caption mẫu
                           (phase 3; ước lượng tổng số tin và ETA theo limiter chưa có). Chạy trước khi tạo đích
6. Xác nhận             ← MỘT câu "Sao chép <nguồn> → <đích> ngay bây giờ?" (đích mới ghi rõ sẽ được tạo), rồi
                           tạo đích (nếu cần) và sao chép ngay. Trả lời không: thoát mã 1, chưa tạo/ghi gì.
                           Làm lại từ đầu trên cặp đã có tin: câu hỏi nêu số tin sẽ bị quên và cảnh báo đích có thể bị trùng
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
- Nguồn bật `noforwards` (D3, đổi 2026-09-20: user chịu hoàn toàn trách nhiệm; tinh chỉnh 2026-09-25): tài khoản không phải admin và **không có** `--yes-i-administer-this-channel` (hoặc, trong wizard/menu, không gõ đúng nguyên văn cờ đó khi được hỏi, xem dưới) → từ chối, thoát mã 4, câu lỗi chỉ ra cờ; là admin → cảnh báo (tắt tạm hoặc `--mode reupload`). Mode chỉ forward (`copy`, hoặc `auto` không có gì phải viết lại) trên nguồn cấm lưu nội dung bị từ chối **trước khi tạo đích hay bắt đầu lần chạy** (`ForwardsRestricted`, mã 4, khuyên `--mode reupload`): Telegram từ chối forward khỏi nguồn này, admin hay không; và khi mode cờ đã cho như vậy thì cũng không hỏi gõ nguyên văn cờ D3 (gõ cũng không giúp được gì) — `plan_endpoints` từ chối như thường. Xác nhận được rồi thì tài khoản nào cũng được (user là chủ kênh bằng tài khoản khác): `warn.noforwards_unadministered` nếu không phải admin, và **mỗi lần chạy dựa trên lời tuyên bố** (`clone`, `run`, `retry`) in `warn.responsibility` (trách nhiệm về quyền sao chép, bản quyền, Điều khoản Telegram; tgmirror không kiểm tra được). Với `--mode reupload` (hoặc `--mode auto` cùng `--caption` khác `keep`, vì khi đó tin có caption cũng được tải xuống) mà nguồn cấm lưu nội dung, user phải tự tuyên bố: tài khoản là admin thì một câu hỏi Có/Không (mặc định không); tài khoản không phải admin thì cờ `--yes-i-administer-this-channel` trên dòng lệnh, hoặc — có terminal mà không có dòng lệnh để gõ (wizard, menu full-screen) — gõ nguyên văn đúng chữ cờ đó vào một câu hỏi riêng khi được hỏi ở bước chọn đích (không phải Có/Không, để giữ đúng mức "cố ý" như gõ cờ thật; gõ gì khác hoặc Esc thì coi như từ chối); **`--yes` không thay được cho cả hai** (đó là lời của chính user, không phải việc bỏ qua một câu hỏi), nên không có terminal mà thiếu cờ này thì mã 2. Kiểm trước khi tạo đích hay ghi gì. `run`/`retry` đọc lại nguồn trước khi tải lên lại (xem `01-kien-truc.md`, "Kiểm lại nguồn khi chạy lại").
- Đích có sẵn: bất kỳ loại nào (phase 8: không còn phải cùng loại nguồn, xem `01-kien-truc.md` "Đích khác loại nguồn"), user là admin và đăng được, khác nguồn (mã 4 nếu thiếu quyền, mã 2 nếu trùng nguồn). Wizard chỉ liệt kê các đích thỏa điều kiện.
- Đích mới (`--dst-new`, tên 1–128 ký tự, `--about` tối đa 255): luôn cùng loại nguồn (group → supergroup, vì basic group không tạo mới được qua API; các loại khác giữ nguyên). Tạo kênh là thao tác ghi: câu xác nhận duy nhất của `clone` nói rõ điều đó, hoặc `--yes` khi không có terminal (thiếu `--yes` thì thoát mã 2).
- Không có terminal mà thiếu `--src`/`--dst`/`--dst-new` → mã 2, nêu tên cờ thiếu.

## Tin đặc thù (chỉ áp dụng khi lần chạy dùng chiến lược B)

Chi tiết và lý do ở `01-kien-truc.md` (mục "Tin đặc thù"). Cờ của `clone` (chỉ dùng được với `--mode reupload`; với mode khác là lỗi mã `2` nêu tên cờ):

```bash
tgmirror clone --src ... --dst ... --mode reupload \
             --ignore-unsupported \   # bỏ qua game/invoice (và quiz chưa trả lời), chỉ in warning
             --reset-polls \          # tạo lại poll/quiz dù mất số vote
             --placeholder            # thay mỗi tin bị bỏ bằng một tin text "không thể sao chép"
```

- Không có `--ignore-unsupported`/`--placeholder`: gặp game, invoice hoặc quiz chưa trả lời thì lần chạy **dừng** tại tin đó (mã `2`, nêu id tin và hai cờ), tiến độ đã lưu; chạy lại `tgmirror clone` với cờ thì đi tiếp. Một lần chạy không bao giờ bỏ tin "âm thầm". (Không quét cả nguồn để đếm trước: bước xem trước chỉ là mẫu.)
- Không có `--reset-polls`: poll/quiz bị bỏ kèm một dòng warning mỗi tin (`Bỏ qua tin N (unsupported:poll)`) và tổng cuối lần chạy, vì tạo lại với 0 vote là thay đổi dữ liệu nên phải chọn rõ. Không dừng lần chạy và không cần `--ignore-unsupported`.
- `--placeholder` ngầm bao gồm `--ignore-unsupported`. Không áp dụng cho poll bị bỏ do thiếu `--reset-polls` (đó là lựa chọn của người dùng, không phải giới hạn kỹ thuật).
- Location, contact luôn được giữ. Với chiến lược A không có cờ nào ở đây (Telegram tự giữ loại tin).
- Tin đã bị bỏ không được delta nhặt lại (con trỏ đã qua): thêm `--reset-polls` sau này không đưa các poll cũ về; muốn vậy dùng `--fresh`.

## Điều khiển khi đang chạy

Khi `clone`/`run` đang chạy (foreground): có terminal thật thì hiện TUI (Rich Live, xem dưới), phím đã hoạt động và hiện ngay trong khung nên không có dòng nhắc riêng. Không có terminal (chuyển hướng, chạy từ script, test) thì phím cũng không hoạt động, và tiến độ là dòng chữ thường (không ANSI, dùng được khi chuyển hướng): `Lần chạy 3: 4,180 tin đã sao chép, 3 lỗi (tin nguồn tới id …)` tối đa một dòng mỗi 5 giây (thêm số tin bị filter loại khi có, và một dòng tổng kết cuối), cộng các thông báo (reconcile, flood, tạm dừng) và một dòng kết quả.

| Cách | Tác dụng |
|---|---|
| Phím `p` | **Tạm dừng tại chỗ**: xong batch hiện tại (nếu đang chờ FloodWait thì sau lượt chờ), lưu, rồi giữ — tiến trình vẫn sống, giữ terminal, ghi heartbeat, không gửi gì |
| Phím `r` | Chạy tiếp sau khi tạm dừng |
| Phím `q` | Dừng: xong batch hiện tại, lưu, thoát (lần chạy `stopped`, mã 0) |
| Ctrl+C | Như `q` nhưng thoát mã 130; lần hai thoát ngay (batch dở dang được xử lý theo quy tắc reconcile ở `04-state-checkpoint.md`) |
| `tgmirror pause` / `run` / `stop` từ terminal khác | Như `p` / `r` / `q` (qua cờ `control` trong DB) |

Dùng phím thường (không phải Ctrl+P/Ctrl+R) vì terminal tích hợp của VS Code giữ hai tổ hợp đó cho chính nó nên chúng không tới được chương trình. Phím chỉ hoạt động khi có terminal; Windows dùng `msvcrt`, POSIX dùng `termios` ở chế độ cbreak (Ctrl+C vẫn là SIGINT). Chúng và các lệnh `pause`/`stop`/`run` cùng điều khiển một `RunControl` (`engine/runner.py`), nên TUI Rich (dưới) chỉ cần gọi cùng ba việc, không có đường riêng.

Foreground TUI (Rich Live) — đã làm (2026-09-23, `ui/tui.py::TuiReporter`, xem `06-lo-trinh.md` "Phase 7 — ghi chú"), dùng khi có terminal thật (không có thì dùng dòng chữ thường ở trên):

```
tgmirror ▸ lần chạy 7  "Kenh A → Kenh A (copy)"   mode=copy   delay=2.4s
██████████░░░░░░░░░░  4,210 / 9,800   43%   2.1 msg/s   ETA 44m
done 4,180 · failed 3 · skipped(filter) 27,911 · flood 2 (last 38s ago)
[p] pause   [r] run   [q] stop
```

- Không cho phép người dùng ép tốc độ thấp hơn `min_delay` (bảo vệ tài khoản); có thể tăng delay.

## Caption handling (`--caption`)

`--caption keep` (mặc định) · `strip-links` (bỏ link `t.me/<nguồn>` và `@nguồn`; hyperlink trỏ về nguồn giữ chữ, mất link) · `append` (cộng `--caption-text "<text>"` sau caption, cách hai dòng trống) · `none`.

- Chỉ đổi **caption của tin media** (ảnh, video, tài liệu, ... kể cả tin đầu của album). Văn bản của tin không có media (hay chỉ có link preview) là chính nội dung nên không bao giờ bị đổi. `append` chỉ cộng vào caption **đã có** (thành viên album không caption không thành có caption).
- Chiến lược A (forward) không sửa được caption, nên `--mode copy` với `--caption` khác `keep` là lỗi mã 2. Với `--mode auto`, chỉ những unit có tin media kèm caption không đi forward: nếu nguồn cho lưu nội dung thì chúng được **gửi bằng mã file** (không tải gì, nhanh gần bằng forward), còn nguồn `noforwards` hay unit không phải file thì tải xuống rồi tải lên lại (chậm hơn nhiều); phần còn lại vẫn forward; thứ tự giữ nguyên. Nếu Telegram không cho gửi lại một file bằng mã, unit đó tự lùi về tải xuống rồi tải lên (một dòng thông báo). Với `--mode reupload`, mọi unit đều tải lên lại.
- `--caption append` cần `--caption-text` (và ngược lại). Caption dài quá giới hạn của Telegram sau khi cộng: tin đó `failed` với lý do của Telegram, `retry` xử lý sau.
- Offset của entity theo UTF-16 như Telegram; định dạng (đậm, nghiêng, ...) được giữ và dịch chuyển khi xóa link.

## Config

Đường dẫn dữ liệu qua `platformdirs`:

```
<data_dir>/tgmirror.db              state
<data_dir>/sessions/<name>.session  Telethon session (bí mật, chmod 600 nếu được)
<config_dir>/config.toml            [limits], và api_id/api_hash nếu máy không có keyring
```

**`api_id`/`api_hash` (Phase 9 — keyring, `core/secrets.py`)**: đọc theo thứ tự biến môi trường `TGMIRROR_API_ID`/`TGMIRROR_API_HASH` → `TGMIRROR_API_ID_FILE`/`TGMIRROR_API_HASH_FILE` (đường dẫn tới file chứa giá trị, cho Docker/Kubernetes secret) → keyring hệ thống (Windows Credential Manager, macOS Keychain, Secret Service) → `config.toml` (cách cũ). `tgmirror login` hỏi rồi ghi vào keyring nếu máy có một keyring "dùng được" (không phải `fail`/`null`/backend lưu file trần) và xóa hai dòng đó khỏi `config.toml`; máy không có keyring thì ghi `config.toml` như trước, lên đầu file, giữ nguyên phần còn lại và chú thích (không bao giờ in `api_hash`). Ai đã có credential trong `config.toml` không tự chuyển ngầm; `login` lần sau (đang chạy lại vì lý do khác, ví dụ session hết hạn) tự chuyển nếu máy giờ có keyring, và `tgmirror doctor` cũng gợi ý việc đó. `tgmirror config get` xem đường dẫn và mọi khóa `[limits]` (`get KEY` một khóa, `--json` cho cả hai); `config set KEY VALUE` sửa một khóa `[limits]` (kiểm tra qua đúng model `Limits`, kể cả luật liên trường, trước khi ghi) theo cùng lối "chỉ đổi khóa liên quan, giữ nguyên phần còn lại" như `login`; `long_pause_range` nhận hai số cách nhau dấu phẩy (`30,90`). Có cả trong menu full-screen ("Cấu hình"). `tgmirror doctor` cho biết credential đang lấy từ đâu (env / file / keyring `<tên backend>` / `config.toml`), không bao giờ in giá trị.

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
prefetch = 0
tmp_budget_mb = 2048
download_requests = 4   # request đang bay khi tải xuống file (0 = tắt pool, cách cũ của Telethon)
upload_requests = 8     # tương tự nhưng cho tải lên (ngân sách riêng, không dùng chung)
upload_connections = 8  # số kết nối chia cho phần tải lên
pool_min_mb = 10        # file nhỏ hơn giữ cách tải của Telethon
```

## Thử lại tin lỗi (`retry`)

Một tin `failed` (Telegram từ chối, hoặc không tạo tin nào cho nó) nằm dưới con trỏ nên `run` không bao giờ đọc lại nó; `retry` là cách gửi lại. `tgmirror retry [n]` gửi lại đúng các tin **còn** `failed` mà lần chạy `n` để lại (chính là danh sách `history n`), đọc chúng theo id (không quét nguồn), và **không** đụng tới con trỏ hay filter của cặp. Nó là một lần chạy mới trong nhật ký (`history` ghi "Thử lại: tin lỗi của lần chạy n"), qua cùng limiter/FloodGuard/pause/stop như mọi lần chạy.

- Tin gửi lại thành công được thêm vào **cuối** đích (thứ tự ở đích không còn theo thời gian, như khi đổi filter).
- Tin lại lỗi thuộc về lần retry mới: `tgmirror retry` (không số) thử lại chúng lần nữa. `retry n` lần hai trên cùng `n` báo không còn gì để thử.
- Tin đã bị xóa ở nguồn từ lúc lỗi không gửi được nữa: chúng được đặt sang `skipped` với `reason='gone_from_source'` (bộ đếm `gone` của lần chạy, dòng `Đã xóa ở nguồn` ở `history n`), để không hiện mãi như lỗi không ai sửa được.
- Tin `skipped` (loại không hỗ trợ) không được thử lại.
- Dừng giữa chừng (`q`, Ctrl+C, `stop`): gợi ý chạy tiếp là `tgmirror retry n`, **không phải** `run` (`run` chỉ tìm tin mới). Các tin chưa kịp gửi vẫn `failed` như cũ.
- Khi một `clone`/`run` kết thúc mà có tin lỗi, nó in gợi ý `tgmirror retry <lần chạy>`.

## Xem tiến độ (`status`)

```
Lần chạy 7: Kenh A → Kenh A (copy)
Trạng thái:  đang chạy
Tiến độ:     ~43% (32100 / tối đa 74500 tin)
Tốc độ:      2.1 tin/giây (trung bình từ lúc bắt đầu), còn khoảng 44 phút
Kết quả:     4180 đã sao chép, 3 lỗi, 27911 bị filter loại
Cap ngày:    còn tối đa 42400 tin, cap 5000/ngày: cần nghỉ thêm khoảng 8 ngày
Giới hạn:    nghỉ 2.4s giữa các lần gửi; hôm nay đã gửi 1200/5000 tin
Telegram:    2 lần bị giới hạn trong 24 giờ qua; gần nhất 38 giây trước (flood_wait, 30s)
```

Mọi số là **ước lượng**: `status` chỉ đọc DB (clone đang chạy giữ session Telegram nên terminal thứ hai không kết nối được), vì vậy tổng được ghi một lần lúc lần chạy bắt đầu: số tin Telegram đếm được cho khoảng và filter của lần chạy (`RunOptions.total_items`, một request `count`, xem `01-kien-truc.md`, "Analyze và tiến độ"), cùng id tin mới nhất của nguồn (`src_last_id`) làm phương án dự phòng.

- **Tiến độ** của lần chạy thường là số tin đã xử lý (sao chép, lỗi, bị filter loại, bị bỏ vì không hỗ trợ, hoặc đã có sẵn ở đích) trên `total_items`. Đó là **cận trên** (tin service được đếm, filter phía client không được trừ trước) nên lần chạy bỏ qua nhiều tin chỉ tới 100% khi xong; chữ "tối đa" nói điều đó. Lần chạy từ trước khi có `total_items` (hoặc phép đếm lỗi) tính theo *id* nguồn như trước (id có khoảng trống, filter nhảy nhanh qua quãng không khớp nên chỉ là tỉ lệ thô). Tiến độ của `retry` chính xác: số tin lỗi đã xử lý trên số còn chờ.
- **Cap ngày**: khi số tin còn lại vượt phần `daily_cap` còn cho phép hôm nay, `status` (và một dòng đầu lần chạy) nói cần nghỉ thêm bao nhiêu ngày. Chỉ hiện cho lần chạy đang sống hoặc đang `waiting_flood`.
- **Tốc độ** là trung bình từ lúc bắt đầu (gồm cả lúc tạm dừng và chờ flood): tin đã sao chép hoặc lỗi mỗi giây; chưa hiện trong 5 giây đầu. **ETA** ngoại suy từ đó và chỉ hiện khi lần chạy thật sự đang `running`.
- Lần chạy ghi `running`/`paused` nhưng heartbeat cũ hơn 2 phút được báo là **không có tiến trình nào giữ** (tín hiệu cuối lúc ...); lần chạy kế tiếp của cặp ghi nó `failed('interrupted')` (`04-state-checkpoint.md`).
- `waiting_flood`: hiện thời điểm được chạy lại. Lần chạy đã kết thúc mà còn tin lỗi: gợi ý `tgmirror retry`.
- Số lần Telegram giới hạn trong 24 giờ là của cả account (`flood_log`); dòng "Giới hạn" đọc `limiter_state` (delay hiện tại; số tin gửi hôm nay, về 0 khi sang ngày mới theo giờ máy).
- `--json`: `run`, `status`, `live`, `abandoned`, `progress`, `speed_per_second`, `eta_seconds`, `copied`, `failed`, `failed_now`, `gone_from_source`, `total_items`, `handled`, `left`, `cap_rest_days`, `limiter`, `floods_24h`, `last_flood`, ... (khóa cho máy đọc, không dịch).

### Trong lúc chạy (`clone`/`run`)

Đầu lần chạy có một dòng "Ước tính: tối đa N tin cần xem xét" (và một dòng về cap ngày nếu tốn hơn một ngày). Dòng tiến độ theo batch là `Lần chạy 7: 1200/9800 tin (~12%): 1150 đã sao chép, 50 bị filter loại, 0 lỗi.` (không có tổng thì dạng cũ). Với file ≥ 8 MB (chiến lược B) có thêm dòng riêng: `Tải xuống tin 42: 45% (12.3 MB / 27.4 MB, 3.2 MB/s).` và `Tải lên tin 42: ...`, in lúc bắt đầu, khi tiến thêm 5% (cách nhau ít nhất 5 giây), hoặc sau 30 giây dù chưa tiến nhiều, và khi xong (chưa xong thì không hiện 100%). Giao diện Rich (`ui/tui.py`, đã làm) dùng cùng dữ liệu (`Reporter.progress`, `Reporter.transfer`) cho thanh tiến độ và hai dòng tải xuống / tải lên.

## Làm lại từ đầu (`--fresh`)

Chạy lại cùng cặp nguồn/đích là delta; `--fresh` (trên `clone` và `run`) là "bắt đầu lại": **quên tiến độ của cặp** (mọi hàng `msg_map`, con trỏ), đọc lại tin mới nhất của đích làm `dst_base_id` mới, rồi sao chép lại từ tin đầu tiên. Filter đang nhớ được giữ (đưa filter khác hoặc `--no-filter` nếu muốn đổi). Nhật ký `history` không bị đụng tới.

Nó có thể làm **trùng tin** nếu đích còn các bản sao cũ, nên có hàng rào: khi có tin để quên, một câu hỏi nêu số tin ("Đích đã có N tin từ các lần chạy trước; làm lại từ đầu sẽ sao chép lại tất cả và có thể bị trùng…"); với `clone` đó chính là câu xác nhận duy nhất. `--yes` đồng ý; không có terminal mà thiếu `--yes` thì mã 2 và không quên gì. Cặp chưa có tin nào thì không hỏi. `run --fresh` không nhận lệnh "chạy tiếp lần đang tạm dừng ở terminal khác": cặp đang có lần chạy sống thì báo bị giữ (mã 1) và không quên gì. Chưa làm: chế độ kiểm tra/sửa (chỉ sao chép lại tin còn thiếu ở đích).

Dùng khi đã dọn kênh đích, hoặc thật sự muốn một bản sao thứ hai. Nếu chỉ cần bản sao sạch thì `--dst-new` đã là cặp mới.
