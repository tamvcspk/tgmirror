# 03 — Filters

## Ngữ nghĩa

```yaml
include:          # rỗng = lấy tất cả
  - {media: [video, photo], hashtag: ["#xxx"]}    # các predicate TRONG một rule: AND
  - {regex: "giveaway"}                            # các rule: OR
exclude:          # nếu khớp bất kỳ rule nào → loại (NOT)
  - {regex: "quảng cáo|ads"}
date: {from: 2024-01-01, to: 2025-01-01}   # điều kiện toàn cục, AND với include
id:   {from: 1000, to: 50000}
album: any        # any | all | first  — cách đánh giá filter cho album
```

Một Unit (tin đơn hoặc album) được clone khi: `global_ok AND (include rỗng OR khớp >= 1 include) AND NOT khớp exclude`.
Service message (join, pin, đổi tên...) luôn bị bỏ qua.

Các điểm đã chốt khi làm phase 3 (không phải D1–D9):

- **Một rule = một tin.** Mọi predicate của một rule áp lên *cùng một tin*. Với album, `{media: [photo], hashtag: ["#x"]}` không khớp album `[video (caption #x), photo]` vì không có tin nào thỏa cả hai; muốn khớp thì đổi thành hai rule hoặc bỏ `media`.
- **Thiếu thuộc tính thì predicate sai**, kể cả `max`: `size`/`duration`/`mime` của tin chỉ có chữ, `views` của tin trong group. Nên `exclude: {size: {min: 2GB}}` vẫn giữ tin chữ, còn `include: {size: {max: 10MB}}` loại tin chữ.
- **`date` là nửa mở `[from, to)`**, UTC (ngày không kèm giờ = 00:00 UTC; giờ không kèm múi giờ coi là UTC), nên một năm là `2024-01-01` → `2025-01-01`. **`id` tính cả hai đầu.** Unit được xét theo **tin đầu tiên** của nó (id và date).
- Rule rỗng (`{}`) bị từ chối, vì làm `include` thì khớp tất cả, làm `exclude` thì loại tất cả. Khóa lạ cũng bị từ chối (báo đúng vị trí, ví dụ `include[0].media[1]`).
- Filter được kiểm tra trước khi chạy: regex sai, đơn vị thiếu, ngày sai... đều là lỗi mã 2, chưa có gì bị ghi.

## Predicate

| Predicate | Giá trị | Ghi chú |
|---|---|---|
| `media` | `photo video audio voice document gif sticker video_note poll geo contact game invoice webpage text` | `text` = tin chỉ có chữ (tin có link preview là `webpage`; muốn cả hai: `[text, webpage]`). `document` = tệp không thuộc loại nào khác, cùng các loại lạ (dice, ...). Loại `game`/`invoice` chủ yếu để `--exclude-media` |
| `hashtag` | list `"#tag"` (một chuỗi = list một phần tử) | Khớp entity `MessageEntityHashtag` (không phải `#` trong text), không phân biệt hoa/thường, nguyên thẻ (`#news` không khớp `#newsletter`), any-of. Chấp nhận có hoặc không có `#`, lưu dạng `#chữthường`. `#tag@kênh` trong group chỉ lấy `#tag`. Trên một nguồn phục hồi từ backup (`tgmirror restore`, Phase 11b) đây là **gần đúng**: `BackupReader` dò `#tag` bằng regex trên text đã bỏ thẻ HTML thay vì entity thật, vì backup không giữ entity riêng — chỉ ảnh hưởng bộ lọc, không ảnh hưởng nội dung được restore |
| `contains` | list chuỗi | Trên text + caption, chuỗi con, không phân biệt hoa/thường, any-of |
| `regex` | pattern | Trên text + caption. Thư viện `regex` (không phải `re`), tìm kiếm (`search`, không cần khớp cả chuỗi), phân biệt hoa/thường (dùng `(?i)`). Biên dịch khi tạo filter; mỗi lần tìm có timeout 0,5 giây: quá hạn thì lần chạy dừng `failed` với lỗi filter nêu id tin và pattern (không bỏ tin âm thầm). Ghi chú: `regex` tự tối ưu vài mẫu kinh điển như `(a+)+$` |
| `has_caption` | bool | `true` khi text/caption khác rỗng (tin chữ cũng có) |
| `size` | `{min,max}` (`500MB`, `2GB`) | Kích thước file media, byte, `1KB` = 1024 B. **Bắt buộc có đơn vị** (số trần bị từ chối); lưu trong DB dạng `"<byte>B"`. Ảnh: thumbnail lớn nhất, xấp xỉ |
| `duration` | `{min,max}` | Video/audio/voice. Số giây, hoặc `90s`, `5m`, `1h30m` |
| `mime` | list | vd `video/mp4`; cho phép `video/*` |
| `views` | `{min,max}` | Kênh broadcast có số views |
| `from_user` | list id (một số = list một phần tử) | Nguồn group/forum, any-of. **Chỉ nhận id** (không `@username`): giữ `filters/model.py`/`matcher.py`/`parser.py` không đụng I/O. Tin của nguồn broadcast không có id người gửi nên predicate luôn sai (thiếu thuộc tính) |
| `topic` | list id (một số = list một phần tử) | Chỉ forum, any-of. **Chỉ nhận id**, không tên topic — `tgmirror topics <src>` tra id/tên. Tin ở General của forum không có topic id ở tầng Telethon (giống nguồn không phải forum); `topic: 1` (General, như `tgmirror topics` và wizard hiển thị) vẫn khớp chúng: matcher coi tin không có topic id là General |

`date` và `id` là điều kiện toàn cục (đẩy xuống server, xem dưới).

## Album

Hashtag/caption thường chỉ nằm ở một tin trong album. `album: any` (mặc định) nghĩa là album khớp nếu **bất kỳ** tin nào khớp, và cả album được clone. `all`: mọi tin phải khớp (một rule nào đó). `first`: chỉ xét tin đầu.
Exclude luôn dùng `any` (một tin bị loại thì cả album bị loại) để an toàn.

## Server pushdown

Để không phải quét cả kênh, `filters/pushdown.py` (`plan_read`) đẩy phần đơn giản xuống Telegram:

| Điều kiện | Tham số Telethon | Ghi chú |
|---|---|---|
| include có đúng một rule, `media` đúng một loại trong `photo video audio voice gif video_note` | `filter=InputMessagesFilter` `Photos/Video/Music/Voice/Gif/RoundVideo` | `document`, `sticker`, `webpage`... **không** đẩy: loại của mình rộng hơn hoặc không có filter tương ứng an toàn |
| include có một rule với đúng một hashtag | `search="#tag"` | `contains` **không** đẩy: `search` của Telegram khớp theo từ, còn `contains` là chuỗi con, đẩy xuống sẽ mất tin hợp lệ |
| `date.from` / `date.to` | Gateway đổi ngày thành id bằng một lời gọi `get_messages(offset_date=..., reverse=True, limit=1)` rồi dùng `min_id`/`max_id` | Không dùng `offset_date` của `iter_messages`: với `search`/`filter` Telethon coi nó là `max_date` và hỏng khi `reverse=True` |
| `id.from` / `id.to` | `min_id` / `max_id` | Telethon **loại trừ** `max_id` nên gateway truyền `to + 1` |

Quy tắc: pushdown chỉ được **thu hẹp an toàn** (không bao giờ loại nhầm tin hợp lệ) và **không bao giờ cắt album** (luật 4). Vì vậy:

1. **Lề album.** Ranh giới id/date được nới `ALBUM_MARGIN` = 10 id (album tối đa 10 tin, id liên tiếp): bắt đầu sớm 10 id, dừng muộn 10 id, để album nằm trên biên vẫn nguyên vẹn; matcher xét lại theo tin đầu của unit nên kết quả vẫn chính xác.
2. **Hoàn thiện album.** `filter`/`search` của server chỉ trả những tin *tự nó* khớp, bỏ các tin anh em trong cùng album (caption/hashtag chỉ ở tin đầu). Nên khi đẩy `media`/`search`, planner đọc thêm một cửa sổ **không lọc** quanh mỗi album (một lời gọi thêm cho mỗi album) rồi mới đánh giá. Vẫn rẻ hơn quét cả kênh khi tin khớp thưa.
3. **Luôn chạy lại client matcher** trên kết quả, vì `search` của Telegram khớp tiền tố/tokenize khác mình và `filter` có thể bao gồm loại gần đúng.
4. **`--no-pushdown`** (tùy chọn của `new`, lưu ở `options.pushdown`): đọc mọi tin sau cursor và lọc ở máy. Chậm hơn nhưng là "sự thật" để đối chiếu với pushdown trên tài khoản thật.
5. Giả định: ngày tăng theo id (Telegram cũng dựa vào điều này cho `offset_date`). Kênh được import lịch sử có thể vi phạm.

Nhiều rule OR hoặc `regex`/`contains` → không đẩy phần đó (quét đầy đủ + lọc client), hoặc chạy nhiều lượt pushdown rồi hợp nhất theo `id` (tối ưu ở phase sau). Test đối chiếu pushdown với quét đầy đủ: `tests/integration/test_pushdown_equivalence.py` (kênh ngẫu nhiên có album, nhiều filter; đã thử đột biến: bỏ hoàn thiện album hoặc bỏ lề thì test đỏ). Hành vi thật của Telegram (spike 3) vẫn cần người dùng kiểm: xem `06-lo-trinh.md`.

## CLI shorthand

```
--media video,photo        --hashtag "#xxx" (lặp được = OR)
--contains "từ khóa" (lặp được = OR)        --regex "pattern"
--exclude-regex "pattern" (lặp được)        --exclude-media sticker
--since 2024-01-01         --until 2025-01-01
--min-size 10MB            --max-size 2GB
--from-user 12345 (lặp được = OR, chỉ id)   --topic 7 (lặp được = OR, chỉ id; `tgmirror topics <src>` tra id)
--album any|all|first
--filter-file filters.yaml  (không trộn với các flag lọc trên; nếu trộn thì báo lỗi rõ ràng, mã 2)
--pushdown / --no-pushdown  --preview / --no-preview   (không phải filter, chỉ của `new`)
```

Ghép cờ thành filter: các cờ "dương" (`--media`, `--hashtag`, `--contains`, `--regex`, `--min-size`, `--max-size`, `--from-user`, `--topic`) tạo **một** rule `include` (AND); mỗi cờ `--exclude-*` là **một rule `exclude` riêng** (OR). Việc phức tạp hơn (nhiều rule include, `mime`, `duration`, `views`, `id`, `has_caption`) dùng YAML. Wizard thu thập cùng các giá trị chuỗi này rồi qua cùng `from_flags`, nên cờ, YAML và wizard cho ra cùng một filter (có test); bước filter của wizard còn có checkbox chọn topic khi nguồn là forum (tự lấy id qua `list_topics`).

Filter được lưu (JSON đã chuẩn hóa: hashtag chữ thường, size dạng `"<byte>B"`, ngày UTC ISO, giá trị mặc định bỏ đi; `{}` là không lọc) trong `mirrors.filters_json` (và ghi lại ở `runs.filters_json` của mỗi lần chạy), nạp lại được (có test round-trip).

## Xem trước

`tgmirror clone` đọc thử 100 tin đầu của khoảng đã chọn (chỉ áp cận `id`/`date`, **không** thu hẹp theo nội dung để mẫu không bị lệch) và cho matcher thật đánh giá: "X trong 100 tin đầu sẽ được sao chép" kèm vài dòng caption mẫu. Mặc định hiện khi có terminal, có filter và không có `--yes`; `--preview`/`--no-preview` ép bật/tắt. Trên terminal, sau bản xem trước là câu hỏi duy nhất "Sao chép … ngay bây giờ?"; trả lời không thì thoát mã 1 và chưa tạo kênh nào, chưa ghi gì (xem trước chạy trước khi tạo đích). Không xem trước khi giữ nguyên filter cũ (không có gì mới để xem).

## Filter khi chạy lại cùng cặp nguồn/đích

Filter được **nhớ** theo cặp nguồn/đích (`mirrors.filters_json`). Chạy lại `tgmirror clone --src ... --dst ...` (hoặc `tgmirror run`):

- **Không có cờ lọc** → dùng lại filter của lần trước và chỉ lấy tin mới hơn con trỏ (delta). Một dòng thông báo nói rõ ("Dùng lại filter của lần chạy trước").
- **Có cờ lọc hoặc `--filter-file` khác** → thay filter, đặt `cursor_src_id` về 0 và quét lại nguồn từ đầu (thay cho `--refilter` cũ). Tin đã `done` được bỏ qua nhờ `msg_map` (không sao chép hai lần); tin mới khớp nhưng chưa sao chép được **thêm vào cuối kênh đích**, nên thứ tự ở đích không còn theo thời gian. Bộ đếm `skipped_filter` là của từng lần chạy nên tự bắt đầu lại. Cùng filter nhập lại (giống hệt bản đã nhớ) vẫn là delta.
- **`--no-filter`** → bỏ filter đã nhớ (cũng là quét lại từ đầu, không sao chép trùng). Không dùng chung với cờ lọc (mã 2).

Wizard: với cặp đã từng clone, bước filter có thêm lựa chọn đầu tiên "Giữ filter của lần chạy trước" (tương đương không đưa cờ lọc). Chỉ có logic này ở `Store.start_run`; đây là chỗ **duy nhất** con trỏ được lùi (xem `04-state-checkpoint.md`). Nếu tiến trình khác đang chạy cặp đó (heartbeat còn mới) thì từ chối, không đổi gì.
