# 03 — Filters

## Ngữ nghĩa

```yaml
include:          # rỗng = lấy tất cả
  - {media: [video, photo], hashtag: ["#xxx"]}    # các predicate TRONG một rule: AND
  - {regex: "giveaway"}                            # các rule: OR
exclude:          # nếu khớp bất kỳ rule nào → loại (NOT)
  - {regex: "quảng cáo|ads"}
  - {size: {min: 2GB}}
date: {from: 2024-01-01, to: 2025-01-01}   # điều kiện toàn cục, AND với include
id:   {from: 1000, to: 50000}
album: any        # any | all | first  — cách đánh giá filter cho album
```

Một Unit (tin đơn hoặc album) được clone khi: `global_ok AND (include rỗng OR khớp >= 1 include) AND NOT khớp exclude`.
Service message (join, pin, đổi tên...) luôn bị bỏ qua.

## Predicate

| Predicate | Giá trị | Ghi chú |
|---|---|---|
| `media` | `photo video audio voice document gif sticker video_note poll geo contact game invoice webpage text` | `text` = tin chỉ có chữ. Loại `game`/`invoice` chủ yếu để `--exclude-media` |
| `hashtag` | list `"#tag"` | Khớp entity `MessageEntityHashtag` (không chỉ regex), không phân biệt hoa/thường, any-of |
| `contains` | list chuỗi | Trên text + caption, any-of |
| `regex` | pattern | Trên text + caption; biên dịch một lần, có giới hạn độ phức tạp/timeout |
| `has_caption` | bool | |
| `size` | `{min,max}` (`500MB`, `2GB`) | Kích thước file media |
| `duration` | `{min,max}` giây | Video/audio/voice |
| `mime` | list | vd `video/mp4` |
| `views` | `{min,max}` | Kênh broadcast có số views |
| `from_user` | id/username | Dùng cho group/supergroup/forum; kênh broadcast ẩn danh |
| `topic` | id/tên topic | Chỉ forum; giới hạn phần clone vào một số topic |

`date` và `id` là điều kiện toàn cục (đẩy xuống `offset_date`/`min_id`/`max_id`).

## Album

Hashtag/caption thường chỉ nằm ở một tin trong album. `album: any` (mặc định) nghĩa là album khớp nếu **bất kỳ** tin nào khớp, và cả album được clone. `all`: mọi tin phải khớp. `first`: chỉ xét tin đầu.
Exclude luôn dùng `any` (một tin bị loại thì cả album bị loại) để an toàn.

## Server pushdown

Để không phải quét cả kênh, `filters/pushdown.py` đẩy phần đơn giản xuống Telegram:

| Điều kiện | Tham số Telethon |
|---|---|
| include có đúng một rule, `media` là một loại | `filter=InputMessagesFilterPhotos/Video/Document/Music/Voice/Gif/RoundVideo/Url...` |
| include có một rule với đúng một hashtag/keyword | `search="#xxx"` |
| `date.from` | `offset_date` (kèm `reverse=True`) |
| `id.from` / `id.to` | `min_id` / `max_id` |

Quy tắc: pushdown chỉ được **thu hẹp an toàn** (không bao giờ loại nhầm tin hợp lệ). Dù đã pushdown, **luôn chạy lại client matcher** trên kết quả, vì `search` của Telegram khớp chuỗi con/tokenize khác mình và `filter` có thể bao gồm loại gần đúng.
Nhiều rule OR hoặc `regex` → không pushdown phần đó (quét đầy đủ + lọc client), hoặc chạy nhiều lượt pushdown rồi hợp nhất theo `id` (tối ưu ở phase sau).

## CLI shorthand

```
--media video,photo        --hashtag "#xxx" (lặp được = OR)
--contains "từ khóa"       --regex "pattern"
--exclude-regex "pattern"  --exclude-media sticker
--since 2024-01-01         --until 2025-01-01
--min-size 10MB            --max-size 2GB
--filter-file filters.yaml  (không trộn với các flag trên; nếu trộn thì báo lỗi rõ ràng)
```

Filter được lưu (JSON đã chuẩn hóa) trong `jobs.filters_json`. Đổi filter của job đã chạy = tạo job mới hoặc `tgmirror run --refilter` (backfill từ `cursor=0`, bỏ qua tin đã `done` nhờ `msg_map`).
