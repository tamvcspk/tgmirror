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
| `tgmirror clone` | Sao chép một nguồn vào một đích **ngay bây giờ**, trong terminal này (foreground): không chạy nền, không lên lịch; Ctrl+C dừng (tiến độ đã lưu). Wizard (bước 1–3 chọn nguồn/đích/filter, bước 4 cách sao chép, bước 5 xem trước, một câu xác nhận) hoặc cờ không tương tác. Chạy lại cho cùng cặp nguồn/đích thì chỉ lấy tin mới hơn con trỏ (delta), dùng lại filter của lần trước; đưa filter khác thì quét lại từ đầu (bỏ qua tin đã sao chép), `--no-filter` bỏ filter cũ (xem `03-filters.md`). Cờ: `--src`, `--dst` \| `--dst-new` (+ `--about`), cờ lọc (`--media`, `--hashtag`, `--contains`, `--regex`, `--exclude-regex`, `--exclude-media`, `--since`, `--until`, `--min-size`, `--max-size`, `--from-user`, `--topic`, `--forwarded/--not-forwarded`, `--forwarded-from`, `--has-link`, `--link-domain`, `--album`, `--filter-file`, `--selection`, `--exact`), `--no-filter`, `--fresh` (làm lại từ đầu, xem dưới), `--pushdown/--no-pushdown`, `--preview/--no-preview`, `--mode auto\|copy\|reupload` (xem "Tin đặc thù" và "Caption handling"), `--caption keep\|strip-links\|append\|none` (+ `--caption-text`), `--reset-polls`, `--ignore-unsupported`, `--placeholder`, `--yes-i-administer-this-channel`, `--topic-as-hashtag/--no-topic-as-hashtag` (phase 8, xem "Đích khác loại nguồn"), `--batch-size 1..100` (mặc định `[limits] batch_size`), `--wait`, `--force-takeover`, `--yes` (bỏ câu xác nhận) |
| `tgmirror run [n]` | Chạy lại cặp nguồn/đích của lần chạy `n` (mặc định lần gần nhất; số lấy từ `tgmirror history`) với filter và tùy chọn của lần đó: chỉ lấy tin mới hơn, hoặc tiếp tục chỗ Ctrl+C/lỗi đã dừng. Không cho số, có terminal, và lịch sử có **hơn một cặp**: hỏi chạy tiếp cặp nào (chọn từ danh sách, đã làm 2026-09-23); một cặp hoặc không có terminal thì vẫn tự lấy lần gần nhất như trước, không hỏi gì. Nếu lần chạy đó đang **tạm dừng ở terminal khác** thì cho nó chạy tiếp ở đó thay vì mở lần thứ hai. `--force-takeover`: chạy dù có vẻ tiến trình khác đang giữ (chỉ khi chắc nó đã chết). `--wait`: chờ hết mọi FloodWait thay vì kết thúc khi chờ dài hơn `max_auto_wait` (không áp dụng cho `daily_cap`). FloodWait ngắn hơn `max_auto_wait` luôn được chờ rồi gửi lại đúng batch đó. Từ chối khi lần chạy trước là `waiting_flood` trước `resume_at` (kể cả nghỉ vì `daily_cap`, có câu riêng) hoặc `failed(peer_flood)` trong 24 giờ (mã 3). `--fresh`: quên tiến độ của cặp và sao chép lại từ đầu (kèm `--yes` để không hỏi). Đổi filter: dùng `tgmirror clone` với cùng `--src/--dst`. `--from-backup THƯ_MỤC` (Phase 15b): trỏ một cặp restore sang thư mục backup khác (phải cùng nguồn, xem "Restore từ đĩa lên kênh"), thay cho từ chối `BackupDirMissing` nếu thư mục cũ đã mất/dời chỗ |
| `tgmirror pause` / `tgmirror stop` | Từ terminal khác: đặt cờ `control` trên lần chạy, **lần backup** (phase 11a) **hoặc thao tác `delete`** (phase 20) đang chạy (chỉ có tối đa một mỗi account, thử lần chạy, rồi backup, rồi thao tác). `pause` giữ nó **tại chỗ** sau batch/tin hiện tại (tiến trình vẫn sống và giữ terminal của nó) cho tới khi chạy lại lệnh đó hoặc phím `r`; `stop` kết thúc nó (`stopped`) như Ctrl+C. Không có gì đang chạy thì báo "Không có clone nào đang chạy", mã 1 |
| `tgmirror backup [<src>] [<dir>]` | Lưu một nguồn ra thư mục `<dir>` trên đĩa (phase 11a, xem "Backup ra đĩa" dưới), ngay bây giờ, trong terminal này. Thiếu tham số thì wizard hỏi (giống `clone`). Chạy lại cùng thư mục là delta (tiếp từ id lớn nhất đã lưu) |
| `tgmirror restore [<dir>] [--dst/--dst-new]` | Phục hồi một thư mục backup lên một kênh (phase 11b, xem "Restore từ đĩa lên kênh" dưới), ngay bây giờ, trong terminal này. Thiếu tham số thì wizard hỏi (giống `backup`). Luôn tải lên lại; `tgmirror run`/`retry` tiếp tục một restore bị dừng như bất kỳ lần chạy nào khác. `--fresh` (Phase 15b): quên tiến độ của cặp và gửi lại từ đầu |
| `tgmirror inspect [<src>]` | Đếm một nguồn có gì và một filter chọn gì, **không ghi gì** lên Telegram (phase 18a, xem "Khám phá nguồn" dưới). Nguồn là kênh đã join hoặc một thư mục backup (đọc offline, không kết nối). Cùng cờ lọc với `clone`/`backup`/`restore`; `--by` (bảng nhóm theo), `--list`/`--limit`, `--json`, `--csv FILE`, `--save FILE [--freeze]`, `--plan clone\|backup\|restore` (pre-flight); chỉ mục cục bộ (phase 18b): `--refresh`, `--offline`, `--no-index`, và `--quick` (số đếm của Telegram, một request) |
| `tgmirror delete [<src>]` | Xoá tin khỏi một kênh/nhóm **bạn quản trị** (Phase 20, D10, xem "Xoá tin tại chỗ" dưới). Luôn plan trước, rồi gõ lại số tin để xác nhận. Cờ lọc như `clone` + `--selection`/`--exact`; `--all` (cho phép filter rỗng = mọi tin), `--yes --expect N`, `--resume O<n>`, `--force-takeover`, `--pushdown/--no-pushdown` |
| `tgmirror history [n]` | Nhật ký các lần chạy, **các lần backup** (T1, Phase 15b) **và các thao tác `delete`** (Phase 20), mới nhất trước, trộn theo thời gian: mã (`R<n>` lần chạy, `B<n>` backup, `O<n>` thao tác — ba dãy số độc lập; `history O3` xem chi tiết một thao tác), lúc bắt đầu, nguồn → đích (backup: nguồn → thư mục), trạng thái, số tin đã sao chép/lỗi/bị filter loại (`--limit`, `--json`, JSON có `"kind": "run"\|"backup"`). `history n`: chi tiết một **lần chạy** (backup không có số riêng để tra ở đây — thư mục của nó là toàn bộ hồ sơ, `tgmirror status` xem được lần đang chạy): thời gian, khoảng tin nguồn, filter (lần thử lại: "Thử lại: tin lỗi của lần chạy n" thay cho hai dòng đó), số tin đã xóa ở nguồn, tin lỗi kèm lý do (tối đa 20), các lần Telegram giới hạn (flood), mỗi lần kèm loại, thời gian chờ, phương thức và (từ Phase 19d1) làn `[forward -> đích, 120/1h 900/24h]` = lời gọi bị chặn, đích, và số tin làn đó đã gửi trong 1 giờ/24 giờ qua (`--json`: `action`, `destination`, `sent_1h`, `sent_24h`) |
| `tgmirror retry [n]` | Thử lại các tin còn `failed` của lần chạy `n` (mặc định lần gần nhất; xem "Thử lại tin lỗi" dưới). Là một lần chạy riêng (có trong `history`, pause/stop được) trong foreground của terminal này. `--force-takeover`, `--wait`, `--from-backup THƯ_MỤC` như `run`. Không có tin lỗi nào thì báo và thoát mã 0, không kết nối Telegram |
| `tgmirror status` | Tiến độ, tốc độ, ETA, số lỗi, delay hiện tại và số lần Telegram giới hạn trong 24 giờ của **lần đang chạy** (không có thì lần/backup gần nhất — T1, Phase 15b: một backup đang chạy hoặc gần nhất giờ cũng hiện được, trước đây `status` chỉ nhìn `runs`). Một backup không có ETA/phần trăm (không phân tích tổng số trước, giống màn hình tiến độ của nó trong menu) — chỉ tốc độ và số đã lưu. Chỉ đọc DB, không kết nối Telegram nên chạy được từ terminal thứ hai trong lúc clone/backup đang giữ session. `--json` (có `"kind": "run"\|"backup"`). Xem "Xem tiến độ" dưới |
| `tgmirror limits [--json]` / `limits reset [--dst ĐÍCH \| --all]` | (Phase 19d4) Hiện từng làn ghi theo đích: ngân sách (tin/cửa sổ), số đã gửi trong cửa sổ, `bị chặn ở` (`bad`), `chạy sạch tới` (`ok`), lần kiểm chứng cuối và nhịp; làn chưa gặp flood dài ghi "chưa học". `reset` quên giới hạn đã học của các làn ghi vào một đích (id, hoặc tên trong `limits`) hay mọi đích (`--all`), **không hỏi** (chủ động); nhịp đã học giữ nguyên. Chỉ đọc/ghi DB, không kết nối Telegram; từ chối (mã 2) khi có lần chạy/backup đang giữ limiter ở nơi khác vì nó sẽ ghi đè |
| `tgmirror config get [KEY]` / `config set KEY VALUE` | Xem (mọi `[limits]` + đường dẫn, hoặc một khóa, `--json`) / sửa một khóa `[limits]`. Không kết nối Telegram; `get` không tham số giống `config get`. `set` kiểm tra giá trị (kể cả luật liên trường như `max_delay >= min_delay`) trước khi ghi, giữ nguyên phần còn lại của `config.toml` (comment, `api_id`/`api_hash`) như `login` đã làm |
| `tgmirror doctor` | Kiểm tra: session hợp lệ, cryptg đã cài, quyền kênh đích, quyền thư mục `sessions/` (POSIX, T5 Phase 15b — cảnh báo nếu rộng hơn 0700, kiểm **trước** khi bất cứ gì trong lệnh kịp tự siết lại), cảnh báo an toàn |
| `tgmirror appdata export <file.zip>` / `appdata import <file.zip> [--yes]` | Xuất/nhập `tgmirror.db` (bản chụp `VACUUM INTO`) + `config.toml` (đã bỏ `api_id`/`api_hash`) để chuyển sang máy khác (Phase 10, "06-lo-trinh.md"). Không có gì là secret trong gói: không session, không credential, không `tmp/`. `export` từ chối khi có lần chạy **hoặc lần backup** đang sống (mã 2, T1 Phase 15b: trước đây chỉ xét lần chạy); `import` kiểm checksum + phiên bản schema trước khi đụng gì, rồi dời dữ liệu cũ (nếu có) sang `*.bak-<thời điểm>` thay vì xóa — máy sạch thì không hỏi gì, máy đã có dữ liệu thì cần `--yes` hoặc trả lời có ở terminal (không thì mã 2). Sau khi nhập: chạy `tgmirror login` |

Tùy chọn chung: `--version`, `--debug` (hiện traceback thay vì một câu lỗi).

**Gọi tên một kênh** (`<src>` của `inspect`/`topics`/`backup`, `--src`/`--dst` của `clone`; chỉ các chat tài khoản đã join): `@username` (trong PowerShell phải có nháy kép, hoặc gõ username trần), id có hoặc không có tiền tố `-100` (`-1002428805566`, `1002428805566`, `2428805566`), hoặc tên đúng (không phân biệt hoa/thường, phải duy nhất) — `engine/endpoints.py::find_channel`. Link `t.me/...` chưa nhận. Một id bắt đầu bằng `-` làm **tham số vị trí** bị parser đọc là cờ: lệnh dùng `cli/refs.py::SourceCommand` đổi lỗi "No such option: -1" thành câu hướng dẫn (bỏ dấu trừ, hoặc đặt `--` trước id); help của tham số dùng chung `SOURCE_HELP`. Mỗi lệnh có `<src>` vị trí khác nhau thì phải dùng `cls=SourceCommand` khi đăng ký ở `cli/app.py`.

Mã thoát: `0` ok, `1` lỗi chung, `2` dùng sai (kể cả filter sai: cờ, file YAML, regex; kiểm tra trước khi hỏi hay ghi gì), `3` lần chạy dừng vì flood/peer_flood/hết ngân sách học được của một làn (hoặc `clone`/`run` bị từ chối vì phải chờ), `4` thiếu quyền (kể cả nguồn cấm forward), `130` Ctrl+C hoặc SIGTERM/`docker stop` (Phase 12; đã lưu, lần chạy `stopped`; `inspect` dừng giữa chừng in báo cáo của phần đã đọc rồi thoát 130). `clone`/`run`/`retry`/`backup`/`restore` trả mã của lần chạy (`0` xong hoặc dừng bằng phím `q`/`tgmirror stop`, `3`, `130`, ...).

## Giao diện full-screen (menu)

Gõ trơn `tgmirror` (không lệnh con) khi có terminal thật mở thẳng một app full-screen (`rich.live.Live(screen=True)`, khung header/footer cố định) thay vì gõ từng lệnh — vòng lặp menu chỉ sống trong đúng tiến trình đó, từ lúc mở tới lúc thoát, không có gì chạy nền hay chạy tiếp sau khi thoát. Không có terminal (chuyển hướng, script, CI) thì gõ trơn vẫn in help như trước.

Điều hướng: ↑/↓ chọn, Enter chọn/xác nhận, Esc lùi lại một bước (menu chính thì không có gì để lùi). Menu chính khi đã đăng nhập: Sao chép mới, Khám phá, Backup, Restore, Chạy tiếp, Thử lại tin lỗi, Trạng thái, Lịch sử, Kênh đã join, Tài khoản, Cấu hình, Thoát — hai mục "Chạy tiếp"/"Thử lại tin lỗi" chỉ hiện khi đã có ít nhất một cặp nguồn/đích. "Backup"/"Restore" (Phase 15a, 2026-09-27) là đúng `BackupFlow`/`RestoreFlow` của dòng lệnh vẽ trong khung, y hệt cách "Sao chép mới" đã dùng `CloneFlow`: "Backup" vào thẳng màn hình tiến độ riêng của nó (`BackupScreen`, vì tiến độ một backup là kiểu `Backup` chứ không phải `Run`); "Restore" vào thẳng màn hình tiến độ của `clone`/`run` (`RunScreen`) vì một restore vẫn là một `Run` bình thường, chỉ đọc từ thư mục backup thay vì gateway sống. "Chạy tiếp"/"Thử lại tin lỗi" chỉ hỏi chọn cặp khi có hơn một cặp (danh sách trong khung, không phải câu hỏi kiểu wizard), rồi vào thẳng màn hình tiến độ — đúng khung tiến độ đã có ở "Điều khiển khi đang chạy" (dưới), phím `p`/`r`/`q` hoạt động y hệt, Ctrl+C giữa lúc này dừng run **và** thoát hẳn app (không lùi về menu). "Trạng thái" tự làm mới vài giây một lần (giống `tgmirror status` nhưng sống, gồm cả một backup đang chạy/gần nhất — T1, Phase 15b); "Lịch sử" là danh sách cuộn được, trộn cả lần chạy lẫn backup (đánh dấu loại), Enter xem chi tiết — một lần chạy ra chi tiết đầy đủ, một backup ra bản tóm tắt gọn hơn (`BackupHistoryDetailScreen`, không có tin lỗi/flood để tra như một lần chạy); "Kênh đã join" gõ để lọc và cuộn theo mục đang chọn (như danh sách nguồn ở wizard — sửa 2026-09-27, trước đó ↑/↓ không làm gì vì màn hình chỉ có ô lọc, không có danh sách chọn được); "Tài khoản" xem ai đang đăng nhập và đăng xuất (có hỏi lại). Badge "đang chạy ở nơi khác" (đầu màn hình) cũng xét cả một backup đang chạy ở tiến trình khác, không chỉ một lần chạy.

Chưa đăng nhập (hoặc chưa lưu `api_id`/`api_hash`), app vẫn mở, với menu rút gọn: Đăng nhập, Trạng thái, Lịch sử, Cấu hình, Thoát.

**Sao chép mới** là đúng wizard của `tgmirror clone` không kèm cờ (cùng câu hỏi, cùng luật, cùng cảnh báo và xác nhận, kể cả câu hỏi D3), vẽ trong khung: danh sách dài gõ để lọc và cuộn theo mục đang chọn, hộp chọn nhiều dùng Space, các câu đã trả lời hiện mờ phía trên. **Esc lùi một câu hỏi**, kể cả sang bước trước (câu trả lời cũ được chọn sẵn); Esc ở câu đầu tiên, hoặc trả lời "Không" ở một câu xác nhận, bỏ wizard và về menu mà không ghi gì. Một lựa chọn bị từ chối (ví dụ tên kênh trống, đích không hợp lệ) hiện lý do và hỏi lại bước đó. Xác nhận xong thì vào thẳng màn hình tiến độ; Esc từ đó về menu chính.

**Khám phá** (Phase 18b): chọn một nguồn (đúng câu hỏi nguồn của wizard clone) rồi một màn hình bốn bảng cạnh nhau — loại media, tháng, hashtag và một bảng đổi bằng Tab (nguồn forward, domain, topic, người gửi) — trên chỉ mục cục bộ của nguồn (dựng hay cập nhật trước, có tiến độ; `q`/Esc dừng và dùng phần đã có). ←→ chọn cột, ↑↓ chọn dòng, **Enter** thêm dòng đó vào bộ lọc (cùng một rule, AND; chọn lại cùng loại thì thay), **x** loại trừ nó, **Backspace** bỏ bước lọc cuối; đầu màn hình luôn ghi bộ lọc hiện tại và số tin/dung lượng/album khớp. Mỗi bước là đếm lại trong bộ nhớ, không gọi Telegram. Dòng không thành bộ lọc được (tên forward ẩn, bài đăng của kênh không có người gửi, loại trừ một tháng) báo lý do và không đổi gì. Phím hành động: **l** danh sách tin khớp (cuộn được), **s** ghi `@last` kèm id (dùng ngay với `--selection @last`), **c** / **b** mở wizard clone / backup với nguồn và bộ lọc đã điền (bước filter không hỏi lại; đích, D3, xác nhận giữ nguyên), **u** đọc tin mới vào chỉ mục, **R** đọc lại cả nguồn, Esc thoát.

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
                           file YAML" (đường dẫn gõ Tab để gợi ý như trên dòng lệnh, `Prompter.path`); với cặp đã
                           clone còn có lựa chọn đầu "Giữ filter của lần chạy trước"; nguồn đã `inspect` rồi thì
                           có thêm "Dùng kết quả khám phá gần nhất của nguồn này (N tin, giờ)" (Phase 18a, filter của
                           `@last`, đánh giá sống; "file YAML" nhận cả file selection). Trả lời
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

Khi `clone`/`run` đang chạy (foreground): có terminal thật thì hiện TUI (Rich Live, xem dưới), phím đã hoạt động và hiện ngay trong khung nên không có dòng nhắc riêng. Không có terminal (chuyển hướng, chạy từ script, test, hay bên trong container Docker của Phase 12) thì phím cũng không hoạt động, và tiến độ là dòng chữ thường (không ANSI, dùng được khi chuyển hướng): `Lần chạy 3: 4,180 tin đã sao chép, 3 lỗi (tin nguồn tới id …)` tối đa một dòng mỗi 5 giây (thêm số tin bị filter loại khi có, và một dòng tổng kết cuối), cộng các thông báo (reconcile, flood, tạm dừng) và một dòng kết quả. SIGTERM (hàng dưới) không cần terminal — chỉ là một tín hiệu OS — nên nó vẫn hoạt động trong trường hợp này, khác với phím.

| Cách | Tác dụng |
|---|---|
| Phím `p` | **Tạm dừng tại chỗ**: xong batch hiện tại (nếu đang chờ FloodWait thì sau lượt chờ), lưu, rồi giữ — tiến trình vẫn sống, giữ terminal, ghi heartbeat, không gửi gì |
| Phím `r` | Chạy tiếp sau khi tạm dừng |
| Phím `q` | Dừng: xong batch hiện tại, lưu, thoát (lần chạy `stopped`, mã 0) |
| Ctrl+C | Như `q` nhưng thoát mã 130; lần hai thoát ngay (batch dở dang được xử lý theo quy tắc reconcile ở `04-state-checkpoint.md`) |
| SIGTERM (Phase 12: `docker stop`) | Y hệt Ctrl+C — lần đầu như `q` (mã 130), lần hai thoát ngay; cùng chia sẻ một `Interruption` với SIGINT (`cli/interrupt.py::stop_on_interrupt`) nên một SIGTERM rồi một Ctrl+C tính là "lần hai" chứ không phải hai lần đầu |
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

### Transform (`--transform FILE`, Phase 19a)

`--caption` là cú pháp tắt của một **danh sách thao tác** chạy theo thứ tự; `--transform` nhận danh sách đầy đủ từ một file YAML (hoặc từ phần `transform:` của file selection, `03-filters.md`, "Selection"):

```yaml
version: 1
caption:
  - strip_links: source        # source | all — `source` = đúng `--caption strip-links` (link) …
  - remove_mentions: source    # … cộng `@nguồn`; `all` = mọi @mention
  - remove_hashtags: ["#ads", "quangcao"]   # hoặc: all
  - replace: {regex: "(?i)liên hệ @\\w+", with: ""}   # `with` là chữ nguyên văn, không có \1
  - append: "— Kênh B"         # cách hai dòng trống, như `--caption append`
  - clear: true                # bỏ caption, như `--caption none`
  - text_too: true             # mặc định chỉ caption của tin media; true = cả tin chữ
topics:                        # Phase 19b: vào topic nào, và gọi topic là gì
  hashtag_to_topic:            # đích phải là forum
    map:                       # theo thứ tự: tin vào topic của rule ĐẦU TIÊN mà hashtag của nó khớp
      "#python": "Python"
      "#rust": "Rust lang"
    auto: ["#go", "#machine_learning"]   # tên topic lấy từ hashtag: "Go", "Machine learning"
    # auto: all                  # hoặc: MỌI hashtag tự thành một topic (xem dưới); kèm limit/skip
    # limit: 30                  #   tối đa 30 topic mới mỗi lần chạy (mặc định), quá thì vào General
    # skip: ["#ads"]             #   hashtag không tạo topic
    default: "Khác"            # không rule nào khớp VÀ tin không nằm trong topic nguồn nào
    strip: true                # bỏ hashtag đã dùng để định tuyến khỏi caption
  topic_map:                   # topic nguồn (tên không phân biệt hoa thường, hoặc id) -> tên topic đích
    "Chat cũ": "Chat"          # nhiều topic về một tên = gộp
    12: "Chat"
  hashtag_format: "#t_{slug}"  # đích không phải forum + --topic-as-hashtag: dạng hashtag thay topic
```

- Một khóa mỗi mục; `text_too` là mục đặc biệt (một lần, không phải thao tác). Khóa lạ, `version` mới hơn, thao tác lạ, regex sai, hashtag có khoảng trắng, `clear` cùng `text_too` (xóa sạch mọi tin chữ), một khóa của `topics` (`hashtag_to_topic:`, `topic_map:`, `hashtag_format:`) để **trống** (viết dở: YAML đọc là `null`, trước đây lặng lẽ thành transform rỗng nên clone chạy như không có gì), hay một file `--transform` mà rốt cuộc không đổi gì (không thao tác caption, không rule/tên topic) → mã 2 trước khi hỏi hay ghi gì, nêu đúng vị trí (`caption[2].replace.regex: ...`). Các khóa của lát cắt sau của Phase 19 (`reply`, `forward_header`, `links`) bị từ chối với thông báo riêng, không bị lờ đi.
- Dùng chung một đường code với `--caption`: cùng viết lại entity theo UTF-16, cùng chiến lược theo unit (`strategy.router`): chỉ unit mà thao tác có thể đổi (tin media có caption; với `text_too` cả tin chữ) bị gửi lại — bằng mã file khi nguồn cho lưu nội dung, tải xuống/lên khi `noforwards` — phần còn lại vẫn forward. Router không biết trước một thao tác có đổi gì không, nên tin có caption mà thao tác không khớp vẫn bị gửi lại giống nhau (đúng như `--caption append` trên tin đã có caption). D3 áp như `--caption`: `--yes-i-administer-this-channel` / câu gõ nguyên văn.
- **`topics.route` (Phase 21)** thay `hashtag_to_topic` bằng luật tổng quát (`hashtag_to_topic` vẫn chạy, là cú pháp tắt). Mỗi luật: `when` (điều kiện, **đúng ngôn ngữ filter**, xem `03-filters.md`), `from` (`hashtags` | `text` | `file_name` | `topic` | `forwarded_from`), `regex`, `pick: first|last`, `skip`, `to` (mẫu tên). Luật đầu tiên cho ra tên không rỗng thắng; một tin (một unit) một topic. Cạnh `route`: `default`, `limit` (chỉ có nghĩa khi có tên có biến), `strip: matched`, `topic_map`, `hashtag_format`.

  ```yaml
  topics:
    route:
      - when: {hashtag: ["#vip"]}
        to: "VIP"
      - from: hashtags                  # ca 1: lấy hashtag CUỐI, bỏ hashtag quảng cáo
        pick: last
        skip: ["#YYLive"]
        to: "{value|tag_title}"
      - from: text                      # ca 2: tên file ghi trong caption
        regex: '(?m)^(?P<name>[^\s.]+?)(?:[_-]\d+)*\.(?:mp4|mkv|mov|jpe?g)\s*$'
        to: "{name|title}"
    default: "Khác"
    strip: matched                      # bỏ hashtag / đoạn regex đã dùng khỏi caption
  ```

  Với `from: text` mỗi lần khớp regex là một ứng viên (`{value}` = `{0}` = đoạn khớp); với nguồn khác regex chỉ giữ ứng viên khớp và cho nhóm (`{value}` vẫn là ứng viên nguyên vẹn). Bộ lọc tên cố định: `title`, `lower`, `upper`, `tag_title`, `slug`; không có biểu thức hay bộ lọc tự định nghĩa. Mọi luật được kiểm khi nạp file (regex biên dịch được, `{nhóm}` có thật, bộ lọc có thật) → mã 2. **Tên là nhãn**: đích forum → topic cùng tên (tìm theo tên trước khi tạo); đích **không** phải forum → hashtag `#{slug}` (theo `hashtag_format`) gắn vào caption/tin chữ, nên unit có nhãn bị gửi lại (D3 như mọi tin gửi lại; `--mode copy` là mã 2) — cùng một file chạy cho cả hai loại đích. `--dst-new` vẫn tạo forum. `inspect --plan` in bảng topic đích và vài tin **không khớp luật nào** (kèm id và đoạn đầu caption) để sửa regex trước khi ghi gì.
- **`topics` (Phase 19b)**: `hashtag_to_topic` cho tin vào topic theo hashtag — nguồn là kênh thường hay forum đều được; đích không phải forum thì tên topic thành hashtag (Phase 21; trước đó là lỗi mã 2). `--dst-new` tạo đích mới là forum bất kể loại nguồn (kênh broadcast cũng thành forum). Hashtag so không phân biệt hoa thường; một tin nhiều hashtag vào topic của rule **đứng trước** trong danh sách (không phải hashtag đứng trước trong tin); `auto` thêm rule sau `map` với tên topic suy từ hashtag (`#machine_learning` → `Machine learning`). **Định tuyến không chọn tin**: một danh sách hashtag chỉ nói tin *đi đâu*; tin không khớp hashtag nào vẫn được clone — vào `default` nếu có (và tin không nằm trong topic nguồn nào), không thì vào General (hoặc theo topic nguồn của nó). Muốn **chỉ lấy** vài hashtag thì đó là việc của filter: `--hashtag "#python" --hashtag "#rust"` (hoặc `filter:` trong file selection) — có pushdown, `inspect` đếm đúng, đổi thì quét lại. Topic đích được **tìm theo tên** trong các topic đích đang có (không phân biệt hoa thường; `General` là topic 1) rồi mới tạo, nên chạy lại, hay hai nguồn vào cùng một forum, dùng chung topic. Album không bao giờ bị tách. Thứ tự **trong từng topic đích** là thứ tự nguồn; thứ tự giữa các topic khác nhau thì không có nghĩa.
- **`--hashtag-to-topic`** (cờ của `clone`/`restore`/`inspect --plan`, Phase 19b) = `auto: all` mà không cần viết file: mỗi tin vào topic đặt theo **hashtag đầu tiên viết trong tin** (`#machine_learning` → `Machine learning`), tạo khi gặp lần đầu. Tin không có hashtag vào General (hoặc `default`). Cờ **không** chọn tin: kết hợp với `--hashtag` nếu chỉ muốn một số hashtag. Kèm `--transform` thì cờ chỉ bổ sung: rule `map` trong file vẫn đặt tên và thắng, cờ lo phần hashtag còn lại. Vì không biết trước có bao nhiêu hashtag, mỗi lần chạy chỉ **tạo tối đa `limit` topic mới** (mặc định 30; một topic đã có luôn được dùng): quá thì tin có hashtag mới đi vào General và có một dòng thông báo (`run.topics_limit`). Tăng `limit` hoặc loại hashtag nhiễu bằng `skip` trong file. Một tin nhiều hashtag chỉ vào **một** topic (hashtag đầu), nên cách này hợp với kênh mà mỗi bài có một hashtag chủ đề; kênh gắn nhiều hashtag chung chung (`#news #hot`) thì nên liệt kê `map`/`skip`.
- `strip: true` bỏ đúng hashtag đã định tuyến tin (các hashtag khác giữ) khỏi caption — là thao tác caption nên unit đó bị gửi lại (bằng mã file hoặc tải xuống/lên, D3 như trên), tin chữ không đổi trừ khi `text_too`. Không có `strip` thì định tuyến một mình chỉ là forward vào topic: không tải gì, không cần D3 thêm.
- `topic_map` **chỉ đổi tên**, không chọn: topic nguồn không có trong đó vẫn được clone dưới tên của nó (muốn chỉ lấy vài topic thì dùng filter `--topic`). Cùng nguyên tắc: *chọn* tin là filter, *sửa/định tuyến* là transform.
- `topic_map` đổi tên topic đích của một forum nguồn (và là tên hashtag nếu đích không phải forum); khóa là id hay tên topic nguồn, giá trị rỗng/`null` (bỏ topic) bị từ chối — dùng filter `exclude`. Không đổi được tên của General (tin ở đó không có id topic). `hashtag_format` bắt buộc có `{slug}` và phải ra đúng một hashtag.
- **Cờ cho phần còn lại của `topics` (Phase 19b1)**, trên `clone`/`restore`/`inspect --plan`, để luồng cờ đủ dùng không cần file:
  - `--topic-map "Cũ=Mới"` (lặp được) = một mục `topic_map`: bên trái là **tên** topic nguồn (không phân biệt hoa thường, bỏ khoảng trắng hai đầu) hoặc **id** (chỉ gồm chữ số); tách ở dấu `=` **đầu tiên**, nên tên topic nguồn có `=` thì viết trong file. Cờ **thêm** vào `topic_map` của file và thắng khi cùng một topic; hai cờ cùng một topic là lỗi "appears twice", như trong file. Vẫn chỉ đổi tên, không chọn tin.
  - `--topic-default TÊN` = `default` của `hashtag_to_topic` (cờ **thay** giá trị của file).
  - `--strip-routing-hashtag` = `strip: true` (chỉ bật, không có cờ tắt: file là nền, cờ chỉ thêm).
  - Hai cờ sau chỉ có nghĩa khi **có định tuyến** (`--hashtag-to-topic`, hoặc `hashtag_to_topic` trong file): thiếu (không có `route`/`hashtag_to_topic` nào) thì mã 2 (`err.transform`) thay vì bị lờ đi, như `limit`/`skip` không có `auto: all`. Giá trị sai (tên rỗng, hai dòng, topic 1) cũng mã 2 trước khi hỏi hay ghi gì.
  - **Chưa có cờ** cho `limit`/`skip` của `auto: all` và `hashtag_format` (chỉ trong file; mặc định `limit` 30 đủ cho ca thường), và **không có bước wizard** cho transform (luật 7: cờ là tập cha của wizard; xem 06, 19a mục 7). "Top N hashtag đếm từ nguồn" thay bằng `auto: all` + `limit`: không quét cả nguồn trước.
- **Cờ chồng lên file** (Phase 19b, thay cho "không ghép" của 19a): file (`--transform`, hay `transform:` của `--selection`) là nền, cờ đặt lên trên: giá trị đơn thì cờ **thay** (`--hashtag-to-topic` bật `auto: all`, `--topic-default` đặt `default`), tập thì cờ **thêm** (`--topic-map`), danh sách thao tác caption thì thao tác của cờ (`--caption`/`--caption-text`) chạy **sau** các thao tác của file. Với filter xem `03-filters.md`, "Cờ chồng lên file". Vẫn là lỗi mã 2: `--transform` cùng `--selection` có `transform:` (hai file, không biết file nào là nền); `--mode copy` với transform có thao tác caption hay `strip` (định tuyến một mình thì được: forward vào topic). `backup` từ chối file selection mang transform (nó không áp transform); bước "nạp từ file" của wizard cũng từ chối và chỉ sang `--selection`.
- **Giữ reply (Phase 19c1)**: `reply: keep` trong file, hay cờ `--keep-replies` (trên `clone`/`restore`/`inspect --plan`, chỉ bật, đặt lên trên file như các cờ `topics`): một tin trả lời tin khác thì trả lời **bản sao** của nó ở đích, tìm qua `msg_map` của cặp (nên bản sao do lần chạy trước tạo ra cũng tính). Forward không mang reply, nên các tin đó bị gửi lại (bằng mã file, hoặc tải xuống/lên khi `noforwards`, D3 như mọi biến đổi; `--mode copy` kèm theo là lỗi mã 2). Tin được trả lời không có bản sao (bị filter loại, lỗi, ngoài phạm vi) → tin vẫn được gửi, **không kèm reply**, và dòng kết quả đếm số tin đó. Poll/game/invoice vẫn forward (gửi lại làm mất phiếu hoặc không gửi được), nên mất reply. Có định tuyến hashtag thì reply chỉ giữ khi cả hai tin vào cùng một topic đích.
- **Giữ header "Forwarded from" (Phase 19c2)**: `forward_header: keep` trong file, hay `--keep-forward-header` (trên `clone` và `inspect --plan`; không có trên `restore`): tin được forward (đường A) mang header của nguồn như một forward thường, thay vì forward không tác giả (D2, mặc định). Header chỉ có trên forward, nên **không đi cùng** thứ gì buộc gửi lại: thao tác `caption`, `strip`, `reply: keep`, `--mode reupload`, `--caption` khác keep, `--topic-as-hashtag` → mã 2 trước khi tạo hay ghi gì; `restore` luôn tải lên lại nên file có `forward_header: keep` cũng bị từ chối ở đó. Định tuyến hashtag và `topic_map` đi cùng được (vẫn là forward vào topic). Nguồn `noforwards` không forward được như trước.
- **Link nội bộ (Phase 19c3)**: `links: rewrite` trong file, hay `--rewrite-links` (trên `clone`/`restore`/`inspect --plan`, chỉ bật, đặt lên trên file như các cờ trên): một link `t.me/<nguồn>/<id>` (hay `t.me/c/<số>/<id>`, có thể kèm topic) trong caption hoặc tin chữ thành link tới **bản sao** của tin đó ở đích, tìm qua `msg_map` của cặp (bản sao do lần chạy trước tạo ra cũng tính). Chỉ link lùi: link tới tin chưa có bản sao (tin sau, hoặc bị filter loại) giữ nguyên và dòng kết quả đếm số link đó. Hyperlink giữ chữ, link hiển thị sửa trong text; chạy **trước** các thao tác caption, và áp cho cả tin chữ không cần `text_too`. Sửa text nên tin mang link bị gửi lại (như `reply: keep`: D3, `--mode copy` kèm theo là mã 2; không đi cùng `forward_header: keep`). Link tới chat khác không đổi.
- Có trên `clone` và `restore` (cùng đường code: restore đọc từ thư mục backup nhưng viết lại caption bằng cùng `rewrite_caption`), và trên `inspect --plan clone|restore` để pre-flight đếm đúng số unit đi từng đường; `inspect --plan ... --save` ghi transform vào file selection.
- Nhớ theo cặp (`RunOptions.transform_json`): `run`/`retry` dùng lại; không có cờ nào để "bỏ" nó ở `run` (clone lại với transform khác). Tin đã sao chép **không** bị sửa lại khi đổi transform.
- `text_too` chạm tin chữ: tin chữ bị xóa sạch sau thao tác (vd. `replace` thay cả tin bằng rỗng) → tin đó `failed` với lý do `transform_emptied_text` (Telegram không gửi được tin rỗng); `retry` không giúp, sửa transform rồi chạy lại. Regex quá `0.5 s` trên một tin → tin đó `failed` với `transform_regex_timeout`.

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
daily_cap = 5000       # chỉ nhắc khi vượt trong 24 giờ, không chặn (Phase 19d2)
short_flood = 60       # FloodWait dài hơn (giây) là hạn mức: đặt ngân sách của làn
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

## Giới hạn học được (`limits`, `--reset-limits`, Phase 19d4)

`--reset-limits` (trên `clone`, `run`, `retry`) quên giới hạn các làn ghi vào đích của lần chạy trước khi nó bắt đầu, rồi in một dòng nói đã quên mấy làn; làn quay về "chưa học" (không gì chặn cho tới flood dài kế tiếp). Cờ này **chỉ của một lần chạy** (`RunOptions.reset_limits`, không vào `for_pair`): không tạo cặp mới, không đổi `filters_json` hay con trỏ, và lần chạy sau không tự quên lại. Không hỏi lại, và `--yes` không liên quan. Wizard/menu chưa có bước riêng cho nó (câu hỏi tự động thuộc phần sau 0.2.0). `[limits] probe_after_hours` (mặc định 168) là thời gian `bad` còn chặn tăng trưởng theo đồng hồ; xem `05-chong-flood.md`, "Thăm dò giới hạn". `tgmirror status` thêm một dòng `Giới hạn:` cho mỗi làn đã biết `bad`/`ok` và `--json` có `ok`, `bad`, `checked_at` trong `budgets`.

## Xem tiến độ (`status`)

```
Lần chạy 7: Kenh A → Kenh A (copy)
Trạng thái:  đang chạy
Tiến độ:     ~43% (32100 / tối đa 74500 tin)
Tốc độ:      2.1 tin/giây (trung bình từ lúc bắt đầu), còn khoảng 44 phút
Kết quả:     4180 đã sao chép, 3 lỗi, 27911 bị filter loại
Cap ngày:    còn tối đa 42400 tin, cap 5000/ngày: cần nghỉ thêm khoảng 8 ngày
Giới hạn:    nghỉ 2.4s giữa các lần gửi; đã gửi 1200 tin trong 24 giờ qua (ngưỡng nhắc 5000)
Ngân sách:   forward: 800 tin/1 giờ (đã gửi 310); đặt sau flood 3600s lúc 2026-10-15 14:02
Telegram:    2 lần bị giới hạn trong 24 giờ qua; gần nhất 38 giây trước (flood_wait, 30s)
```

Mọi số là **ước lượng**: `status` chỉ đọc DB (clone đang chạy giữ session Telegram nên terminal thứ hai không kết nối được), vì vậy tổng được ghi một lần lúc lần chạy bắt đầu: số tin Telegram đếm được cho khoảng và filter của lần chạy (`RunOptions.total_items`, một request `count`, xem `01-kien-truc.md`, "Analyze và tiến độ"), cùng id tin mới nhất của nguồn (`src_last_id`) làm phương án dự phòng.

- **Tiến độ** của lần chạy thường là số tin đã xử lý (sao chép, lỗi, bị filter loại, bị bỏ vì không hỗ trợ, hoặc đã có sẵn ở đích) trên `total_items`. Đó là **cận trên** (tin service được đếm, filter phía client không được trừ trước) nên lần chạy bỏ qua nhiều tin chỉ tới 100% khi xong; chữ "tối đa" nói điều đó. Lần chạy từ trước khi có `total_items` (hoặc phép đếm lỗi) tính theo *id* nguồn như trước (id có khoảng trống, filter nhảy nhanh qua quãng không khớp nên chỉ là tỉ lệ thô). Tiến độ của `retry` chính xác: số tin lỗi đã xử lý trên số còn chờ.
- **Ngân sách**: mỗi làn ghi vào đích của lần chạy có một dòng (số tin/cửa sổ, đã gửi trong cửa sổ, flood đã đặt ra nó), hay "chưa học" khi chưa gặp flood dài nào. Khi số tin còn lại vượt phần ngân sách còn trong cửa sổ, `status` (và một dòng đầu lần chạy) nói cần nghỉ thêm bao nhiêu ngày; chỉ với làn đã có ngân sách, và chỉ cho lần chạy đang sống hoặc đang `waiting_flood`. JSON: `limiter.sent_24h`, `limiter.daily_cap`, `limiter.budgets[]` (`action`, `budget`, `window_hours`, `sent`, `flood_seconds`, `flood_at`).
- **Tốc độ** là trung bình từ lúc bắt đầu (gồm cả lúc tạm dừng và chờ flood): tin đã sao chép hoặc lỗi mỗi giây; chưa hiện trong 5 giây đầu. **ETA** ngoại suy từ đó và chỉ hiện khi lần chạy thật sự đang `running`.
- Lần chạy ghi `running`/`paused` nhưng heartbeat cũ hơn 2 phút được báo là **không có tiến trình nào giữ** (tín hiệu cuối lúc ...); lần chạy kế tiếp của cặp ghi nó `failed('interrupted')` (`04-state-checkpoint.md`).
- `waiting_flood`: hiện thời điểm được chạy lại. Lần chạy đã kết thúc mà còn tin lỗi: gợi ý `tgmirror retry`.
- Số lần Telegram giới hạn trong 24 giờ là của cả account (`flood_log`); dòng "Giới hạn" đọc `limiter_state` (delay cao nhất của các làn ghi) và các ô 5 phút `sent_buckets` (số tin cả account gửi trong 24 giờ qua, cửa sổ trượt, không còn "hôm nay" theo giờ máy).
- `--json`: `run`, `status`, `live`, `abandoned`, `progress`, `speed_per_second`, `eta_seconds`, `copied`, `failed`, `failed_now`, `gone_from_source`, `total_items`, `handled`, `left`, `cap_rest_hours`, `cap_rest_days` (= giờ nghỉ làm tròn lên ngày, giữ cho máy đọc cũ), `limiter`, `floods_24h`, `last_flood`, ... (khóa cho máy đọc, không dịch).

### Trong lúc chạy (`clone`/`run`)

Đầu lần chạy có một dòng "Ước tính: tối đa N tin cần xem xét" (và một dòng về cap ngày nếu tốn hơn một ngày). Dòng tiến độ theo batch là `Lần chạy 7: 1200/9800 tin (~12%): 1150 đã sao chép, 50 bị filter loại, 0 lỗi.` (không có tổng thì dạng cũ). Với file ≥ 8 MB (chiến lược B) có thêm dòng riêng: `Tải xuống tin 42: 45% (12.3 MB / 27.4 MB, 3.2 MB/s).` và `Tải lên tin 42: ...`, in lúc bắt đầu, khi tiến thêm 5% (cách nhau ít nhất 5 giây), hoặc sau 30 giây dù chưa tiến nhiều, và khi xong (chưa xong thì không hiện 100%). Giao diện Rich (`ui/tui.py`, đã làm) dùng cùng dữ liệu (`Reporter.progress`, `Reporter.transfer`) cho thanh tiến độ và hai dòng tải xuống / tải lên.

## Làm lại từ đầu (`--fresh`)

Chạy lại cùng cặp nguồn/đích là delta; `--fresh` (trên `clone` và `run`) là "bắt đầu lại": **quên tiến độ của cặp** (mọi hàng `msg_map`, con trỏ), đọc lại tin mới nhất của đích làm `dst_base_id` mới, rồi sao chép lại từ tin đầu tiên. Filter đang nhớ được giữ (đưa filter khác hoặc `--no-filter` nếu muốn đổi). Nhật ký `history` không bị đụng tới.

Nó có thể làm **trùng tin** nếu đích còn các bản sao cũ, nên có hàng rào: khi có tin để quên, một câu hỏi nêu số tin ("Đích đã có N tin từ các lần chạy trước; làm lại từ đầu sẽ sao chép lại tất cả và có thể bị trùng…"); với `clone` đó chính là câu xác nhận duy nhất. `--yes` đồng ý; không có terminal mà thiếu `--yes` thì mã 2 và không quên gì. Cặp chưa có tin nào thì không hỏi. `run --fresh` không nhận lệnh "chạy tiếp lần đang tạm dừng ở terminal khác": cặp đang có lần chạy sống thì báo bị giữ (mã 1) và không quên gì. Chưa làm: chế độ kiểm tra/sửa (chỉ sao chép lại tin còn thiếu ở đích).

Dùng khi đã dọn kênh đích, hoặc thật sự muốn một bản sao thứ hai. Nếu chỉ cần bản sao sạch thì `--dst-new` đã là cặp mới.

## Backup ra đĩa (`tgmirror backup`, Phase 11a)

`tgmirror backup [<src>] [<dir>]` lưu một nguồn vào thư mục `<dir>` (`backup.json` + `messages.jsonl` + `media/`, chi tiết ở `04-state-checkpoint.md` và `01-kien-truc.md`), ngay bây giờ, trong terminal này; Ctrl+C dừng, tiến độ đã lưu trên đĩa. Chạy lại đúng thư mục đó là delta (tiếp từ id lớn nhất đã lưu). `<src>`/`<dir>` là tham số tùy chọn: thiếu cái nào thì wizard hỏi cái đó (giống `clone`).

```bash
tgmirror backup "@ten_kenh" ./backups/ten_kenh --media video,photo --since 2024-01-01 --yes
tgmirror backup   # wizard: chọn nguồn, nhập thư mục, (D3 nếu cần), chọn filter, xem trước, một câu xác nhận
```

- Wizard bước 1-4 giống `clone` (nguồn, thư mục thay vì đích, D3 nếu cần, filter), rồi xem trước và một câu xác nhận "Backup X vào Y ngay bây giờ?" (`--yes` bỏ qua, không bao giờ bỏ qua được lời tuyên bố D3). Câu hỏi thư mục gõ Tab để gợi ý đường dẫn như trên dòng lệnh (`Prompter.path`) — cùng một bộ so khớp cho cả hai: wizard cổ điển (`QuestionaryPrompter`) gọi thẳng `questionary.path` (bọc `GreatUXPathCompleter` của prompt_toolkit); giao diện full-screen menu không có line editor để gắn bộ đó vào nên `ui/menu/prompter.py::complete_path` (Phase 15a) gọi cùng `GreatUXPathCompleter` rồi tự vẽ kết quả (một Tab khớp một mục thì hoàn thành luôn, kèm dấu phân cách nếu là thư mục để Tab tiếp theo đi sâu vào trong; khớp nhiều mục thì hoàn thành tới phần chung dài nhất và liệt chúng dưới ô nhập) — không phải một bộ so khớp tự viết riêng, để không lệch quy tắc (phân biệt hoa/thường, thế nào là khớp) với wizard cổ điển. Thư mục đã có backup từ trước thì wizard **không hỏi lại filter**, chỉ báo "giữ filter đã dùng" (backup không đổi filter được khi tiếp tục, xem dưới); D3 cũng không hỏi lại nếu thư mục đó đã có lời tuyên bố. Một thư mục còn đang chờ hết FloodWait/PeerFlood bị từ chối **trước** câu hỏi filter, như `clone` từ chối một cặp đang bận trước bước 4.
- Cờ lọc giống hệt `clone` (`--media`, `--hashtag`, `--contains`, `--regex`, `--exclude-regex`, `--exclude-media`, `--since`, `--until`, `--min-size`, `--max-size`, `--from-user`, `--topic`, `--album`, `--filter-file`), cộng `--pushdown/--no-pushdown` và `--preview/--no-preview` (giống hệt ý nghĩa ở `clone`). Chỉ đặt được ở lần đầu của một thư mục: đưa filter khác ở lần sau bị từ chối (mã 2) — backup không giữ gì tương đương `msg_map` nên không biết filter cũ đã bỏ qua tin nào, phải dùng thư mục khác nếu muốn đổi.
- **D3 áp dụng y như `--mode reupload`** (backup luôn tải xuống): nguồn `noforwards` cần `--yes-i-administer-this-channel` (tài khoản bất kỳ) hoặc — có terminal, tài khoản là admin — một câu hỏi Có/Không; không phải admin và không có terminal để gõ câu hỏi thì mã 4/2 như `clone`. Lời tuyên bố ghi vào `backup.json`, không phải DB.
- `--force-takeover`: chạy dù thư mục có vẻ đang được một tiến trình khác backup (chỉ khi chắc nó đã chết). `--yes`: bỏ câu xác nhận cuối (không thay được cho D3).
- `tgmirror pause`/`stop` và phím `p`/`r`/`q` hoạt động như với `clone`/`run`.
- **Đã có mục "Backup" trong giao diện full-screen (menu)** (Phase 15a, 2026-09-27, `BackupScreen`): dùng chung `BackupWriter`/`RunControl`, phím `p`/`r`/`q`; tiến độ vẽ bằng `BackupTuiReporter` (không có tổng/ETA, giống dòng chữ thường vì chưa có analyze).
- **Chưa làm**: phân tích/ETA (không có dòng "x trên y"), dòng tiến độ theo từng file đang tải, tải ảnh bìa video, ghi `reply_to`. **Đã nối vào `tgmirror history`/`status` và badge "đang chạy ở nơi khác"** (T1, Phase 15b, 2026-09-27) — mỗi lệnh `backup` vẫn tự in tiến độ/kết quả của chính nó khi đang chạy, nhưng giờ cũng hiện được từ nơi khác qua `history`/`status`. Xem `06-lo-trinh.md`, "Phase 11 — ghi chú".

## Restore từ đĩa lên kênh (`tgmirror restore`, Phase 11b)

`tgmirror restore [<dir>] [--dst <kênh> | --dst-new] [--about ...]` phục hồi một thư mục `tgmirror backup` đã làm lên một đích (có sẵn, hoặc tạo mới — `--dst-new` đặt tên đích thẳng theo tên nguồn gốc ghi trong `backup.json`, không hỏi tên/about như `clone --dst-new`, vì Phase 11a chưa từng ghi `about`/avatar). Đây là một lần `run` bình thường (có trong `history`, pause/stop/`p`/`r`/`q` được, `tgmirror run`/`retry` tiếp tục nó y như một clone bị dừng) — chỉ khác ở nguồn là một thư mục, và luôn tải lên lại (không có "copy phía server" từ một thư mục).

```bash
tgmirror restore ./backups/ten_kenh --dst-new --yes
tgmirror restore   # wizard: chọn thư mục, chọn đích, (D3 nếu cần), chọn filter, xem trước, một câu xác nhận
```

- Wizard bước 1-4: thư mục (`wizard.ask_restore_dir`, gõ Tab để gợi ý đường dẫn như trên dòng lệnh — cùng `Prompter.path` của `backup`), đích (có sẵn, hoặc "Tạo kênh mới" rồi **nhập tên** [+ about], giống hệt bước chọn đích của `clone` (`wizard.ask_new_channel`) — khác `--dst-new` trên dòng lệnh, vẫn đặt tên thẳng theo nguồn của backup vì không có gì để gõ ở đó), D3 nếu cần, filter (luôn hỏi mới — không có lựa chọn "giữ filter" như `clone` một cặp đã chạy, vì filter ở đây quyết định đọc gì ra khỏi thư mục, không phải tiếp tục một cái đã lưu), rồi xem trước và một câu xác nhận "Restore X vào Y ngay bây giờ?" (`--yes` bỏ qua, không bao giờ bỏ qua được lời tuyên bố D3; tạo đích mới vẫn cần `--yes` khi không có terminal, như `clone --dst-new`).
- Cờ lọc giống hệt `clone`/`backup`, cộng `--pushdown/--no-pushdown` (nhận cho khớp nhưng không có tác dụng: cả thư mục đã ở cục bộ, không có gì để đẩy lọc xuống) và `--preview/--no-preview`.
- **D3 luôn hỏi lại**, dù `backup.json` đã có `protected_ack` từ lúc backup, cho nhất quán "không bao giờ âm thầm": nguồn từng `noforwards` cần `--yes-i-administer-this-channel` hoặc, có terminal, gõ đúng cờ đó vào câu hỏi — **không có** nhánh "tài khoản là admin thì Có/Không" như `clone`/`backup`, vì kênh gốc có thể đã mất, không còn gì để kiểm `is_admin`. Không có terminal và không có cờ thì mã 2.
- Cờ chiến lược: `--caption keep\|strip-links\|append\|none` (+ `--caption-text`), `--ignore-unsupported`, `--placeholder`, `--topic-as-hashtag/--no-topic-as-hashtag` (khi đích có sẵn không phải forum). **Không có** `--mode` (luôn `reupload`) và **không có** `--reset-polls` (luôn bật: một backup không giữ phiếu bầu, nên phục hồi một poll luôn là tạo mới hoàn toàn; một quiz vẫn cần đáp án đúng đã biết lúc backup mới phục hồi được, không thì bị bỏ/thay placeholder như một quiz chưa trả lời khi tải lên lại tin sống).
- `--force-takeover`: chạy dù cặp có vẻ đang được một tiến trình khác giữ. `--wait` như `run`. `--fresh` (Phase 15b): quên tiến độ của cặp và gửi lại từ đầu — như `clone --fresh`, hỏi trước khi có gì để quên, `--yes` đồng ý; dùng khi vừa trỏ cặp sang một thư mục backup khác (hoặc làm lại) và muốn gửi lại toàn bộ thay vì tiếp delta.
- **Đã có mục "Restore" trong giao diện full-screen (menu)** (Phase 15a, 2026-09-27): vào thẳng `RunScreen` (như `clone`/"Chạy tiếp") với `reader_override` đọc từ thư mục backup — cùng lúc phát hiện và sửa một lỗ hổng cũ: "Chạy tiếp"/"Thử lại tin lỗi" trên một cặp bắt nguồn từ `restore` trước đây không truyền `reader_override` vào `RunScreen`, nên sẽ đọc nhầm gateway sống thay vì thư mục backup.
- **Thư mục backup mất/dời chỗ, kể cả sau khi mang `tgmirror.db` sang máy khác bằng `tgmirror appdata import`** (N6, Phase 15b, 2026-09-27): `backups.dir`/`RunOptions.from_backup` giữ đường dẫn tuyệt đối, không đi theo `appdata export`/`import` (xem `store/appdata.py`) — `tgmirror run`/`retry` (và menu "Chạy tiếp"/"Thử lại tin lỗi") trên một cặp restore mà thư mục không đọc được nữa từ chối rõ bằng một câu (`BackupDirMissing`), **không** tạo dòng chạy nào và **không** âm thầm rơi về kiểm nguồn sống (nguồn gốc có thể đã mất). Hai cách xử lý: `--from-backup THƯ_MỤC_MỚI` trên `tgmirror run`/`retry` trỏ cặp sang thư mục mới (phải là backup của cùng nguồn — kiểm `src_id`, khác nguồn thì bị từ chối rõ), tiếp đúng delta; hoặc `tgmirror restore THƯ_MỤC_MỚI --dst <đích cũ> --fresh` để gửi lại từ đầu vào cùng đích. Trên menu, "Chạy tiếp"/"Thử lại tin lỗi" gặp lỗi này thì hỏi "Chọn thư mục backup khác / Chạy lại từ đầu / Huỷ" thay vì thoát hay sập.
- **Chưa làm**: phân tích/ETA riêng, dòng tiến độ khi đang gửi một album (nhiều file trong một lần gọi không lộ callback tiến độ ra), avatar cho đích mới. Xem `06-lo-trinh.md`, "Phase 11 — ghi chú".

## Xoá tin tại chỗ (`tgmirror delete`, Phase 20, D10)

`tgmirror delete [<src>] <cờ lọc>` xoá các tin mà một filter chọn khỏi một kênh/nhóm/forum mà tài khoản **quản trị** (không phải admin → `NotAdmin`, mã 4, trước khi làm gì). Xoá không hoàn tác được trên Telegram, nên lệnh **không bao giờ xoá ngay**:

```bash
tgmirror delete "@kenh_cua_toi" --hashtag "#ads" --since 2024-01-01      # plan → gõ lại số tin → xoá
tgmirror delete "@kenh_cua_toi" --selection chon.yaml --yes --expect 412    # không tương tác
tgmirror delete "@kenh_cua_toi" --hashtag "#ads"                            # không terminal, không --yes: chỉ lưu plan O7
tgmirror delete --resume O7 --yes --expect 412                              # áp một plan đã lưu / xoá tiếp một thao tác bị dừng
```

1. **Plan**: đọc kênh theo filter (qua `FloodGuard`, như `inspect`), **đóng băng** danh sách id vào bảng `op_items` và in tóm tắt: số tin, số đơn vị/album, theo loại, theo tháng (theo năm khi trải ≥ 13 tháng), mẫu 5 tin đầu và 5 tin cuối. Chưa xoá gì; plan là một thao tác `O<n>` trạng thái `planned`. Không chọn được tin nào thì không tạo plan.
2. **Xác nhận**: có terminal → gõ lại **đúng số tin** ("Gõ 412 để xoá"; gõ khác thì huỷ plan, mã 1) — không phải Có/Không. Không có terminal → `--yes` **kèm** `--expect N` với N đúng số tin của plan (sai thì mã 2 và plan bị huỷ; `--yes` thiếu `--expect` cũng mã 2). Không có terminal và không `--yes` → plan được giữ và lệnh in cách áp nó (mã 2). Filter rỗng chọn **mọi** tin nên cần `--all` (ở terminal thì hỏi thêm một câu Có/Không, mặc định Không).
3. **Áp dụng** (foreground, phím `p`/`r`/`q` và Ctrl+C như `clone`; `tgmirror pause|stop` từ terminal khác cũng tới được): mỗi lô tối đa 100 id, **không bao giờ cắt giữa album**. Trước mỗi lô, mọi tin được **đọc lại theo id** và filter được kiểm lại: tin đã bị sửa không còn khớp thì để nguyên (`skipped`, lý do `changed`), tin đã mất thì tính `gone`. Lô được ghi `pending` trước lời gọi Telegram, xoá, rồi ghi `done`. FloodWait đi qua `FloodGuard` làn `delete` của kênh đó (`pace`, chờ rồi lặp lại, hoặc đỗ ở `waiting_flood` và thoát mã 3).
4. **Tiếp tục**: Ctrl+C/`q`/lỗi/kill giữa chừng thì `--resume O<n>` làm tiếp. Lô đang dở lúc đó được xử lý trước bằng cách đọc kênh: id không còn là đã xoá, id còn nguyên quay về `planned`; nên không sót và không xoá hai lần. Plan chưa từng áp cần xác nhận như ở bước 2; thao tác đã bắt đầu thì không hỏi lại. `--resume` không đi cùng nguồn/filter/`--all`.

Chưa có (các lát sau của Phase 20, xem `06-lo-trinh.md`): hỏi **backup trước khi xoá** (Q5), `edit`/`repost`/`undo`, mục trong menu full-screen, dòng `O<n>` trong `tgmirror status`, wizard chọn filter bằng màn hình Khám phá (phím `d`).

## Khám phá nguồn (`tgmirror inspect`, Phase 18a)

`tgmirror inspect [<src>]` trả lời "kênh có gì" và "filter này chọn gì" trước khi sao chép/backup/restore. **Chỉ đọc**: không ghi gì lên Telegram, không tải media, nên **không cần D3** kể cả với nguồn `noforwards` và kể cả khi xuất caption/text (`--list`, `--csv`) — D3 chỉ áp khi thật sự chạm vào nội dung (tải xuống, đăng lại; chốt 2026-10-12). Thiếu `<src>` mà có terminal thì wizard chọn nguồn.

```bash
tgmirror inspect "@kenh_a"                                   # tổng quan: media, tháng, hashtag
tgmirror inspect "@kenh_a" --media video --since 2024-01-01 --by month --by forward
tgmirror inspect "@kenh_a" --hashtag "#ads" --list --limit 20
tgmirror inspect "@kenh_a" --has-link --link-domain example.com --csv ads.csv
tgmirror inspect ./backups/kenh_a --by media                 # offline, từ thư mục backup
tgmirror inspect "@kenh_a" --plan clone --mode auto --caption strip-links   # pre-flight
tgmirror inspect "@kenh_a" --media video --save videos.yaml --freeze        # selection + danh sách id
```

- **Quét cả nguồn** (không đẩy filter xuống server): báo cáo mô tả cả nguồn *và* phần filter chọn, nên phải đọc hết (kênh 100.000 tin là ~1000 trang đọc; mỗi trang cách nhau `READ_WAIT` = 1 s nên **đo 2026-10-12: kênh ~13.000 tin mất 2 phút 22 giây (~1,1 s/trang), nên 100.000 tin ≈ 18 phút**; spike 14). Đi qua `FloodGuard` (`FloodOwner.of_inspect`: không có dòng `runs`/`backups`, `flood_log` để trống cả hai cột) — một lần quét dài không phải lệnh một lần của luật 1. FloodWait ngắn thì chờ rồi đọc tiếp; quá `max_auto_wait` thì in báo cáo của phần đã đọc rồi thoát mã 3. Tiến độ in ra stderr mỗi 5 giây; Ctrl+C dừng và in báo cáo phần đã đọc (đánh dấu "dừng sớm"), thoát 130.
- **`--by`** (lặp được; mặc định `media`, `month`, `hashtag`): `media`, `month`, `year`, `hashtag`, `topic` (forum; có tên topic), `sender`, `forward` (nguồn forward kèm tên), `domain` (domain của link), `size` (bậc cỡ file), `duplicate` (cùng file — cùng id file Telegram — đăng từ hai lần: số lần và dung lượng). Bảng xếp hạng in top `--top` (mặc định 10, `0` = tất cả) cộng một dòng "khác"; `month`/`year`/`size` là chuỗi thời gian/bậc nên in đủ, theo thứ tự của chúng.
- **Đếm theo tin** (mỗi tin của album tính một), kèm số unit/album và tổng dung lượng; một tin có nhiều hashtag/domain được tính vào từng cái. Chỉ tin *được lọc vào* mới vào bảng; dòng đầu luôn là tổng cả nguồn.
- **`--list`** liệt kê tin được chọn (id, ngày, loại, cỡ, đầu caption, link `t.me/...`), `--limit` mặc định 50. **`--csv FILE`** ghi *mọi* tin được chọn (UTF-8 có BOM để Excel đọc đúng tiếng Việt). **`--json`** in báo cáo + mọi tin được chọn + `next_commands` ra stdout (tiến độ ở stderr).
- **`--plan clone|backup|restore`** (pre-flight, `engine/preflight.py`): với selection này, một lần chạy thật sẽ làm gì — bao nhiêu unit đi forward / gửi lại bằng mã file / tải xuống rồi tải lên lại / bị bỏ, bao nhiêu dung lượng phải truyền, bao nhiêu lời gọi ghi và **sàn thời gian theo limiter** (chưa tính thời gian truyền byte: phụ thuộc đường mạng; không ước lượng số ngày vì ngân sách học được là của từng đích, mà một kế hoạch chưa có đích), những gì sẽ dừng lần chạy (game/invoice/quiz chưa biết đáp án mà chưa có `--ignore-unsupported`/`--placeholder`) hoặc bị bỏ có chủ đích (poll không `--reset-polls`). Dùng **đúng** `strategy.router` và `reupload.plan_unit` của runner (có test đối chiếu số unit từng đường với lần `clone` thật) và `check_options` (cờ mâu thuẫn là mã 2). Nhận `--mode`, `--caption`, `--caption-text`, `--reset-polls`, `--ignore-unsupported`, `--placeholder`, `--topic-as-hashtag`; `--plan restore` chỉ với thư mục backup, `--plan clone|backup` chỉ với kênh. Nguồn `noforwards` mà kế hoạch có phần tải xuống thì nhắc lời tuyên bố D3 sẽ cần (inspect tự nó không cần).
- **`--save FILE`** lưu filter làm file selection (`--freeze` thêm danh sách id, file `.ids` cạnh nó); **mỗi lần quét xong** còn tự ghi `@last` của nguồn đó. Cuối báo cáo, **"Dùng tiếp với đúng filter này"** in sẵn các lệnh `clone`/`backup` (hoặc `restore` cho thư mục backup) với đúng cờ — copy-paste được, vào được script. Chi tiết file/`@last`/`--exact` ở `03-filters.md`, "Selection".
- Giới hạn đã biết: backup chưa có `reply_to`/`forward`/... cho backup làm **trước** 18a (các trường tùy chọn vắng thì báo như "không có"; domain của link suy gần đúng từ HTML, hashtag cũng gần đúng — xem `03-filters.md`). 
- **Chỉ mục cục bộ (Phase 18b)**: mặc định `inspect` một kênh đi qua chỉ mục của nó (`<data_dir>/index/<id>.db`): lần đầu đọc cả nguồn và lưu lại, các lần sau chỉ đọc tin **mới hơn** `last_id` rồi báo cáo từ chỉ mục — nên đổi filter hay bảng không còn đọc lại kênh. Dòng "Chỉ mục cục bộ: N tin, cập nhật lúc ..." ở cuối báo cáo nói số liệu dựa vào đâu (JSON: khóa `index`). Tin đã sửa/xoá và lượt xem/reaction chỉ được làm mới khi chạy lại với `--refresh` (đọc cả nguồn, rồi bỏ tin không còn). `--offline` dùng chỉ mục **như đang có**, không tài khoản, không Telegram (nguồn gọi bằng id, `@username` hay tên đúng của một nguồn đã xem; không có chỉ mục thì lỗi mã 2 kèm cách dựng). `--no-index` quét sống như 18a và không lưu gì. `--refresh`/`--no-index` không dùng được với thư mục backup (vốn đã offline); `--offline` + `--refresh`/`--no-index` là lỗi mã 2. Dừng giữa chừng (Ctrl+C, FloodWait quá dài) giữ phần đã đọc: chỉ mục là một tiền tố, lần sau đọc tiếp.
- **`--quick`**: số đếm của chính Telegram (`messages.getSearchCounters`, **một** request, qua `FloodGuard`) theo loại — ảnh, video, tài liệu, nhạc, voice, video tròn, gif, có link, vị trí — của cả nguồn hoặc một topic (`--topic ID`, 1 = General); dùng để nhìn nhanh một kênh lạ hay rất lớn. Không đọc tin nào, không đi cùng bộ lọc khác hay `--by`/`--list`/`--csv`/`--save`/`--plan`/cờ chỉ mục, không dùng với thư mục backup (mã 2). Danh bạ, poll và tin ghim Telegram không trả (in "—"). Đây là **cách phân loại của Telegram**: lệch vài % so với quét (tin bị ẩn vì vi phạm điều khoản vẫn là video/ảnh/link ở đó), nên chỉ để xem nhanh, không để đoán một filter chọn bao nhiêu; request không có khoảng id/ngày nên không đếm "từ id N" (spike 1, `07` mục 9). Với `--topic N`, lệnh đọc danh sách topic trước: nguồn không phải forum, hay forum không có topic đó, là lỗi mã 2 nói rõ (một id không phải topic làm Telegram trả lỗi nội bộ sáu lần liền — gặp thật). Nếu server vẫn trả lỗi nội bộ (`RPC_CALL_FAIL`) cho một topic có thật, lệnh thoát mã 1 với một câu nói đó là lỗi của Telegram (`ServerFailed`) và gợi ý đếm cả nguồn hoặc quét bằng `inspect`.
- **Màn hình "Khám phá"** trong menu full-screen: xem "Giao diện full-screen" ở trên.
