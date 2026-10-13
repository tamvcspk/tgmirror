# 07 — Kế hoạch 0.2/0.3: Inspect, Transform, thao tác hàng loạt

**Trạng thái: đã duyệt hướng** (đề xuất 2026-10-12, người dùng trả lời cả 7 câu hỏi cùng ngày, xem mục 10). **Phase 18a đã có code (2026-10-12)** — chi tiết đã chuyển sang `01-kien-truc.md` ("Khám phá"), `02-cli-ux.md` ("Khám phá nguồn") và `03-filters.md` ("Selection", predicate mới); những chỗ làm khác với bản đề xuất dưới đây ghi ở `06-lo-trinh.md`, "Phase 18a — ghi chú" (đọc mục đó trước khi dựa vào mục 3–4 và 8 bên dưới). **Phase 19 đã bắt đầu (2026-10-13), theo ba lát cắt 19a/19b/19c; 19a (mục 5.2: chuỗi thao tác caption và mô hình `Transform`) và 19b (mục 5.1: hashtag → topic, `topic_map`, định dạng hashtag) đã có code, chi tiết ở `02-cli-ux.md` ("Transform"), `01-kien-truc.md` ("Định tuyến theo hashtag") và `06-lo-trinh.md` ("Phase 19 — ghi chú", kể cả chỗ làm khác với mục 5.1, 5.2 và 5.4 bên dưới); 19c (giữ reply, `forward_header`, link nội bộ) chưa có dòng code nào.** Phase 18b, 20 chưa có dòng code nào. Phase 18–20 đã vào bảng lộ trình của `06-lo-trinh.md`; D10 đã vào `00-tong-quan.md`. Khi từng phase bắt đầu thì chuyển chi tiết của nó sang các doc 01–04 tương ứng, như đã làm với "Kế hoạch Phase 9–16".

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
| giữ reply | không (forward không giữ) | mới, chỉ khi tin đi đường gửi lại (B/REFERENCE): `reply_to` ánh xạ qua `msg_map`; tin được reply nằm ngoài selection → bỏ reply |
| giữ/bỏ header "Forwarded from" | luôn bỏ (`drop_author=True`, D2) | tùy chọn `forward_header: keep` → `drop_author=False`. Cũng là câu trả lời cho "biến forward thành tin của kênh" **khi clone**: điều đó đã là mặc định |

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

Trong file selection (`transform:`), hoặc file riêng `--transform t.yaml`; cờ tắt cho ca phổ biến (`--hashtag-to-topic auto`, `--topic-map "Cũ=Mới"`, `--keep-replies`, `--rewrite-links`). Transform được nhớ theo cặp (`mirrors.options_json`), giống filter: `run` dùng lại, đổi transform giữa chừng thì cảnh báo rằng tin đã sao chép không bị sửa lại (không quét lại như đổi filter).

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

1. `messages.getSearchCounters`: các loại nào, có áp `min_id`/`max_id` không (nhớ spike 10: `count` bỏ qua khoảng), độ chính xác so với quét.
2. `channels.deleteMessages`: tốc độ, FloodWait khi xoá hàng nghìn tin; xoá tin trong supergroup/forum của người khác; xoá có làm đổi id/thứ tự gì không.
3. `messages.editMessage`: sửa tin cũ trong broadcast/supergroup/forum (giới hạn thời gian?), sửa tin forward, sửa caption album, người theo dõi có nhận thông báo không, nhãn "đã sửa".
4. Chuyển tin giữa các topic: xác nhận thật sự không có API (nếu có thì `repost` cho hashtag → topic tại chỗ không cần nữa).
5. Đăng im lặng (`silent`) cho `repost` hàng loạt; `forward_header: keep` (`drop_author=False`) có giữ album/topic như `drop_author=True` không.
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
