"""Every user-facing string, in one place so it can be translated (skill ``cli-wizard``).

Vietnamese is the default; English is the fallback for a missing key and is selected with
``TGMIRROR_LANG=en``. Keep both dictionaries in step: a test compares their keys.
"""

import os
from collections.abc import Mapping

ENV_LANG = "TGMIRROR_LANG"

VI: dict[str, str] = {
    # login / logout / whoami
    "login.api_intro": "Cần api_id và api_hash của bạn. Tạo tại https://my.telegram.org/apps.",
    "login.prompt_api_id": "api_id",
    "login.prompt_api_hash": "api_hash (không hiện khi nhập)",
    "login.api_id_invalid": "api_id phải là một số nguyên dương.",
    "login.api_saved": "Đã lưu api_id/api_hash vào {path}.",
    "login.prompt_phone": "Số điện thoại (quốc tế, ví dụ +84901234567)",
    "login.prompt_code": "Mã đăng nhập Telegram vừa gửi (không hiện khi nhập)",
    "login.prompt_password": "Mật khẩu xác minh hai bước (không hiện khi nhập)",
    "login.code_sent": "Đã gửi mã đăng nhập tới ứng dụng Telegram (hoặc SMS) của bạn.",
    "login.code_expired": "Mã đã hết hạn, đã gửi mã mới.",
    "login.code_invalid": "Mã không đúng, thử lại.",
    "login.phone_invalid": "Số điện thoại không hợp lệ, thử lại.",
    "login.password_invalid": "Mật khẩu không đúng, thử lại.",
    "login.already": "Đã đăng nhập: {who}.",
    "login.ok": "Đăng nhập thành công: {who}.",
    "logout.ok": "Đã đăng xuất {who}.",
    "logout.none": "Chưa đăng nhập, không có gì để đăng xuất.",
    "whoami.line": "{who} (id {id})",
    # channels
    "channels.empty": "Không có kênh/nhóm nào khớp.",
    "channels.count": "{count} kênh/nhóm.",
    "col.kind": "Loại",
    "col.title": "Tên",
    "col.id": "ID",
    "col.members": "Thành viên",
    "col.noforwards": "Cấm lưu",
    "col.post": "Post",
    "yes": "có",
    "no": "không",
    "admin": "admin",
    # new (wizard steps 1-2)
    "clone.pick_source": "Chọn kênh/nhóm nguồn (gõ để lọc)",
    "clone.pick_destination": "Chọn đích (gõ để lọc)",
    "clone.create_new": "+ Tạo kênh mới",
    "clone.prompt_title": "Tên kênh mới",
    "clone.prompt_about": "Mô tả (có thể để trống)",
    "clone.no_candidates": "Không có đích có sẵn phù hợp, sẽ tạo mới.",
    "clone.src": "Nguồn: {channel}",
    "clone.dst": "Đích:  {channel}",
    "clone.dst_created": "Đích:  {channel} (vừa tạo)",
    "kind.broadcast": "kênh",
    "kind.supergroup": "supergroup",
    "kind.forum": "forum",
    "kind.group": "nhóm",
    # run / pause / stop
    "run.start": "Lần chạy {id}: {src} → {dst}, tiếp từ tin nguồn {cursor}.",
    "run.progress": (
        "Lần chạy {id}: {done} tin đã sao chép, {failed} lỗi (tin nguồn tới id {cursor})."
    ),
    "run.stopping": "Đang dừng sau batch hiện tại (Ctrl+C lần nữa để thoát ngay).",
    "run.result": "Lần chạy {id}: {status}. {done} tin đã sao chép, {failed} lỗi.",
    "run.reconciled": (
        "Lần chạy trước bị ngắt giữa chừng: {count} tin đã có ở đích, ghi nhận và không gửi lại."
    ),
    "run.reconcile_resend": (
        "Lần chạy trước bị ngắt giữa chừng: {count} tin chưa được gửi, sẽ gửi lại."
    ),
    "run.reconcile_ambiguous": (
        "Lần chạy trước bị ngắt giữa chừng và không xác định được {count} tin đã gửi chưa; "
        "gửi lại nên đích có thể bị trùng vài tin."
    ),
    "run.flood_stopped": (
        "Telegram yêu cầu chờ {seconds}s. Lần chạy đã lưu ở trạng thái chờ đến "
        "{resume_at}; chạy lại `tgmirror run` sau đó (hoặc `tgmirror run --wait` để "
        "chờ luôn)."
    ),
    "run.flood_waiting": (
        "Telegram yêu cầu chờ {seconds}s; đang chờ rồi gửi lại đúng batch đó "
        "(Ctrl+C để dừng, tiến độ đã lưu)."
    ),
    "run.throttled": (
        "Telegram giới hạn liên tiếp: tạm thời chỉ gửi {batch_size} tin mỗi lần và nghỉ "
        "{delay}s giữa các lần, cho tới khi yên ổn trở lại."
    ),
    "status.running": "đang chạy",
    "status.paused": "tạm dừng",
    "status.stopped": "đã dừng",
    "status.waiting_flood": "chờ flood",
    "status.done": "xong",
    "status.failed": "lỗi",
    "control.pause_requested": "Đã yêu cầu tạm dừng lần chạy {id}; nó dừng sau batch hiện tại.",
    "control.stop_requested": "Đã yêu cầu dừng lần chạy {id}; nó dừng sau batch hiện tại.",
    # warnings
    "warn.noforwards_admin": (
        "Nguồn bật «Restrict saving content». Bạn là admin nên có thể tắt tùy chọn này tạm thời, "
        "hoặc dùng --mode reupload kèm xác nhận (có từ phase 6)."
    ),
    # errors (each says what to do next)
    "err.not_logged_in": "Chưa đăng nhập hoặc phiên đã hết hạn. Chạy `tgmirror login`.",
    "err.missing_credentials": (
        "Chưa có api_id/api_hash. Chạy `tgmirror login` (tương tác), hoặc đặt biến môi trường "
        "TGMIRROR_API_ID và TGMIRROR_API_HASH."
    ),
    "err.config": "Cấu hình lỗi: {detail}",
    "err.bad_api": (
        "Telegram từ chối api_id/api_hash. Kiểm tra lại tại https://my.telegram.org/apps."
    ),
    "err.flood": "Telegram yêu cầu chờ {seconds}s (FLOOD_WAIT). Thử lại sau.",
    "err.peer_flood": (
        "Telegram đánh dấu tài khoản này là spam (PEER_FLOOD). Đừng thử lại; nghỉ ít nhất 24h."
    ),
    "err.no_permission": "Không đủ quyền: {detail}",
    "err.too_many_channels": "Tài khoản đã đạt giới hạn số kênh/nhóm của Telegram.",
    "err.session_busy": (
        "File session đang được tiến trình tgmirror khác dùng. Đóng nó rồi thử lại."
    ),
    "err.transient": "Lỗi kết nối tới Telegram. Kiểm tra mạng rồi thử lại.",
    "err.generic": "{detail}",
    "err.aborted": "Đã hủy.",
    "err.no_tty": (
        "Lệnh này cần terminal tương tác (mã đăng nhập gửi qua Telegram). Chạy trong terminal."
    ),
    "err.missing_flag": "Thiếu {flag} (không có terminal để hỏi).",
    "err.conflicting_flags": "Không dùng cùng lúc {flags}.",
    "err.needs_yes": "Sẽ tạo kênh mới nên cần {flag} khi không có terminal để xác nhận.",
    "err.channel_not_found": (
        "Không thấy kênh/nhóm «{ref}» trong danh sách đã join. Xem `tgmirror channels`."
    ),
    "err.ambiguous": "«{ref}» khớp nhiều kênh: {matches}. Dùng id hoặc @username.",
    "err.source_restricted": (
        "«{title}» bật «Restrict saving content» và bạn không phải admin, nên tgmirror không "
        "sao chép (quyết định D3). Nhờ chủ kênh tắt tùy chọn này nếu họ đồng ý."
    ),
    "err.dest_not_writable": "Bạn cần là admin có quyền đăng bài ở «{title}». Chọn đích khác.",
    "err.kind_mismatch": "Nguồn là {src} còn đích là {dst}; đích có sẵn phải cùng loại với nguồn.",
    "err.same_channel": "Nguồn và đích là cùng một kênh.",
    "err.title_empty": "Tên kênh không được để trống.",
    "err.title_too_long": "Tên kênh tối đa 128 ký tự.",
    "err.about_too_long": "Mô tả tối đa 255 ký tự.",
    "err.new_unsupported": (
        "Chưa tạo được đích mới cho nguồn loại {kind} (có từ phase 8). "
        "Chọn một đích có sẵn cùng loại."
    ),
    "err.mode_unsupported": (
        "Chế độ «{mode}» chưa dùng được (reupload có từ phase 6). Dùng auto hoặc copy."
    ),
    "err.run_busy": (
        "Lần chạy {id} đang được một tiến trình khác giữ. Nếu chắc chắn nó đã chết, "
        "chạy lại với --force-takeover."
    ),
    "err.run_waiting_flood": "Telegram đã yêu cầu chờ; chưa chạy lại được trước {until}.",
    "err.run_waiting_daily_cap": (
        "Hôm nay đã gửi đủ số tin cho phép (daily_cap); chưa chạy lại được trước {until}."
    ),
    "err.daily_cap": (
        "Hôm nay đã gửi {sent} tin, chạm giới hạn ngày ({cap}). Tiến độ đã lưu; chạy "
        "lại `tgmirror run` sau {until}."
    ),
    "err.run_waiting_peer_flood": (
        "Telegram đánh dấu tài khoản là spam (PEER_FLOOD) ở lần chạy trước. Nghỉ đến "
        "{until} rồi hãy chạy lại."
    ),
    "err.forwards_restricted": (
        "Nguồn bật «Restrict saving content» nên Telegram từ chối forward. Nếu bạn là admin, "
        "tắt tùy chọn này ở nguồn; chế độ reupload có từ phase 6."
    ),
    "err.store": "Lỗi cơ sở dữ liệu: {detail}",
    "err.schema_too_new": "Cơ sở dữ liệu do bản tgmirror mới hơn tạo ra. Hãy nâng cấp tgmirror.",
    "err.filter": "Filter không hợp lệ: {detail}",
    "err.filter_mix": (
        "Không dùng --filter-file cùng các cờ lọc khác (--media, --hashtag, --since, ...)."
    ),
    # filters (wizard step 3, preview, refilter)
    "filter.pick": "Lọc nội dung sao chép?",
    "filter.keep": "Giữ filter của lần chạy trước",
    "filter.none": "Không lọc: sao chép tất cả",
    "filter.criteria": "Chọn tiêu chí",
    "filter.file": "Nạp từ file YAML",
    "filter.ask_media": "Loại media (phím cách để chọn; không chọn gì = tất cả)",
    "filter.ask_hashtags": "Hashtag, cách nhau bằng dấu phẩy (để trống = không lọc)",
    "filter.ask_contains": "Từ khóa trong nội dung, cách nhau bằng dấu phẩy (để trống = không lọc)",
    "filter.ask_since": "Từ ngày YYYY-MM-DD (để trống = từ đầu)",
    "filter.ask_until": "Đến ngày YYYY-MM-DD, không tính ngày này (để trống = tới hết)",
    "filter.ask_min_size": "Dung lượng tối thiểu, ví dụ 10MB (để trống = không giới hạn)",
    "filter.ask_max_size": "Dung lượng tối đa, ví dụ 2GB (để trống = không giới hạn)",
    "filter.ask_file": "Đường dẫn file YAML",
    "clone.preview": (
        "Xem trước: {matched} trong {scanned} tin đầu tiên của khoảng đã chọn sẽ được sao chép."
    ),
    "clone.preview_empty": "Xem trước: nguồn không có tin nào trong khoảng đã chọn.",
    "clone.preview_example": "  · {text}",
    "run.skipped": "{count} tin bị filter loại.",
    "run.progress_filtered": (
        "Lần chạy {id}: {done} tin đã sao chép, {skipped} bị filter loại, {failed} "
        "lỗi (tin nguồn tới id {cursor})."
    ),
    "clone.confirm_start": "Sao chép {src} → {dst} ngay bây giờ?",
    "clone.dst_will_be_created": "«{title}» (kênh mới sẽ được tạo)",
    "run.filter_reused": "Dùng lại filter của lần chạy trước; chỉ lấy tin mới hơn lần trước.",
    "run.filter_changed": (
        "Filter đã đổi: quét lại nguồn từ đầu. Tin đã sao chép được bỏ qua; tin khớp "
        "mà chưa sao chép sẽ được thêm vào cuối đích (thứ tự ở đích không còn theo "
        "thời gian)."
    ),
    "run.keys_hint": "Phím: [p] tạm dừng  [r] chạy tiếp  [q] dừng. Ctrl+C cũng dừng.",
    "run.paused": "Đã tạm dừng. Bấm r để chạy tiếp, q để dừng.",
    "run.resumed": "Chạy tiếp.",
    "run.continue_hint": "Chạy tiếp sau: tgmirror run {id}",
    "run.resumed_elsewhere": "Lần chạy {id} đang tạm dừng ở terminal khác; đã cho chạy tiếp ở đó.",
    "control.nothing_running": "Không có clone nào đang chạy.",
    "history.empty": "Chưa có lần chạy nào. Bắt đầu bằng `tgmirror clone`.",
    "history.title": "Các lần chạy gần đây",
    "history.col_run": "Lần",
    "history.col_started": "Bắt đầu",
    "history.col_pair": "Nguồn → đích",
    "history.col_status": "Trạng thái",
    "history.col_copied": "Đã chép",
    "history.col_failed": "Lỗi",
    "history.col_filtered": "Bị lọc",
    "history.header": "Lần chạy {id}: {src} → {dst}",
    "history.line_status": "Trạng thái:  {status}{note}",
    "history.line_time": "Bắt đầu:     {started}   Kết thúc: {ended}",
    "history.line_counts": (
        "Kết quả:     {done} đã sao chép, {failed} lỗi, {skipped} bị filter loại"
    ),
    "history.line_cursor": "Tin nguồn:   từ id {start} tới id {end}",
    "history.line_filter": "Filter:      {filter}",
    "history.no_filter": "không lọc",
    "history.failed_title": "Tin lỗi ({count}):",
    "history.failed_line": "  · tin nguồn {id}: {reason}",
    "history.failed_more": "  … và còn nữa; xem bằng --json",
    "history.floods_title": "Giới hạn từ Telegram:",
    "history.flood_line": "  · {ts} {kind} {seconds}s ({method})",
    "history.still_running": "đang chạy",
    "err.run_not_found": "Không có lần chạy «{ref}». Xem `tgmirror history`.",
    "err.run_none": "Chưa clone gì cả. Bắt đầu bằng `tgmirror clone`.",
    "clone.pick_resume": "Cặp này đã sao chép {count} tin. Làm gì tiếp?",
    "clone.resume_continue": "Tiếp tục: chỉ lấy tin mới",
    "clone.resume_fresh": "Làm lại từ đầu: sao chép lại tất cả",
    "clone.confirm_fresh": (
        "Đích đã có {count} tin từ các lần chạy trước; làm lại từ đầu sẽ sao chép lại "
        "tất cả và có thể bị trùng. Sao chép {src} → {dst} ngay bây giờ?"
    ),
    "run.fresh_started": "Làm lại từ đầu: đã quên {count} tin đã sao chép, đọc nguồn từ đầu.",
    "err.fresh_needs_yes": (
        "--fresh sẽ quên {count} tin đã sao chép và sao chép lại (đích có thể bị "
        "trùng); thêm --yes để đồng ý khi không có terminal."
    ),
}

EN: dict[str, str] = {
    "login.api_intro": (
        "Your api_id and api_hash are needed. Create them at https://my.telegram.org/apps."
    ),
    "login.prompt_api_id": "api_id",
    "login.prompt_api_hash": "api_hash (hidden while typing)",
    "login.api_id_invalid": "api_id must be a positive integer.",
    "login.api_saved": "Saved api_id/api_hash to {path}.",
    "login.prompt_phone": "Phone number (international, e.g. +14155550123)",
    "login.prompt_code": "Login code Telegram just sent (hidden while typing)",
    "login.prompt_password": "Two-step verification password (hidden while typing)",
    "login.code_sent": "A login code was sent to your Telegram app (or by SMS).",
    "login.code_expired": "The code expired; a new one was sent.",
    "login.code_invalid": "Wrong code, try again.",
    "login.phone_invalid": "Invalid phone number, try again.",
    "login.password_invalid": "Wrong password, try again.",
    "login.already": "Already logged in: {who}.",
    "login.ok": "Logged in: {who}.",
    "logout.ok": "Logged out {who}.",
    "logout.none": "Not logged in, nothing to log out of.",
    "whoami.line": "{who} (id {id})",
    "channels.empty": "No matching channels or groups.",
    "channels.count": "{count} channels/groups.",
    "col.kind": "Kind",
    "col.title": "Title",
    "col.id": "ID",
    "col.members": "Members",
    "col.noforwards": "No-fwd",
    "col.post": "Post",
    "yes": "yes",
    "no": "no",
    "admin": "admin",
    "clone.pick_source": "Pick the source channel/group (type to filter)",
    "clone.pick_destination": "Pick the destination (type to filter)",
    "clone.create_new": "+ Create a new channel",
    "clone.prompt_title": "New channel title",
    "clone.prompt_about": "Description (may be empty)",
    "clone.no_candidates": "No suitable existing destination; a new one will be created.",
    "clone.src": "Source:      {channel}",
    "clone.dst": "Destination: {channel}",
    "clone.dst_created": "Destination: {channel} (just created)",
    "kind.broadcast": "channel",
    "kind.supergroup": "supergroup",
    "kind.forum": "forum",
    "kind.group": "group",
    "run.start": "Run {id}: {src} → {dst}, continuing after source message {cursor}.",
    "run.progress": "Run {id}: {done} messages copied, {failed} failed (source up to id {cursor}).",
    "run.stopping": "Stopping after the current batch (press Ctrl+C again to quit at once).",
    "run.result": "Run {id}: {status}. {done} messages copied, {failed} failed.",
    "run.reconciled": (
        "The previous run was interrupted: {count} messages are already in the destination, "
        "recorded without sending them again."
    ),
    "run.reconcile_resend": (
        "The previous run was interrupted: {count} messages were never sent and will be sent."
    ),
    "run.reconcile_ambiguous": (
        "The previous run was interrupted and it is unclear whether {count} messages were sent; "
        "they are sent again, so the destination may get a few duplicates."
    ),
    "run.flood_stopped": (
        "Telegram asked to wait {seconds}s. The run is saved as waiting until "
        "{resume_at}; run `tgmirror run` again after that (or `tgmirror run --wait` "
        "to sit it out)."
    ),
    "run.flood_waiting": (
        "Telegram asks to wait {seconds}s; waiting, then sending that same batch again "
        "(Ctrl+C to stop, progress is saved)."
    ),
    "run.throttled": (
        "Telegram limited us repeatedly: for now batches are {batch_size} messages and the pause "
        "between them is {delay}s, until things calm down."
    ),
    "status.running": "running",
    "status.paused": "paused",
    "status.stopped": "stopped",
    "status.waiting_flood": "waiting (flood)",
    "status.done": "done",
    "status.failed": "failed",
    "control.pause_requested": "Asked run {id} to pause; it holds after the current batch.",
    "control.stop_requested": "Asked run {id} to stop; it stops after the current batch.",
    "warn.noforwards_admin": (
        "The source has 'Restrict saving content' on. You are an admin, so you can turn it off "
        "temporarily, or use --mode reupload with confirmation (available from phase 6)."
    ),
    "err.not_logged_in": "Not logged in, or the session expired. Run `tgmirror login`.",
    "err.missing_credentials": (
        "No api_id/api_hash. Run `tgmirror login` (interactive), or set the TGMIRROR_API_ID and "
        "TGMIRROR_API_HASH environment variables."
    ),
    "err.config": "Invalid configuration: {detail}",
    "err.bad_api": "Telegram rejected api_id/api_hash. Check them at https://my.telegram.org/apps.",
    "err.flood": "Telegram asks to wait {seconds}s (FLOOD_WAIT). Try again later.",
    "err.peer_flood": (
        "Telegram flagged this account as spam (PEER_FLOOD). Do not retry; rest for at least 24h."
    ),
    "err.no_permission": "Not enough rights: {detail}",
    "err.too_many_channels": "The account reached Telegram's limit on channels/groups.",
    "err.session_busy": "The session file is used by another tgmirror process. Close it and retry.",
    "err.transient": "Connection problem talking to Telegram. Check your network and retry.",
    "err.generic": "{detail}",
    "err.aborted": "Cancelled.",
    "err.no_tty": (
        "This command needs an interactive terminal (the login code arrives via Telegram)."
    ),
    "err.missing_flag": "Missing {flag} (no terminal to ask).",
    "err.conflicting_flags": "Do not use {flags} together.",
    "err.needs_yes": (
        "A new channel would be created, so {flag} is required without a terminal to confirm."
    ),
    "err.channel_not_found": (
        "No joined channel or group matches '{ref}'. See `tgmirror channels`."
    ),
    "err.ambiguous": "'{ref}' matches several chats: {matches}. Use the id or @username.",
    "err.source_restricted": (
        "'{title}' has 'Restrict saving content' on and you are not an admin, so tgmirror will "
        "not copy it (decision D3). Ask the owner to turn it off if they agree."
    ),
    "err.dest_not_writable": "You must be an admin allowed to post in '{title}'. Pick another one.",
    "err.kind_mismatch": "The source is a {src} but the destination is a {dst}; they must match.",
    "err.same_channel": "Source and destination are the same chat.",
    "err.title_empty": "The channel title must not be empty.",
    "err.title_too_long": "The channel title is at most 128 characters.",
    "err.about_too_long": "The description is at most 255 characters.",
    "err.new_unsupported": (
        "Creating a new destination for a {kind} source arrives in phase 8. "
        "Pick an existing destination of the same kind."
    ),
    "err.mode_unsupported": (
        "Mode '{mode}' is not available yet (reupload arrives in phase 6). Use auto or copy."
    ),
    "err.run_busy": (
        "Run {id} is held by another process. If you are sure it is dead, run again "
        "with --force-takeover."
    ),
    "err.run_waiting_flood": "Telegram asked to wait; the clone must not run again before {until}.",
    "err.run_waiting_daily_cap": (
        "The daily limit on messages sent (daily_cap) is used up; the clone must not run "
        "again before {until}."
    ),
    "err.daily_cap": (
        "{sent} messages sent today, the daily cap ({cap}) is reached. Progress is "
        "saved; run `tgmirror run` again after {until}."
    ),
    "err.run_waiting_peer_flood": (
        "Telegram flagged the account as spam (PEER_FLOOD) on the previous run. Rest "
        "until {until}, then run again."
    ),
    "err.forwards_restricted": (
        "The source has 'Restrict saving content' on, so Telegram refuses to forward. If you are "
        "an admin, turn that option off at the source; reupload mode arrives in phase 6."
    ),
    "err.store": "Database error: {detail}",
    "err.schema_too_new": "The database was made by a newer tgmirror. Please upgrade tgmirror.",
    "err.filter": "Invalid filter: {detail}",
    "err.filter_mix": (
        "Do not combine --filter-file with the other filter flags (--media, --hashtag, ...)."
    ),
    "filter.pick": "Filter what gets copied?",
    "filter.keep": "Keep the filter of the previous run",
    "filter.none": "No filter: copy everything",
    "filter.criteria": "Pick criteria",
    "filter.file": "Load from a YAML file",
    "filter.ask_media": "Media types (space to select; none selected = all)",
    "filter.ask_hashtags": "Hashtags, comma-separated (empty = no filter)",
    "filter.ask_contains": "Keywords in the text, comma-separated (empty = no filter)",
    "filter.ask_since": "From date YYYY-MM-DD (empty = from the start)",
    "filter.ask_until": "Until date YYYY-MM-DD, that day not included (empty = to the end)",
    "filter.ask_min_size": "Minimum size, e.g. 10MB (empty = no limit)",
    "filter.ask_max_size": "Maximum size, e.g. 2GB (empty = no limit)",
    "filter.ask_file": "Path of the YAML file",
    "clone.preview": (
        "Preview: {matched} of the first {scanned} messages in the chosen range would be copied."
    ),
    "clone.preview_empty": "Preview: the source has no messages in the chosen range.",
    "clone.preview_example": "  · {text}",
    "run.skipped": "{count} messages were left out by the filter.",
    "run.progress_filtered": (
        "Run {id}: {done} messages copied, {skipped} left out by the filter, {failed} "
        "failed (source up to id {cursor})."
    ),
    "clone.confirm_start": "Clone {src} → {dst} now?",
    "clone.dst_will_be_created": "'{title}' (a new channel will be created)",
    "run.filter_reused": (
        "Using the filter of the previous run; only messages newer than last time."
    ),
    "run.filter_changed": (
        "The filter changed: reading the source again from the start. Messages "
        "already copied are skipped; ones that now match but were not copied go to "
        "the end of the destination (its order is no longer chronological)."
    ),
    "run.keys_hint": "Keys: [p] pause  [r] resume  [q] stop. Ctrl+C also stops.",
    "run.paused": "Paused. Press r to resume, q to stop.",
    "run.resumed": "Resumed.",
    "run.continue_hint": "Continue with: tgmirror run {id}",
    "run.resumed_elsewhere": "Run {id} was paused in another terminal; it is running again there.",
    "control.nothing_running": "No clone is running.",
    "history.empty": "No runs yet. Start with `tgmirror clone`.",
    "history.title": "Recent runs",
    "history.col_run": "Run",
    "history.col_started": "Started",
    "history.col_pair": "Source → destination",
    "history.col_status": "Status",
    "history.col_copied": "Copied",
    "history.col_failed": "Failed",
    "history.col_filtered": "Filtered",
    "history.header": "Run {id}: {src} → {dst}",
    "history.line_status": "Status:      {status}{note}",
    "history.line_time": "Started:     {started}   Ended: {ended}",
    "history.line_counts": (
        "Result:      {done} copied, {failed} failed, {skipped} left out by the filter"
    ),
    "history.line_cursor": "Source:      from id {start} to id {end}",
    "history.line_filter": "Filter:      {filter}",
    "history.no_filter": "no filter",
    "history.failed_title": "Failed messages ({count}):",
    "history.failed_line": "  · source message {id}: {reason}",
    "history.failed_more": "  … and more; see --json",
    "history.floods_title": "Limits from Telegram:",
    "history.flood_line": "  · {ts} {kind} {seconds}s ({method})",
    "history.still_running": "running",
    "err.run_not_found": "No run '{ref}'. See `tgmirror history`.",
    "err.run_none": "Nothing has been cloned yet. Start with `tgmirror clone`.",
    "clone.pick_resume": "This pair already has {count} messages copied. What next?",
    "clone.resume_continue": "Continue: only what is new",
    "clone.resume_fresh": "Start from scratch: copy everything again",
    "clone.confirm_fresh": (
        "The destination already has {count} messages from earlier runs; starting "
        "fresh copies everything again and may duplicate them. Clone {src} → {dst} "
        "now?"
    ),
    "run.fresh_started": (
        "Fresh start: forgot {count} copied messages, reading the source from the start."
    ),
    "err.fresh_needs_yes": (
        "--fresh forgets {count} copied messages and copies them again (the "
        "destination may get duplicates); add --yes to agree when there is no "
        "terminal to ask."
    ),
}


def t(key: str, env: Mapping[str, str] | None = None, **params: object) -> str:
    """Look up ``key`` in the active language, falling back to English, then format it."""
    lang = (os.environ if env is None else env).get(ENV_LANG, "vi").lower()
    table = EN if lang == "en" else VI
    template = table.get(key) or EN[key]
    return template.format(**params) if params else template
