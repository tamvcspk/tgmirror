# 07 — Kế hoạch 0.2/0.3: Inspect, Transform, thao tác hàng loạt

**Trạng thái: đã duyệt hướng** (đề xuất 2026-10-12, người dùng trả lời cả 7 câu hỏi cùng ngày, xem mục 10). **Phase 18a đã có code (2026-10-12); Phase 18b (chỉ mục, `--quick`, màn hình Khám phá) xong code 2026-10-14, ghi ở `06-lo-trinh.md`, "Phase 18b — ghi chú"** — chi tiết đã chuyển sang `01-kien-truc.md` ("Khám phá"), `02-cli-ux.md` ("Khám phá nguồn") và `03-filters.md` ("Selection", predicate mới); những chỗ làm khác với bản đề xuất dưới đây ghi ở `06-lo-trinh.md`, "Phase 18a — ghi chú" (đọc mục đó trước khi dựa vào mục 3–4 và 8 bên dưới). **Phase 19 đã bắt đầu (2026-10-13), theo ba lát cắt 19a/19b/19c; 19a (mục 5.2: chuỗi thao tác caption và mô hình `Transform`) và 19b (mục 5.1: hashtag → topic, `topic_map`, định dạng hashtag) đã có code, chi tiết ở `02-cli-ux.md` ("Transform"), `01-kien-truc.md` ("Định tuyến theo hashtag") và `06-lo-trinh.md` ("Phase 19 — ghi chú", kể cả chỗ làm khác với mục 5.1, 5.2 và 5.4 bên dưới); 19c (giữ reply, `forward_header`, link nội bộ) chưa có dòng code nào.** **Phase 20 đã bắt đầu (2026-10-18), theo lát cắt: lát 20.1 `delete` (plan → xác nhận → apply, `ops`/`op_items`, làn `delete`, `--resume`) có code; `edit`/`undo`/`repost`, lưới an toàn backup, menu và `status` chưa** — chi tiết ở `02-cli-ux.md` ("Xoá tin tại chỗ"), `04-state-checkpoint.md` ("Thao tác tại chỗ") và `06-lo-trinh.md` ("Phase 20 — ghi chú"). Phase 18–20 đã vào bảng lộ trình của `06-lo-trinh.md`; D10 đã vào `00-tong-quan.md`. Khi từng phase bắt đầu thì chuyển chi tiết của nó sang các doc 01–04 tương ứng, như đã làm với "Kế hoạch Phase 9–16".

**Bổ sung 2026-10-18 (người dùng duyệt hướng cùng ngày, trả lời 5 câu ở 11.6; chưa có dòng code nào; cả ba vào 0.3.0)**: mục 11 — định tuyến topic tổng quát (`topics.route`, Phase 21), gộp/chia topic (Phase 20b), sửa tin theo `msg_map`.

Điều kiện trước: 0.1.1 đã phát hành (Phase 16) và Đợt 4 của Phase 15b xong. Không có gì ở đây chặn 0.1.1.

---

## 1. Từ ý tưởng tới một mô hình

Ý tưởng ban đầu có ba mảnh: (a) xem/thống kê nội dung kênh bằng filter rồi dùng kết quả cho clone/backup/restore; (b) clone/restore mà kết quả *khác* nguồn (topic ↔ hashtag, ...); (c) lệnh tác động hàng loạt lên một kênh (xoá, sửa, biến tin forward thành tin của kênh).

Ba mảnh đó thật ra là ba trục của **một** mô hình. Mọi lệnh của tgmirror — cũ và mới — là:

```
  CÁI GÌ              ĐỔI THẾ NÀO            ĐI ĐÂU
  Selection     ×     Transform       ×      Target
  (nguồn+filter)      (không / có)           (báo cáo / đĩa / kênh khác / chính kênh đó)
```

| Lệnh | Selection | Transform | Target | Có |
|---|---|---|---|---|
| `inspect` | kênh hoặc thư mục backup | — | báo cáo (màn hình, JSON, CSV) | **mới** |
| `backup` | kênh | — | thư mục | đã có |
| `clone` | kênh | caption (`--caption`), topic→hashtag | kênh khác | đã có, **mở rộng** |
| `restore` | thư mục backup | như `clone` | kênh | đã có, **mở rộng** |
| `edit` | kênh | caption/text | **chính kênh đó**, tại chỗ | **mới** |
| `repost` | kênh | như `clone` | **chính kênh đó**, đăng lại ở cuối | **mới** |
| `delete` | kênh | — | (xoá khỏi chính kênh đó) | **mới** |

Hệ quả thiết kế, cũng là xương sống của cả kế hoạch:

1. **Selection là một khái niệm hạng nhất**, giống nhau cho mọi lệnh: cùng ngôn ngữ filter (đã có, `03-filters.md`), cùng cờ, cùng file, cùng wizard. Inspect chỉ là "chạy selection mà không làm gì ngoài đếm".
2. **Transform là một danh sách thao tác khai báo** dùng chung cho mọi target nhận nó. `--caption strip-links` hôm nay trở thành một thao tác trong danh sách đó. `edit` tại chỗ và `clone` sang kênh khác dùng *cùng* thao tác viết lại caption.
3. **Target quyết định mức an toàn**, không phải lệnh: ghi sang nơi khác (đĩa, kênh khác) thì chạy lại được, không mất gì; ghi lên chính kênh nguồn thì **không đảo ngược được** → cần plan/apply (mục 6.3).

## 2. Tham khảo từ các app khác

| App | Cơ chế | Lấy gì cho tgmirror |
|---|---|---|
| **beets** (quản lý thư viện nhạc) | Một ngôn ngữ query dùng chung cho `beet ls QUERY`, `beet modify QUERY field=x`, `beet rm QUERY`; `modify` in diff trước/sau rồi hỏi y/n | Gần nhất với điều ta muốn: *đổi động từ, giữ nguyên query*. Xem trước dạng diff cho `edit` |
| **mutt** | `l` (limit) thu hẹp danh sách theo pattern, `T` tag theo pattern, `;` áp lệnh lên mọi tin đã tag | Màn hình Khám phá của menu: thu hẹp → tag → chọn hành động |
| **tdl** (Telegram downloader, Go) | `tdl chat export` lọc tin bằng biểu thức rồi ghi JSON; `tdl dl`/`forward` nhận file JSON đó làm đầu vào | Đúng mẫu "inspect ra file, lệnh khác nhận file". Không lấy ngôn ngữ biểu thức tự do (khó kiểm, khó giải thích), giữ filter khai báo |
| **Terraform** | `plan -out f` → `apply f`: cái được áp đúng là cái đã xem, kể cả khi thế giới đã đổi | Plan/apply cho `delete`/`edit`/`repost` (mục 6.3) |
| **find / rsync / kubectl** | `find ... -print` rồi `-delete`; `--dry-run` | Mặc định là xem trước; hành động phá huỷ phải xin riêng |
| **GitHub "Delete repository"** | Gõ lại tên repo để xác nhận | Gõ lại *số tin* sẽ xoá (giống câu gõ nguyên văn của D3) |
| **git filter-repo** | Viết lại hàng loạt lịch sử theo các thao tác khai báo; khuyên làm trên bản clone | "Dựng lại kênh" = clone có transform sang kênh mới, rồi đổi username (mục 6.1) |
| **Gmail / Kibana / Lightroom** | Facet có số đếm, bấm một facet để lọc tiếp (drill-down) | Báo cáo của inspect *là* các facet; bấm vào là thêm predicate |
| **Telegram Desktop "Export chat history"** | JSON/HTML có `forwarded_from`, `reply_to_message_id`, `edited`, `text_entities` | Danh sách trường metadata cần thêm vào `SrcMessage` (mục 3.3) |

## 3. Inspect & report (Phase 18)

### 3.1 Trả lời được câu hỏi gì

- **Kênh có gì**: tổng tin, theo loại media, tổng dung lượng, album, tin service; theo tháng/năm; top hashtag; theo topic (forum); theo người gửi (group); **nguồn forward** (bao nhiêu tin là forward, từ kênh nào); tin có link (theo domain); tin đã sửa; tin là reply; top views/forwards.
- **Selection khớp gì**: cùng các con số đó nhưng chỉ cho phần khớp filter, kèm danh sách tin (id, ngày, loại, cỡ, đầu caption, link `t.me/c/...`).
- **Pre-flight cho clone/backup/restore** (giá trị cao, rẻ — đã có gần đủ dữ liệu): bao nhiêu unit đi forward / gửi bằng mã file / tải xuống-tải lên theo `strategy.router` với mode đã chọn; bao nhiêu GB phải truyền; ước lượng thời gian theo limiter và số ngày theo `daily_cap`; tin không hỗ trợ (game/invoice/quiz chưa trả lời) — hôm nay chỉ biết khi lần chạy dừng giữa chừng; file vượt giới hạn upload (spike 13). Báo cáo này thay cho bước "xem trước 100 tin" khi người dùng muốn con số thật.
- **Trùng lặp**: cùng file (cùng id file Telegram, hoặc cùng cỡ + thời lượng) đăng nhiều lần — đầu vào tự nhiên cho `delete`.

### 3.2 Lệnh

```bash
tgmirror inspect "@kenh_a"                                   # tổng quan cả kênh
tgmirror inspect "@kenh_a" --media video --since 2024-01-01  # cùng cờ lọc như clone
tgmirror inspect "@kenh_a" --by month --by hashtag           # bảng nhóm theo (lặp được)
tgmirror inspect "@kenh_a" --hashtag "#ads" --list           # danh sách từng tin khớp
tgmirror inspect "@kenh_a" --plan clone --mode auto --caption strip-links   # pre-flight
tgmirror inspect ./backups/kenh_a --by media                 # offline, từ thư mục backup
tgmirror inspect "@kenh_a" ... --json | --csv out.csv
tgmirror inspect "@kenh_a" ... --save chon.yaml [--freeze]   # lưu selection (mục 4)
```

- Cờ lọc đúng bộ của `clone`/`backup` (`cli/filter_options.py`), nên filter của inspect và của các lệnh khác *không thể* lệch nhau.
- `--by`: `media`, `month`, `year`, `hashtag`, `topic`, `sender`, `forward`, `domain`, `size` (bậc cỡ). Mỗi bảng in top N (`--top`, mặc định 10) cộng "khác".
- Nguồn là thư mục backup: đọc qua `BackupReader` (đã là một `MessageReader`) — không cần mạng, không tốn request. Hạn chế đã biết: hashtag trên backup là gần đúng (`03-filters.md`), backup chưa có trường forward/reply (mục 3.3).
- Inspect **chỉ đọc**, không bao giờ ghi lên Telegram, và **không cần D3** trên nguồn `noforwards` — kể cả khi xuất caption/text (`--list`, `--csv`, chỉ mục) (chốt 2026-10-12, Q6). D3 chỉ áp khi thật sự chạm vào nội dung: tải media xuống (backup, lưới an toàn của `delete`) hay đăng lại (chiến lược B, `repost`).
- Có tiến độ (đọc cả kênh 100k tin là ~1000 trang đọc cách nhau `READ_WAIT` = 1 s, đã đo: ~13.000 tin = 2 phút 22 giây, nên ~18 phút cho 100.000 tin) và dừng/tiếp được bằng Ctrl+C (mục 3.4: tiếp từ chỉ mục).

### 3.3 Dữ liệu và predicate cần thêm

`SrcMessage` hôm nay chỉ giữ đủ cho filter hiện có. Cần thêm (đều có sẵn trong tin Telethon, không tốn request thêm):

| Trường | Dùng cho |
|---|---|
| `fwd_from_id`, `fwd_from_title`, `is_forward` | báo cáo nguồn forward, predicate mới, `repost` |
| `reply_to_id` | báo cáo, giữ reply khi clone bằng chiến lược B (đã ghi là "có thể" ở `01-kien-truc.md`) |
| `edit_date` | báo cáo, phát hiện tin đổi sau khi plan |
| `urls` (domain) | báo cáo, predicate `link` |
| `file_name`, `file_ref_key` (id file ổn định) | trùng lặp, predicate `filename` |
| `forwards`, `reactions` (tổng) | báo cáo, predicate dạng `{min,max}` như `views` |
| `post_author`, `via_bot`, `pinned` | báo cáo |

Predicate filter mới (theo skill `filter-dsl`, cùng luật "thiếu thuộc tính thì sai"): `forwarded: bool`, `forwarded_from: [id]`, `is_reply: bool`, `edited: bool`, `link: [domain]` (rỗng = có link bất kỳ), `filename: [glob]`, `forwards`/`reactions: {min,max}`. Cờ CLI tương ứng: `--forwarded/--not-forwarded`, `--forwarded-from ID`, `--has-link [DOMAIN]`. Pushdown: `link` ↔ `InputMessagesFilterUrl` (thu hẹp an toàn, cùng luật hoàn thiện album); các predicate còn lại không đẩy.

Định dạng backup (`messages.jsonl`) thêm các trường này như trường tùy chọn — tương thích ngược, `FORMAT_VERSION` giữ 1 (cùng cách đã làm với tên file gốc ở Phase 15b).

### 3.4 Chi phí đọc và chỉ mục metadata

Inspect lặp lại (đổi filter, xem bảng khác) mà mỗi lần quét lại cả kênh thì vừa chậm vừa tốn request — và màn hình Khám phá (mục 4.5) không dùng được. Đề xuất một **chỉ mục metadata cục bộ** theo nguồn:

- Một file SQLite riêng mỗi nguồn, `<cache_dir>/index/<src_id>.db` (không nằm trong `tgmirror.db`: lớn, dựng lại được, không nên đi theo `appdata export`, không tranh khoá với lần chạy đang ghi). Một hàng mỗi tin: các trường của `SrcMessage` (+ mục 3.3), không có media.
- Lần đầu: quét đầy đủ (qua `FloodGuard`, luật 1 — inspect dài nên **không** xếp vào ngoại lệ "lệnh một lần"). Lần sau: delta theo id lớn nhất, như backup. `--refresh`: quét lại toàn bộ để bắt tin đã sửa/xoá. Mọi đầu ra ghi "chỉ mục cập nhật lúc ...".
- Filter nào cũng chạy được trên chỉ mục bằng chính `Matcher` (đánh giá trên `SrcMessage`), không viết lại thành SQL — một nơi định nghĩa ngữ nghĩa.
- Chỉ mục **không bao giờ là căn cứ để ghi**: `delete`/`edit`/`repost` luôn đọc lại tin sống theo id trước khi tác động (mục 6.3).
- Tăng tốc rẻ không cần quét: `messages.getSearchCounters` trả số tin theo từng loại (ảnh, video, tài liệu, nhạc, voice, link, ...) trong **một** request — đủ cho `inspect --quick` của một kênh lạ (cần spike, mục 9).

Có thể làm v1 không có chỉ mục (quét sống mỗi lần) rồi thêm chỉ mục ở 18b; xem mục 8.

## 4. UX: dùng lại filter của inspect cho lệnh chính

### 4.1 Vấn đề

Inspect là việc **lặp**: thử filter, nhìn số, chỉnh, nhìn lại. Lệnh chính là việc **một lần**, cần đúng filter cuối cùng. Dòng lệnh không có "trạng thái đang chọn" để mang từ lệnh này sang lệnh kia, và filter phức tạp (nhiều rule OR, exclude) gõ lại là dễ sai. Với `delete` còn tệ hơn: filter đánh giá lại lúc xoá có thể khớp **tin khác** với lúc xem (tin mới đăng, tin vừa sửa).

### 4.2 Năm tầng, từ đơn giản tới mạnh — làm cả năm, mỗi tầng phục vụ một kiểu người dùng

1. **Cùng cờ cho mọi động từ** (đã gần có). `tgmirror inspect --src X --media video` → sửa đúng một chữ thành `tgmirror clone --src X --media video --dst-new ...`. Mũi tên lên trong shell là đủ.
2. **Inspect in sẵn lệnh kế tiếp.** Cuối mỗi báo cáo:
   ```
   412 tin khớp (37.8 GB, 96 album) trên 58,211 tin.
   Dùng tiếp với đúng filter này:
     tgmirror clone  --src -1001234567890 --media video --hashtag "#tutorial" --since 2024-01-01 --dst-new "..."
     tgmirror backup --src -1001234567890 --media video --hashtag "#tutorial" --since 2024-01-01 ./backup
   ```
   Dựng từ `FilterSpec` đã chuẩn hoá (cần hàm ngược `to_flags`, chỉ khi filter biểu diễn được bằng cờ; không được thì gợi ý `--save`). Đây là cách CLI-native nhất: không có trạng thái ẩn, copy-paste được, vào được script.
3. **File selection** (`--save chon.yaml`, mục 4.3) cho filter phức tạp hoặc đã chỉnh tay. Mọi lệnh nhận `--selection chon.yaml`. Đọc được, sửa được, commit được, gửi cho người khác được. `--filter-file` hiện có vẫn chạy (file chỉ có filter là một selection không có nguồn).
4. **`@last`**: inspect luôn tự lưu selection cuối cùng của mỗi nguồn vào `<data_dir>/selections/last-<src_id>.yaml`; `--selection @last` (và wizard, mục 4.4) dùng nó. Không phải "job": chỉ là file, ghi đè mỗi lần, không liệt kê, không quản lý.
5. **Menu full-screen** (mục 4.5): ở đây giới hạn của dòng lệnh biến mất — lọc, nhìn số, bấm hành động, trong cùng một màn hình.

### 4.3 File selection

```yaml
# tgmirror selection — tạo bởi `tgmirror inspect` 2026-10-12 10:31 UTC
version: 1
source: {id: -1001234567890, title: "Kênh A"}   # id là căn cứ; title chỉ để người đọc
filter:                                          # đúng ngôn ngữ của 03-filters.md
  include: [{media: [video], hashtag: ["#tutorial"]}]
  date: {from: 2024-01-01}
snapshot:                                        # chỉ có khi --freeze (hoặc do plan tạo, mục 6.3)
  taken_at: 2026-10-12T10:31:00Z
  upto_id: 58211
  count: 412
  ids_file: chon.ids                             # một id mỗi dòng; sửa tay được (xoá dòng = bỏ tin)
transform: { ... }                               # tùy chọn, mục 5.4; lệnh không nhận transform thì từ chối (mã 2), không lờ đi
```

- `source` điền cho `--src` khi thiếu; có cả hai mà khác nhau → mã 2. Selection từ thư mục backup ghi `source: {backup: ./backups/kenh_a}`.
- Kiểm tra như filter hôm nay: sai thì mã 2 trước khi hỏi hay ghi gì.
- Một file selection không có `source`/`snapshot`/`transform` chính là file `--filter-file` hiện tại → không cần migration.

### 4.4 Filter sống hay danh sách đóng băng

| Lệnh | Mặc định | Lý do |
|---|---|---|
| `clone`, `backup`, `restore` | **filter sống**: đánh giá lại lúc chạy; snapshot (nếu có) bị bỏ qua | Giữ delta: lần chạy sau lấy cả tin mới đăng khớp filter. `--exact` để chỉ lấy đúng danh sách đóng băng (đọc theo id, như `retry`) — hữu ích khi người dùng đã sửa tay danh sách |
| `edit`, `repost`, `delete` | **danh sách đóng băng, bắt buộc** | "Cái bị xoá đúng là cái đã xem". Không có snapshot thì lệnh tự tạo plan trước rồi hỏi (mục 6.3) |

Ở cả hai chế độ, mỗi tin bị tác động vẫn được kiểm lại với filter lúc tác động: tin đã sửa tới mức không còn khớp thì bỏ qua (`skipped: changed_since_plan`), không bao giờ "đã lỡ chọn thì cứ làm".

Wizard (`clone`, `backup`, `restore`, và các lệnh mới): bước filter thêm lựa chọn "Dùng kết quả khám phá gần nhất của nguồn này (412 tin, 10:31)" khi `@last` của nguồn đó tồn tại, và "Nạp từ file selection" bên cạnh "Nạp từ file YAML" hiện có (gộp làm một).

### 4.5 Màn hình "Khám phá" trong menu

```
┌ Khám phá ▸ Kênh A · 58,211 tin · chỉ mục 3 phút trước ─────────────────────┐
│ Lọc: video · #tutorial · từ 2024-01-01                       [f] sửa lọc    │
│ Khớp: 412 tin · 37.8 GB · 96 album                                          │
│                                                                              │
│  Loại           Tháng            Hashtag          Forward từ                │
│ ›video    412   2024-01   31     #tutorial  412   (không)       380         │
│                 2024-02   44     #python    120   @kenh_khac     32         │
│                 ...              #ads         7                              │
│                                                                              │
│ ←→ cột  ↑↓ mục  Enter lọc thêm  x loại trừ  Backspace bỏ bước lọc cuối     │
│ l danh sách tin   s lưu selection   c clone   b backup   e sửa   d xoá      │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Mỗi cột là một bảng `--by`; Enter trên một mục thêm predicate tương ứng vào rule `include` (AND — đúng hình dạng mà cờ tạo ra, nên luôn in ra được thành lệnh, tầng 2), `x` thêm một rule `exclude`. Backspace lùi bước lọc cuối (ngăn xếp, giống Esc lùi câu hỏi của wizard).
- Chạy trên chỉ mục (mục 3.4) nên mỗi bước là tức thì; không có chỉ mục thì màn hình đề nghị dựng trước (có tiến độ, `q` dừng).
- Phím hành động mở đúng flow của lệnh đó (`CloneFlow`, `BackupFlow`, ...) với selection đã điền sẵn — bước filter của wizard được bỏ qua, mọi bước khác (đích, D3, xác nhận) giữ nguyên. Luật 7: không có logic chỉ menu mới có.

## 5. Transform khi sao chép (Phase 19)

### 5.1 Cấu trúc

| Biến đổi | Hôm nay | Đề xuất |
|---|---|---|
| topic → hashtag (forum → không phải forum) | có, `--topic-as-hashtag` | giữ; thêm chọn **định dạng** hashtag (hiện tự sinh từ tên topic) |
| **hashtag → topic** (không phải forum → forum) | không | mới: định tuyến tin vào topic theo hashtag. `auto` = mỗi hashtag (trong danh sách cho trước, hoặc top N từ inspect) một topic; `map` = bảng hashtag → tên topic; tin nhiều hashtag lấy rule khớp đầu tiên; không có hashtag → General hoặc topic mặc định; `strip: true` bỏ hashtag đã dùng khỏi caption |
| topic → topic khác tên / gộp topic | không (ánh xạ 1-1 theo tên) | mới: `map` tên/id topic nguồn → tên topic đích; nhiều nguồn vào một đích = gộp; `drop` để bỏ topic |
| forum → nhiều kênh (tách theo topic) | làm được: nhiều `clone --topic N` | không làm thêm; ghi vào hướng dẫn |
| giữ reply | không (forward không giữ) | **xong ở 19c1** (`reply: keep`, xem `06`): mới, chỉ khi tin đi đường gửi lại (B/REFERENCE): `reply_to` ánh xạ qua `msg_map`; tin được reply nằm ngoài selection → bỏ reply |
| giữ/bỏ header "Forwarded from" | luôn bỏ (`drop_author=True`, D2) | **xong ở 19c2** (không ghép với thứ buộc gửi lại, xem `06`): tùy chọn `forward_header: keep` → `drop_author=False`. Cũng là câu trả lời cho "biến forward thành tin của kênh" **khi clone**: điều đó đã là mặc định |

Mọi biến đổi cấu trúc đều quyết định *đường đi* của unit (giống `topic_as_hashtag` hôm nay): cái gì forward không làm được (đổi caption, thêm reply) đẩy unit sang REFERENCE hoặc B, nên D3 áp dụng như cũ (`strategy.may_reupload` mở rộng). Pre-flight của inspect (mục 3.1) cho người dùng thấy trước bao nhiêu unit sẽ bị đẩy sang tải lại.

### 5.2 Nội dung (caption/text)

`--caption keep|strip-links|append|none` thành một **danh sách thao tác chạy theo thứ tự**:

```yaml
caption:
  - strip_links: source           # = --caption strip-links hôm nay
  - remove_hashtags: ["#ads", "#quangcao"]
  - remove_mentions: source
  - replace: {regex: "(?i)liên hệ @\\w+", with: ""}
  - append: "\n\n— Kênh B"        # = --caption append
  - text_too: false               # mặc định chỉ caption của tin media, như hôm nay; true = cả tin chữ
```

- Cùng một engine viết lại (`rewrite_caption` + dịch entity theo UTF-16, đã có) nên định dạng được giữ.
- Cờ cũ là cú pháp tắt, dịch sang danh sách này — không đổi hành vi của lệnh cũ.
- `regex` dùng thư viện `regex` với timeout như filter.
- Chỉ thao tác khai báo, **không** ngôn ngữ biểu thức/script tự do (khác tdl): kiểm được trước khi chạy, test được, giải thích được trong một dòng xem trước.

### 5.3 Liên kết nội bộ

`links: rewrite` đổi `t.me/<nguồn>/123` (và `t.me/c/<id>/123`) thành link tới tin tương ứng ở đích qua `msg_map`. Link tới tin **sau** (chưa sao chép) không biết id đích lúc gửi → hoặc để nguyên và ghi lại, rồi một lượt `edit` sau khi clone xong sửa chúng (dùng chính máy của Phase 20), hoặc chỉ hỗ trợ link lùi ở 0.2. Đề xuất: 0.2 chỉ link lùi + báo số link chưa đổi được; lượt sửa bù ở 0.3.

### 5.4 Nơi đặt transform

Trong file selection (`transform:`), hoặc file riêng `--transform t.yaml`; cờ tắt cho ca phổ biến (`--hashtag-to-topic`, `--topic-map "Cũ=Mới"`, `--topic-default`, `--strip-routing-hashtag` — có từ 19b/19b1; `--keep-replies` — có từ 19c1; `--keep-forward-header` — 19c2; `--rewrite-links` — có từ 19c3). Transform được nhớ theo cặp (`mirrors.options_json`), giống filter: `run` dùng lại, đổi transform giữa chừng thì cảnh báo rằng tin đã sao chép không bị sửa lại (không quét lại như đổi filter).

## 6. Thao tác hàng loạt trên chính một kênh (Phase 20)

### 6.1 Giới hạn của Telegram định hình mọi thứ

Telegram **không chèn được tin vào quá khứ** và (theo hiểu biết hiện tại, cần spike) **không chuyển được tin giữa các topic**. Vì vậy:

| Muốn | Tại chỗ được không | Cách |
|---|---|---|
| Xoá tin | được (admin có quyền xoá) | `delete` |
| Sửa caption/text | được với tin của kênh; **không** với tin forward (cần spike), group có thể chỉ tin của mình | `edit` |
| Thêm/bớt hashtag | được (là sửa text) | `edit` |
| Forward → tin của kênh | **không** tại chỗ: phải đăng bản mới ở **cuối** kênh rồi xoá bản cũ — mất thứ tự, views, reactions, bình luận, link tới tin cũ | `repost --delete-original` |
| hashtag → topic, đổi topic | **không** tại chỗ: như trên | `repost` |

Nên với mọi biến đổi cấu trúc trên **cả kênh**, đường đúng là **dựng lại**: `clone` có transform sang kênh mới (giữ thứ tự), kiểm tra, rồi chuyển username sang kênh mới. tgmirror nên nói điều này khi người dùng chọn `repost` trên một selection lớn. `repost` chỉ hợp lý cho số ít tin.

### 6.2 Lệnh

```bash
tgmirror delete --selection ads.yaml                     # plan → xem → gõ số tin → xoá
tgmirror delete --src "@kenh_a" --hashtag "#ads" --expect 37 --yes   # không tương tác
tgmirror edit   --selection chon.yaml --transform t.yaml # xem diff trước/sau → xác nhận
tgmirror repost --selection fwd.yaml [--delete-original] [--silent]
tgmirror undo O12                                        # chỉ cho edit (mục 6.3)
```

- Đích là chính nguồn → không có `--dst`. Cần quyền tương ứng (xoá/sửa/đăng), kiểm trước khi plan (mã 4).
- Album không bao giờ bị tách (luật 4): xoá/đăng lại cả album; sửa caption của album là sửa tin mang caption.
- `delete` gom tối đa 100 id mỗi lời gọi; `edit` một lời gọi mỗi tin; `repost` như chiến lược A/REFERENCE/B (đăng **im lặng** mặc định, vì đăng lại hàng loạt trên kênh sống là spam thông báo cho người theo dõi).

### 6.3 An toàn: plan → apply

1. **Plan** (mặc định, và là điều duy nhất xảy ra khi thiếu xác nhận): đánh giá selection, đóng băng danh sách id, kiểm quyền từng tin (tin nào không sửa/xoá được và vì sao), in tóm tắt (số tin, theo loại, theo tháng, mẫu 10 tin; với `edit` là **diff trước/sau** của mẫu, kiểu `beet modify`), ghi vào một lần thao tác `O<n>` trạng thái `planned`.
2. **Xác nhận**: có terminal → gõ lại **số tin** ("Gõ 412 để xoá"), như câu gõ nguyên văn của D3; không phải Có/Không. Không có terminal → `--yes` **kèm** `--expect N`: số trong plan khác `N` thì từ chối (mã 2) — chặn đúng kiểu sai "filter trong script khớp nhiều hơn mình nghĩ".
3. **Lưới an toàn — tùy chọn** (chốt 2026-10-12, Q5): trước khi xoá, `delete` và `repost --delete-original` **hỏi Có/Không** "Backup N tin này trước khi xoá?" (mặc định Có), có thì hỏi thư mục (gợi ý một thư mục trong data dir). Không tương tác: chỉ backup khi có `--backup-to DIR`; `--no-backup` bỏ câu hỏi. Lưu ý D3: kênh `noforwards` cần lời tuyên bố cho bước backup này như mọi backup.
4. **Apply**: mỗi tin đọc lại sống theo id, kiểm lại filter (mục 4.4), rồi mới tác động. Pause/stop/`p`/`r`/`q`/Ctrl+C như một lần chạy; FloodWait qua `FloodGuard`.
5. **Undo** chỉ cho `edit`: lưu text + entity gốc từng tin trong `op_items` → `tgmirror undo O<n>` sửa ngược lại (cũng là một thao tác có plan). `delete` không undo được trên Telegram; "khôi phục" là `restore` từ lưới an toàn — tin quay lại **ở cuối** kênh, nói thẳng điều đó.

### 6.4 Trạng thái

- `ops` (nhật ký, giống `runs`/`backups`: loại, chat, selection + transform JSON, trạng thái, `control`, `stats_json`, thời gian) và `op_items` (op, `msg_id`, `grouped_id`, trạng thái `planned/pending/done/failed/skipped`, `reason`, `before_json` cho edit, `new_msg_id` cho repost). Write-ahead `pending` và một transaction mỗi batch như `msg_map` (luật 3).
- Reconcile sau crash: `delete` — đọc lại id `pending`, vắng mặt = đã xoá; `edit` — so text hiện tại với text mới; `repost` — dò "tail" như reconcile hiện có.
- `FloodOwner.of_op` (cột `op_id` trong `flood_log`); `history` hiện `O<n>` bên cạnh `R<n>`/`B<n>`; `status`, `pause`, `stop`, badge "đang chạy ở nơi khác" đều xét ops. Một account một việc ghi tại một thời điểm, như hôm nay.

### 6.5 Quan hệ với Phase 17 (đồng bộ edit/delete nguồn → đích)

Phase 17 cần đúng máy của Phase 20: sửa/xoá tin ở đích theo `msg_map`, có write-ahead và reconcile. Đề xuất làm Phase 20 trước rồi Phase 17 thành một lệnh mỏng ("tin nguồn đã đổi/xoá → plan edit/delete trên đích"), dùng chỉ mục metadata (`edit_date`, tin vắng mặt khi `--refresh`) để biết cái gì đổi. Cần đổi số thứ tự phase — mục 10, Q7.

## 7. Ảnh hưởng tới kiến trúc và luật cứng

- **Gateway** thêm: `delete_messages`, `edit_message`, `search_counters`; `SrcMessage` thêm trường (mục 3.3). `FakeGateway` cài cả ba, cùng `poison`/`fail_next`.
- **Gói mới**: `engine/inspect.py` (gom số liệu, thuần, chạy trên mọi `MessageReader`), `engine/index.py` (chỉ mục), `engine/selection.py` (đọc/ghi/kiểm file selection, `to_flags`), `engine/transform.py` (danh sách thao tác + áp lên caption/định tuyến; `rewrite_caption` dời vào đây), `engine/ops.py` (plan/apply/undo), `store/ops.py`. CLI: `inspect`, `delete`, `edit`, `repost`, `undo`. Menu: `ExploreScreen`, và `TaskScreen` dùng lại cho ops.
- **Luật 1**: inspect (quét dài) và ops đi qua `FloodGuard`; danh sách ngoại lệ "lệnh một lần" không đổi. Luật 3: ops bền như runs. Luật 4: không tách album ở cả ba lệnh mới. Luật 5 (D3): áp cho mọi đường tải xuống mới (backup lưới an toàn, `repost` đường B, transform đẩy unit sang B). Luật 7: mọi lệnh mới có cả cờ lẫn wizard/menu, cùng một đường code.
- **D10 (chốt 2026-10-12)**: tgmirror được ghi/xoá trên một kênh của user ngoài vai trò đích của clone, chỉ khi có xác nhận (plan/apply, gõ lại số tin hoặc `--yes --expect N`). Ghi ở bảng quyết định của `00-tong-quan.md`. "Bài toán"/"Mục tiêu" của `00-tong-quan.md` sửa khi Phase 20 bắt đầu, không sửa trước (cùng lệ với Phase 11). Khi Phase 20 bắt đầu, `CLAUDE.md` thêm một luật cứng cho D10, như luật 5 cho D3.

## 8. Lộ trình đề xuất

Tách thành **hai release** để phần chỉ đọc/không phá huỷ ra trước, phần ghi tại chỗ (rủi ro cao, cần spike trên Telegram thật) ra sau:

| Phase | Nội dung | Hoàn thành khi | Release |
|---|---|---|---|
| 18a | `SrcMessage` + predicate mới (mục 3.3, kể cả trường mới trong backup), `tgmirror inspect` quét sống + `BackupReader`, `--by`/`--list`/`--json`/`--csv`, pre-flight `--plan`, in lệnh kế tiếp, file selection + `--save`/`--freeze`/`@last`, `--selection` cho `clone`/`backup`/`restore` (+ `--exact`), wizard "dùng kết quả khám phá" | Inspect một kênh thật, rồi `clone --selection @last` sao chép đúng số tin inspect đã báo; pre-flight khớp số unit theo chiến lược của lần chạy thật | 0.2.0 |
| 18b | Chỉ mục metadata (delta, `--refresh`), `inspect --quick` (`getSearchCounters`), màn hình "Khám phá" trong menu | Lọc lặp lại trên kênh 100k tin không gọi Telegram; từ Khám phá bấm `c` ra đúng wizard clone với filter điền sẵn | 0.2.0 |
| 19 | Transform: danh sách thao tác caption (cờ cũ thành cú pháp tắt), hashtag → topic, `topic_map`, giữ reply, `forward_header: keep`, link nội bộ (lùi), nhớ transform theo cặp, áp cho cả `clone` và `restore` | Clone một kênh thường sang forum với hashtag → topic: đúng topic, đúng thứ tự trong từng topic; restore cùng transform cho cùng kết quả | 0.2.0 |
| 20 | `delete`/`edit`/`repost`/`undo`: plan/apply, gõ lại số tin, `--expect`, lưới an toàn, `ops`/`op_items`, reconcile, `O<n>` trong history/status | Kill giữa `delete` 1000 tin rồi chạy lại: không sót, không xoá ngoài plan; `edit` rồi `undo` trả đúng text + định dạng gốc | 0.3.0 |
| 17 | Đồng bộ edit/delete nguồn → đích, dựng trên máy của 20 + chỉ mục | Theo nhu cầu (như cũ) | 0.3.x |

Thứ tự trong 0.2.0: 18a → 19 → 18b (18b là UX đắt nhất; 18a + 19 đã đủ giá trị và đủ để phát hành nếu cần cắt).

## 9. Spike cần trên Telegram thật (người dùng chạy, như `scripts/spike_*.py`)

1. `messages.getSearchCounters`: các loại nào, có áp `min_id`/`max_id` không (nhớ spike 10: `count` bỏ qua khoảng), độ chính xác so với quét. **Script có sẵn: `scripts/spike_counters.py`** (một request đếm 13 loại, quét cả kênh, in hai cột cạnh nhau; `--topic` cho forum). Đọc chữ ký của Telethon 1.45 thì request chỉ có `peer`, `filters`, `saved_peer_id`, `top_msg_id`: **không có `min_id`/`max_id`/ngày**, nên con số này luôn là của cả kênh (hoặc cả một topic) và `--quick` không thể trả lời "từ id N"; phần còn lại (loại nào được trả lời, `inexact`, sai lệch so với quét) cần chạy script.
   **Kết quả (người dùng chạy 2026-10-14, hai lần: kênh 1.038 tin và forum 2.660 tin; xong phần lớn):**
   - **Tốc độ**: một request trả lời trong 0,0–0,2 s cho 13 bộ lọc; quét cùng kênh mất 11 s và 27 s. `inexact` **không bao giờ** được đặt.
   - **Phủ**: server chỉ trả 10 trong 13 bộ lọc thử — ảnh, video, ảnh+video, tài liệu, nhạc, voice, video tròn, gif, link, vị trí. **Danh bạ, poll và tin ghim bị bỏ hẳn**: không lỗi, chỉ vắng trong danh sách trả về (kênh có 1 poll, và 44 tin ghim ở forum).
   - **`ảnh+video` = ảnh + video** (1.005 = 144 + 861): là tổng hai bộ lọc, không phải một số riêng.
   - **Khớp quét**: ảnh, tài liệu, nhạc, voice, video tròn, gif, vị trí ở kênh nhỏ — và **toàn bộ bảng của topic 1 (General)** với `top_msg_id=1`: ảnh 2 = 2, video 27 = 27, ảnh+video 29 = 29, link 3 = 3. Tức là `top_msg_id` lọc đúng một topic kể cả General (id 1). **Giới hạn gặp sau (2026-10-14, lệnh thật)**: với một topic có thật nhưng nhiều khả năng rất lớn (id 9496, có trong `tgmirror topics`) Telegram trả `RPC_CALL_FAIL` sáu lần liền; `--quick --topic` khi đó báo `ServerFailed` và gợi ý quét bằng `inspect`.
   - **Lệch, và vì sao** (`--explain` trên kênh nhỏ): video +10 và link +4 là **tin mà Telegram thay nội dung bằng thông báo vi phạm điều khoản** ("This channel can’t be displayed because it violated Telegram’s Terms of Service"): server vẫn đếm chúng là video/link, còn quét thấy một tin chữ. Link +1 còn lại là một video không có chữ (id 297) mà server tính là có link, chưa rõ vì sao. Forum cả kênh (`--explain` chạy cùng ngày): video +33 và ảnh +1: 15 tin video đầu (script chỉ in chừng đó) và tin ảnh **đều là tin thông báo vi phạm** (`topic_id=None`, `reply_to=None`), 18 tin còn lại chưa xem; link +11 gồm 9 tin thông báo và 2 video không chữ nằm trong topic 2 và 182 (cùng kiểu với id 297: server tính video không chữ là có link; nghi ngờ link ẩn trong thực thể của tin, chưa kiểm). Đây là chênh **cách phân loại**, không phải đếm sai: ở cả kênh, server lớn hơn quét ở mọi dòng lệch trừ tài liệu (−1).
   - **Hai tin còn lại (`--raw 3059 3060`, đã in thô)**: cả hai `reply_to=None`, do chính tài khoản đăng (`out`), nên đúng là General — **`reader` không đọc nhầm topic**. Id 3060 là `MessageMediaToDo` (danh sách việc/checklist mới của Telegram): `media_kind` rơi vào nhánh cuối `return MediaKind.DOCUMENT` nên `inspect` và `--media document` coi nó là tài liệu, còn bộ lọc tài liệu của server thì không (468 so với 469). **Đây là lỗi phân loại của mình, không phải của `--quick`**: ToDo, `MessageMediaPaidMedia`, `VideoStream`, `Unsupported`... đều thành `document`, và trong chiến lược B nhánh `document` sẽ đi tải một file không có (chỉ `game`/`invoice` được coi là không sao chép được ở `engine/reupload.py`; chưa thử trên Telegram thật xem lỗi hiện ra thế nào). Id 3059 là `MessageMediaVenue` (địa điểm có tên): `media_kind` xếp vào `geo`; server đếm 1 `geo` ở cả kênh mà `top_msg_id=1` trả 0 — vì chưa liệt kê id của tập `geo` cả kênh nên chưa biết số 1 đó có đúng là tin này không (nghi: bộ lọc `geo` của server chỉ đếm vị trí thường, không đếm venue).
   - **Hệ quả cho `--quick`**: số là **cách phân loại của Telegram**, không phải của `Matcher`, nên chỉ để xem nhanh (nhãn "theo Telegram, có thể lệch vài %"), không dùng để dự đoán số tin một filter chọn; loại không có thì in "—"; theo topic được (`top_msg_id`), theo khoảng id/ngày thì không (request không có tham số đó).
2. `channels.deleteMessages`: tốc độ, FloodWait khi xoá hàng nghìn tin; xoá tin trong supergroup/forum của người khác; xoá có làm đổi id/thứ tự gì không.
3. `messages.editMessage`: sửa tin cũ trong broadcast/supergroup/forum (giới hạn thời gian?), sửa tin forward, sửa caption album, người theo dõi có nhận thông báo không, nhãn "đã sửa".
4. Chuyển tin giữa các topic: xác nhận thật sự không có API (nếu có thì `repost` cho hashtag → topic tại chỗ không cần nữa).
5. Giữ reply vào topic (19c1) — **xong 2026-10-14** (`scripts/spike_reply.py`, chạy trên forum thử): `reply_to=<id tin>` một mình vào đúng topic của tin đó; album với `reply_to_msg_id`+`top_msg_id` cũng đúng; reply sang topic khác chưa thử.
   Đăng im lặng (`silent`) cho `repost` hàng loạt; `forward_header: keep` (`drop_author=False`) có giữ album/topic như `drop_author=True` không (19c2: **script có sẵn `scripts/spike_forward_header.py`**; **album: có**, một nhóm như `drop_author=True`, chạy 2026-10-14; vế topic chưa chạy).
6. Cỡ chỉ mục và thời gian quét cho một kênh ~100k tin ở `read_delay` mặc định.

## 10. Câu hỏi đã chốt (người dùng trả lời 2026-10-12)

| # | Câu hỏi | Trả lời |
|---|---|---|
| 1 | Cho ghi/xoá tại chỗ trên kênh của user (D10)? | **Được, với xác nhận** (khung plan/apply ở mục 6.3) |
| 2 | Tách 0.2.0 / 0.3.0 hay gộp? | **Tách** như mục 8 |
| 3 | Dùng lại filter bằng file selection + `@last` + in lệnh kế tiếp, không lưu selection có tên trong DB? | **Đồng ý** |
| 4 | Chỉ mục metadata là file riêng theo nguồn trong cache dir, không đi theo `appdata export`? | **Đúng** |
| 5 | `delete` mặc định backup trước khi xoá? | **Tùy chọn, hỏi Có/Không** (mục 6.3, bước 3) |
| 6 | Xuất caption/text của nguồn `noforwards` từ inspect có cần D3? | **Không**; D3 chỉ áp khi thật sự chạm vào nội dung (tải media, đăng lại) |
| 7 | Dời Phase 17 ra sau Phase 20, dựng trên máy của nó? | **Ok** |

## 11. Bổ sung cho release sau 0.2.0: định tuyến topic tổng quát, gộp/chia topic, sửa tin (đề xuất 2026-10-18, **người dùng duyệt hướng cùng ngày**, câu trả lời ở 11.6; chưa code)

Người dùng yêu cầu (2026-10-18): thêm vào plan các thao tác cho forum — **gộp topic, chia topic, sửa tin** — đi qua selection và transform; và gặp hai tình huống thật khi dùng 19b:

- **Ca 1 — nhiều hashtag**: tin có `#a #b #c`, `auto: all` luôn lấy hashtag *viết đầu tiên* (`TopicRules.match`), `map` lấy theo thứ tự của bảng. Không có cách nói "lấy hashtag cuối", "lấy hashtag khớp mẫu này".
- **Ca 2 — tên file trong caption, không có hashtag**: caption kiểu `Alida_0412.mp4`; muốn dùng regex trích tên trong đó (`Alida`) làm tên topic. Hôm nay không có cách nào: định tuyến chỉ đọc hashtag.

Yêu cầu: giải pháp không chạy theo từng ca mà là một cơ chế chung.

### 11.1 Chẩn đoán

19b cứng ba chỗ, và cả hai ca (lẫn gộp/chia) đều rơi vào đúng ba chỗ đó:

| Chỗ cứng hôm nay | Cái cần |
|---|---|
| **Khoá lấy từ đâu**: chỉ hashtag | hashtag, caption/text, tên file, tên topic nguồn, ... |
| **Chọn ứng viên nào**: hashtag đầu tiên (`auto`), hoặc thứ tự của `map` | `first` / `last`, lọc ứng viên bằng regex, bỏ qua một danh sách |
| **Đặt tên thế nào**: `_title_of_tag` (`#machine_learning` → `Machine learning`) hoặc tên cố định | một mẫu tên dùng được phần trích ra (`{name}`) với vài bộ lọc cố định |

Thêm một điều kiện tùy chọn (`when`, chính là ngôn ngữ filter) thì cùng một nguyên tố giải luôn gộp topic (`when: {topic: [A, B]}` → một tên) và chia topic (`when: {topic: [A], ...}` → nhiều tên).

### 11.2 Đề xuất: luật định tuyến (`topics.route`)

Một **danh sách luật có thứ tự**; luật đầu tiên *cho ra được một tên topic* thắng. Mỗi luật có bốn phần, phần nào cũng tùy chọn trừ `to`:

```yaml
topics:
  route:
    # tên cố định theo điều kiện — `when` là một rule include của 03-filters.md (các predicate AND)
    - when: {hashtag: ["#vip"]}
      to: "VIP"

    # Ca 1: mỗi hashtag là một ứng viên; lấy cái CUỐI, bỏ qua danh sách quảng cáo
    - from: hashtags
      pick: last                        # first (mặc định) | last
      skip: ["#YYLive", "#789Live"]
      to: "{value|tag_title}"           # đúng cách `auto: all` đặt tên hôm nay

    # Ca 2: trích tên file trong caption, bỏ số thứ tự và đuôi
    - from: text                        # caption/text của tin NGUỒN, trước mọi thao tác caption
      regex: '(?m)^(?P<name>[^\s.]+?)(?:[_-]\d+)*\.(?:mp4|mkv|mov|jpe?g)\s*$'
      to: "{name|title}"

    # hoặc từ tên file thật của media (SrcMessage.file_name), khi có
    - from: file_name
      regex: '^(?P<name>[A-Za-z]+)'
      to: "{name}"

    # chia một topic nguồn: tin của topic 1207 có chữ "part 2" sang topic riêng
    - when: {topic: [1207], regex: '(?i)part\s*2'}
      to: "Alida — phần 2"

  default: "Khác"                       # không luật nào cho ra tên, và tin không nằm trong topic nguồn nào
  limit: 100                            # topic MỚI mà luật có biến (`{...}`) được tạo mỗi lần chạy
  strip: matched                        # bỏ phần đã dùng để định tuyến khỏi caption (hashtag / đoạn regex khớp)
  topic_map: { ... }                    # như 19b
```

**Các phần của một luật:**

- `when` — điều kiện, **đúng ngôn ngữ của filter** (`03-filters.md`, đánh giá bằng chính `Matcher`, cùng luật "thiếu thuộc tính thì sai", regex có timeout). Không có ngôn ngữ thứ hai cho điều kiện.
- `from` — nguồn ứng viên: `hashtags` (mỗi hashtag một ứng viên, theo thứ tự viết), `text` (caption/text), `file_name`, `topic` (tên topic nguồn), `forwarded_from` (tên kênh gốc của tin forward). Album: ứng viên gom từ mọi tin của album theo thứ tự (luật 4 — một unit một topic).
- `regex` — tùy chọn; giữ ứng viên khớp và lấy nhóm: nhóm có tên → `{name}`, nhóm số → `{1}`, cả đoạn → `{0}`; `{value}` là ứng viên (hoặc đoạn khớp). `pick` chọn lần khớp đầu/cuối; `skip` bỏ ứng viên trong danh sách (so không phân biệt hoa thường).
- `to` — mẫu tên. Bộ lọc **cố định, không mở rộng được**: `title` (hoa chữ đầu), `lower`, `upper`, `tag_title` (`#a_b` → `A b`), `slug`. Kết quả được cắt khoảng trắng, gộp khoảng trắng liền, tối đa 128 ký tự (`MAX_TOPIC_TITLE`); **rỗng thì luật coi như không khớp**, xét luật kế tiếp.

**Ngữ nghĩa giữ nguyên của 19b:**

- Định tuyến đọc **tin nguồn**, không đọc caption sau các thao tác `caption:` (như hôm nay: t.yaml xoá mọi từ không phải hashtag mà vẫn định tuyến đúng).
- Thứ tự ưu tiên: `route` → ánh xạ topic nguồn (`topic_map`, 1-1 theo tên) → `default` (chỉ cho tin không nằm trong topic nguồn nào) → General.
- Topic đích tìm theo **tên** trước khi tạo (`TopicResolver._topic_titled`), nên chạy lại / crash / hai luật cho cùng tên đều về một topic; tên so không phân biệt hoa thường — `alida` và `Alida` là một topic.
- `limit` thay cho `hashtag_to_topic.limit`, áp cho mọi tên **có biến** (tên cố định trong `to` không bị giới hạn, như `map` hôm nay).
- `strip: matched` tổng quát hoá `strip: true`: bỏ hashtag đã định tuyến, hoặc đoạn regex đã khớp (chỉ khi `from: text`). Có `strip` thì tin phải gửi lại (D3 như mọi tin gửi lại), giống hôm nay.
- Cú pháp cũ `hashtag_to_topic` vẫn chạy, là **cú pháp tắt** dịch sang `route` (như `--caption` dịch sang danh sách thao tác): `map` → luật `when: {hashtag: [...]}` + tên cố định; `auto: [..]` → như vậy với `tag_title`; `auto: all` → `from: hashtags, pick: first, skip, to: "{value|tag_title}"`. Cặp đã lưu transform dạng cũ không bị coi là "đổi transform": so sánh sau khi dịch cả hai về `route`.
- **Một tin (một unit) một topic** (chốt 2026-10-18, Q1): không nhân bản tin vào nhiều topic; `msg_map` giữ một bản sao mỗi tin nguồn.
- **Cùng luật cho cả topic lẫn hashtag** (chốt 2026-10-18, Q3): tên mà `route` cho ra là một **nhãn**; loại đích quyết định nhãn thành gì. Đích là forum → topic có tên đó. Đích không phải forum → một hashtag viết theo `hashtag_format` (mặc định `#{slug}`), gắn vào caption ở đúng chỗ `topic_as_hashtag` dùng hôm nay (`CaptionPolicy.hashtag`), nên tin phải gửi lại (D3 như mọi tin gửi lại). Một file transform vì vậy chạy được cho cả hai loại đích; `route` không còn bị từ chối khi đích không phải forum (khác `hashtag_to_topic` hôm nay, vẫn đòi forum). Với nhãn là hashtag, `limit` không áp (không tạo gì ở đích), `default` cũng thành hashtag.

**Xem trước — bắt buộc phải có, vì regex là thứ dễ sai nhất:** `inspect --plan clone --transform t.yaml` thêm bảng **"topic đích"** (topic → số tin, đánh dấu topic sẽ được tạo mới, số tin rơi vào `default`/General) và mẫu vài tin **không khớp luật nào** kèm caption, để sửa regex trước khi ghi gì. Màn hình Khám phá có cột tương ứng khi đã nạp transform.

**Không làm** (giữ nguyên tắc ở 5.2): không biểu thức tự do, không script, không bộ lọc tên do người dùng định nghĩa. Mọi luật kiểm được khi nạp file (regex biên dịch được, mẫu chỉ dùng nhóm có trong regex, bộ lọc có thật) và giải thích được trong một dòng.

**Hai ca của người dùng với cơ chế này:**

| Ca | Luật |
|---|---|
| 1 — nhiều hashtag | `pick: last`; hoặc `regex: '^#(?!YY)'` để chỉ giữ ứng viên đúng mẫu; hoặc luật `when: {hashtag: [...]}` đặt trước cho hashtag ưu tiên |
| 2 — tên file trong caption | `from: text` + `regex` có nhóm `name` + `to: "{name|title}"`; tin không khớp rơi vào `default` |

### 11.3 Gộp và chia topic

**Khi sao chép (clone/restore sang một forum khác)** — không cần lệnh mới:

- **Gộp**: hai topic nguồn cho ra một tên (`topic_map` cùng tên — đã có từ 19b; hoặc `when: {topic: [A, B]}` → một `to`).
- **Chia**: luật `when: {topic: [A], ...}` hoặc `from: ...` với điều kiện topic; tin còn lại của A đi theo ánh xạ topic như cũ.
- Thứ tự trong từng topic đích đúng thứ tự nguồn (một lần quét theo id tăng dần).

**Tại chỗ, trên một forum đang có** — giới hạn của Telegram quyết định (mục 6.1): theo hiểu biết hiện tại **không có API chuyển tin sang topic khác** (spike 4, chưa xác minh). Nên:

1. **Dựng lại**: clone forum → forum mới với `route`/`topic_map`, kiểm tra, đổi username. Không mất gì, chạy được từ 11.2. Vẫn là đường nên gợi ý cho một forum rất lớn.
2. **Gộp và chia tại chỗ** (Phase 20b, chốt 2026-10-18, Q5) — dựng trên `repost` + `delete` của Phase 20, cùng plan → xác nhận → apply (D10). Ngữ nghĩa người dùng chốt:

   **Chia** — `tgmirror topics split <forum> "A" --transform t.yaml` (hoặc `--selection`): những tin của A mà `route` cho ra một tên khác A được **đăng lại vào topic mới** (tạo theo tên, hoặc topic có sẵn cùng tên) **theo đúng thứ tự gốc** của chúng, rồi **xoá khỏi A**. Tin còn lại của A giữ nguyên chỗ và thứ tự. Một lần chia có thể cho ra nhiều topic mới; mỗi topic mới chứa các tin của nó theo thứ tự id tăng dần ở A.

   ```
   A: a1 b1 a2 b2 a3      route: b* → "B"
   ⇒ A: a1 a2 a3          B (mới): b1 b2
   ```

   **Gộp** — `tgmirror topics merge <forum> "Topic 1" "Topic 2" [...]`: mọi tin của topic sau được **đăng lại vào topic đầu, nối tiếp sau các tin đã có của nó**, giữ thứ tự của topic sau; xong thì **xoá topic sau** (cả topic, không còn lại vỏ rỗng). Nhiều topic thì nối lần lượt theo thứ tự đã gõ.

   ```
   Topic 1: t1a t1b       Topic 2: t2a t2b t2c
   ⇒ Topic 1: t1a t1b t2a t2b t2c          (Topic 2 bị xoá)
   ```

   Chi tiết an toàn (theo mục 6.3, không có gì riêng của topic được nới ra):
   - Plan đóng băng danh sách id cần chuyển; xác nhận bằng gõ lại số tin (hoặc `--yes --expect N`); lưới an toàn backup hỏi Có/Không như `delete` (D3 cho nguồn `noforwards` như mọi backup).
   - Từng unit (album nguyên vẹn, luật 4): đăng lại (im lặng mặc định) → ghi `new_msg_id` trong `op_items` → **chỉ sau đó** xoá bản gốc. Crash giữa chừng: reconcile dò bản đã đăng ở topic đích (như reconcile của run) nên không đăng trùng, và không bao giờ xoá một tin chưa có bản mới.
   - Đăng lại đi đường của `repost` (forward không tác giả, hoặc gửi lại/tải lại khi forward không được), nên tin chuyển sang **mất views, reactions, bình luận, nhãn thời gian gốc**, và link/reply tới id cũ hỏng; reply *giữa* các tin cùng được chuyển được nối lại qua `op_items` như `reply: keep`. Plan nói thẳng những điều này.
   - **Xoá topic sau khi gộp** chỉ chạy khi mọi unit đã `done` **và** đọc lại topic đó không còn tin nào ngoài plan (tin mới đăng vào giữa lúc chạy): còn thì không xoá topic, báo số tin còn lại và lệnh để gộp tiếp. Xoá topic là `channels.deleteTopicHistory`; không bao giờ xoá General (gộp *vào* General được, gộp General vào topic khác thì từ chối — General không xoá được).
   - Không đổi được thứ tự *xen kẽ* với tin đã có: tin đăng lại luôn nằm **sau** mọi tin hiện có của topic đích (giới hạn của Telegram, mục 6.1). Với chia, topic mới thì không có tin nào trước nên thứ tự tái hiện đúng hoàn toàn; chia vào một topic **đã có tin** thì tin chuyển nối ở cuối — plan cảnh báo.
3. Thao tác chỉ trên topic, rẻ và thật sự tại chỗ: `topics rename`, `topics close/reopen`, `topics delete` (cả lịch sử của topic; plan + gõ lại số tin như `delete`).

### 11.4 Sửa tin

Là `edit` của Phase 20 (mục 6), không phải cơ chế mới. Bổ sung:

- `edit` nhận cùng transform, nhưng chỉ phần `caption:` (+ `links: rewrite`); có `route`/`topics` thì **từ chối** (mã 2) và chỉ sang `repost`/`topics split` — sửa tin không đổi được topic.
- **Sửa trên đích của một cặp** theo `msg_map` (`edit --mirror M3` / từ một lần chạy): áp thêm một transform lên các bản sao đã đăng, khỏi clone lại. Cũng là lượt **sửa bù link tiến** mà 19c3 để lại (`links_left`, mục 5.3).
- Undo như mục 6.3 (lưu text + entity gốc).

### 11.5 Lộ trình đề xuất

| Phase | Nội dung | Hoàn thành khi | Release |
|---|---|---|---|
| 21 | `topics.route` (mục 11.2), `hashtag_to_topic` thành cú pháp tắt, `strip: matched`, bảng "topic đích" trong `inspect --plan`, wizard/menu chọn luật cơ bản | Hai ca của người dùng chạy đúng trên kênh thật: nhiều hashtag với `pick: last`, tên file trong caption → đúng topic; transform 19b cũ cho đúng kết quả cũ; cùng file transform sang kênh thường cho hashtag đúng tên | 0.3.0 (chốt Q4) |
| 20 | Như mục 8, cộng `edit` theo `msg_map` (11.4) | Như mục 8 | 0.3.0 |
| 20b | `topics split/merge` theo ngữ nghĩa 11.3 (chia: topic mới đúng thứ tự gốc, xoá khỏi topic cũ; gộp: nối topic sau vào cuối topic trước rồi xoá topic sau), `topics rename/close/delete` | Trên forum thử: chia một topic thành hai, gộp lại; không sót, không trùng, thứ tự đúng như 11.3; kill giữa chừng rồi chạy lại không đăng trùng, không xoá tin chưa có bản mới; tin mới đăng vào topic sau giữa lúc gộp thì topic đó không bị xoá | 0.3.0 |
| 17 | Như cũ | | 0.3.x |

**Trạng thái 2026-10-18**: Phase 21 xong code (kể cả wizard, cột Khám phá, cờ tinh chỉnh và `--dst-no-topics`), chưa thử thật; các điểm khác bản đề xuất ở `06-lo-trinh.md`, "Phase 21 — ghi chú".

Thứ tự: **21 → 20 → 20b → 17**, cả ba (21, 20, 20b) trong **0.3.0** (chốt 2026-10-18, Q4). Phase 21 không ghi/xoá tại chỗ nên không cần D10, làm trước được.

Spike thêm (mục 9): `channels.editForumTopic`, `channels.deleteTopicHistory` (tốc độ, FloodWait, có xoá được topic General không); spike 4 (chuyển tin giữa topic) trở thành điều kiện trước của 20b.

### 11.6 Câu hỏi đã chốt (người dùng trả lời 2026-10-18)

| # | Câu hỏi | Trả lời |
|---|---|---|
| 1 | Tin nhiều hashtag: một topic theo luật chọn, hay nhân bản vào mọi topic khớp? | **Một tin một topic** (không đổi `msg_map`) |
| 2 | Ca tên file trong caption | "Không có đường" nghĩa là *không có cách* (không phải "không có đuôi"); **giải theo hướng đã đề xuất** (`from: text` + `regex` + `to`) |
| 3 | Tên do `route` cho ra dùng làm hashtag khi đích không phải forum? | **Có**: `route` dùng cho cả topic lẫn hashtag (11.2, "Cùng luật cho cả topic lẫn hashtag") |
| 4 | Phase 21 ra 0.2.1 hay 0.3.0? | **Gom vào 0.3.0** |
| 5 | Gộp/chia tại chỗ? | **Có, làm 20b**: chia → tái hiện thứ tự ở topic mới, xoá các tin đã chuyển khỏi topic cũ; gộp → tái hiện thứ tự của topic sau vào (cuối) topic trước, ví dụ `[topic 1..., topic 2...]`, rồi xoá luôn topic sau (11.3) |
