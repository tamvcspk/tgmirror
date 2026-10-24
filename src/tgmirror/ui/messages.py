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
    "login.api_saved_keyring": "Đã lưu api_id/api_hash vào keyring hệ thống ({backend}).",
    "login.api_saved_config": (
        "Đã lưu api_id/api_hash vào {path} (máy này không có keyring dùng được)."
    ),
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
    # topics (phase 8)
    "topics.empty": "Kênh này không có topic nào.",
    "topics.count": "{count} topic.",
    "col.closed": "Đã đóng",
    "err.not_a_forum": "«{title}» không phải forum nên không có topic.",
    # config
    "config.paths_title": "Đường dẫn:",
    "config.path_config": "config.toml",
    "config.path_db": "state (SQLite)",
    "config.path_sessions": "sessions",
    "config.path_line": "  {label}: {path}",
    "config.limits_title": "[limits]:",
    "config.saved": "Đã lưu {name} = {value}.",
    # appdata (phase 10)
    "appdata.export_done": "Đã xuất {files} tệp vào {path}.",
    "appdata.confirm_overwrite": (
        "Máy này đã có dữ liệu tgmirror; dời sang bản sao lưu rồi nhập đè lên?"
    ),
    "appdata.backed_up": "Dữ liệu cũ đã dời sang {path} (không bị xóa).",
    "appdata.import_done": "Đã nhập {files} tệp.",
    "appdata.login_reminder": "Chạy `tgmirror login` để đăng nhập lại.",
    # doctor
    "doctor.title": "tgmirror doctor",
    "doctor.session_missing_credentials": (
        "Session: chưa có api_id/api_hash (chạy `tgmirror login`)."
    ),
    "doctor.credential_source": "Credential: lấy từ {source}.",
    "doctor.credential_move_suggested": (
        "Credential: vẫn còn trong config.toml, nhưng máy này có keyring — "
        "chạy lại `tgmirror login` để chuyển sang keyring."
    ),
    "doctor.session_not_logged_in": "Session: chưa đăng nhập (chạy `tgmirror login`).",
    "doctor.session_error": "Session: lỗi kết nối — {detail}",
    "doctor.session_ok": "Session: hợp lệ, đang đăng nhập {who}.",
    "doctor.cryptg_ok": "cryptg: đã cài (giải mã nhanh hơn).",
    "doctor.cryptg_missing": (
        "cryptg: chưa cài — chạy chậm hơn nhưng vẫn hoạt động (uv sync cài lại)."
    ),
    "doctor.sessions_perm_ok": "Quyền thư mục session: chỉ chủ sở hữu đọc/ghi được (0700).",
    "doctor.sessions_perm_warn": (
        "Quyền thư mục session: {mode}, rộng hơn 0700 — người khác trên máy có thể đọc được "
        "session (một khi có nó là đăng nhập được, không cần mật khẩu). Chạy tgmirror lại một "
        "lần để tự siết, hoặc tự chmod 700."
    ),
    "doctor.no_destinations": "Đích: chưa có cặp nguồn/đích nào (chạy `tgmirror clone` trước).",
    "doctor.destinations_need_session": "Đích: cần đăng nhập để kiểm tra quyền.",
    "doctor.destination_ok": "Đích {title}: vẫn đăng được.",
    "doctor.destination_bad": (
        "Đích {title}: KHÔNG còn đủ quyền đăng (không còn admin hoặc bị cấm)."
    ),
    "doctor.destination_error": "Đích {title}: không kiểm tra được — {detail}",
    "doctor.safety_account": (
        "An toàn: dùng tài khoản đã có lịch sử; tài khoản mới tinh dễ bị siết."
    ),
    "doctor.safety_sessions": (
        "An toàn: không chạy nhiều tool/nhiều session cùng lúc trên một account."
    ),
    "doctor.safety_risk": (
        "An toàn: tự động hóa user account có rủi ro bị giới hạn. "
        "tgmirror giảm rủi ro, không loại bỏ hoàn toàn."
    ),
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
    "run.slow_mode": (
        "Đích bật chế độ chậm {seconds}s: mỗi lần đăng vào đó cách nhau ít nhất {seconds}s."
    ),
    # backup (phase 11a)
    "backup.pick_dir": "Thư mục backup (tạo mới nếu chưa có)",
    "backup.pick_dir_empty": "Đường dẫn không được để trống.",
    "backup.filter_kept": "Thư mục đã có backup trước đó: giữ nguyên filter đã dùng.",
    "backup.preview": (
        "Xem trước: {matched} trong {scanned} tin đầu tiên của khoảng đã chọn sẽ được lưu."
    ),
    "backup.preview_empty": "Xem trước: nguồn không có tin nào trong khoảng đã chọn.",
    "backup.confirm_start": "Backup {src} vào {dir} ngay bây giờ?",
    "backup.start": "Lần backup {id}: {src} → {dir}.",
    "backup.analyzed": (
        "Ước tính: tối đa {total} tin cần xem xét (số đếm của Telegram, chưa trừ filter)."
    ),
    "backup.progress": (
        "Lần backup {id}: {done} tin đã lưu, {skipped} bị filter loại (tới id {cursor})."
    ),
    "backup.result": "Lần backup {id}: {status}. {done} tin đã lưu (tới id {cursor}).",
    "backup.continue_hint": "Backup tiếp sau: tgmirror backup ... {dir}",
    "backup.paused": "Đã tạm dừng. Bấm r để chạy tiếp, q để dừng.",
    "backup.resumed": "Chạy tiếp.",
    "backup.gone": "Tin {id} đã bị xóa ở nguồn từ lúc liệt kê, bỏ qua.",
    "backup.flood_stopped": (
        "Telegram yêu cầu chờ {seconds}s. Lần backup đã lưu ở trạng thái chờ đến "
        "{resume_at}; chạy lại `tgmirror backup` sau đó."
    ),
    "backup.flood_waiting": (
        "Telegram yêu cầu chờ {seconds}s; đang chờ (Ctrl+C để dừng, tiến độ đã lưu trên đĩa)."
    ),
    "backup.throttled": (
        "Telegram giới hạn liên tiếp: tạm nghỉ {delay}s giữa các lần đọc, "
        "cho tới khi yên ổn trở lại."
    ),
    # delete (phase 20, decision D10)
    "delete.no_admin_channels": ("Bạn không quản trị kênh/nhóm nào, nên không có gì để xoá tin."),
    "delete.pick_source": "Xoá tin khỏi kênh/nhóm nào?",
    "delete.needs_filter": (
        "Bộ lọc trống sẽ xoá TẤT CẢ tin của kênh. Nếu đúng ý bạn, thêm --all; nếu không, thêm "
        "bộ lọc (--media, --hashtag, --since, --selection, ...)."
    ),
    "delete.confirm_all": (
        "Không có bộ lọc: kế hoạch sẽ gồm TẤT CẢ tin của «{chat}». Vẫn lập kế hoạch?"
    ),
    "delete.nothing": "Bộ lọc không chọn tin nào trong «{chat}»: không có gì để xoá.",
    "delete.plan_header": (
        "Kế hoạch xoá O{id} khỏi «{chat}»: {messages} tin ({units} đơn vị, {albums} album)."
    ),
    "delete.plan_media": "  theo loại: {items}",
    "delete.plan_months": "  theo tháng: {items}",
    "delete.plan_years": "  theo năm: {items}",
    "delete.plan_sample": "  id {id} · {date} · {media} · {text}",
    "delete.plan_gap": "  …",
    "delete.type_count": (
        "Việc này xoá {count} tin khỏi «{chat}» và không hoàn tác được. Gõ {count} để xoá"
    ),
    "delete.needs_expect": (
        "--yes cần đi kèm --expect N, với N là số tin trong kế hoạch ({count}): để một bộ lọc "
        "khớp nhiều hơn bạn nghĩ không xoá nhầm."
    ),
    "delete.expect_mismatch": (
        "Kế hoạch có {count} tin, không phải {expected} như --expect. Chưa xoá gì."
    ),
    "delete.needs_yes": (
        "Chưa xoá gì. Kế hoạch O{id} ({count} tin) đã được lưu; để xoá, chạy: "
        "tgmirror delete --resume O{id} --yes --expect {count}"
    ),
    "delete.cancelled": "Đã bỏ kế hoạch O{id}; chưa xoá gì.",
    "delete.finished": "Thao tác O{id} đang ở trạng thái «{status}»; không còn gì để làm.",
    "delete.start": "Thao tác O{id}: xoá {count} tin khỏi «{chat}».",
    "delete.progress": (
        "O{id}: {done}/{planned} tin đã xoá, {skipped} để nguyên (đã đổi), {gone} đã mất sẵn."
    ),
    "delete.result": "Thao tác O{id}: {status}. {done}/{planned} tin đã xoá.",
    "delete.left_alone": (
        "{count} tin đã thay đổi từ lúc lập kế hoạch, không còn khớp bộ lọc nên được để nguyên."
    ),
    "delete.gone": "{count} tin đã không còn khi tới lượt (có người xoá trước).",
    "delete.continue_hint": "Xoá tiếp phần còn lại: tgmirror delete --resume O{id}",
    "delete.paused": "Đã tạm dừng. Bấm r để chạy tiếp, q để dừng.",
    "delete.resumed": "Chạy tiếp.",
    "delete.settled": (
        "Lần trước dừng giữa một lô: {deleted} tin đã xoá xong, {again} tin còn nguyên sẽ xoá tiếp."
    ),
    "delete.flood_stopped": (
        "Telegram yêu cầu chờ {seconds}s. Thao tác được lưu ở trạng thái chờ đến {resume_at}; "
        "chạy lại với `tgmirror delete --resume` sau đó."
    ),
    "delete.flood_waiting": (
        "Telegram yêu cầu chờ {seconds}s; đang chờ (Ctrl+C để dừng, tiến độ đã lưu)."
    ),
    "delete.throttled": (
        "Telegram giới hạn liên tiếp: tạm nghỉ {delay}s giữa các lần, cho tới khi yên ổn trở lại."
    ),
    "delete.budget_learned": (
        "Telegram bắt chờ {seconds}s: ngân sách của làn {action} giờ là {budget} tin/{window} giờ."
    ),
    "delete.cap_warning": "Đã thao tác {sent} tin trong 24 giờ qua (nhắc daily_cap: {cap}).",
    # the safety net before a delete (phase 20.2)
    "delete.confirm_backup": (
        "Backup {count} tin này trước khi xoá? Telegram không cho hoàn tác; từ bản backup có thể "
        "`tgmirror restore` lại, nhưng tin sẽ quay về ở cuối kênh."
    ),
    "delete.confirm_backup_dir": "Lưu backup vào {dir}?",
    "delete.backup_stopped": (
        "Backup chưa xong ({status}) nên chưa xoá gì. Kế hoạch O{id} vẫn được giữ; backup tiếp "
        "rồi xoá: tgmirror delete --resume O{id} --yes --expect {count}"
    ),
    "delete.backup_done": (
        "Backup xong: {dir}. Lấy lại bằng `tgmirror restore {dir}` (tin quay về ở cuối kênh)."
    ),
    "delete.backup_note": "Bản backup trước khi xoá: {dir}",
    # restore (phase 11b)
    "restore.pick_dir": "Thư mục backup cần restore",
    "restore.pick_dir_empty": "Đường dẫn không được để trống.",
    "restore.pick_destination": "Restore vào đâu?",
    "restore.preview": (
        "Xem trước: {matched} trong {scanned} tin đầu tiên của khoảng đã chọn sẽ được restore."
    ),
    "restore.preview_empty": "Xem trước: backup không có tin nào trong khoảng đã chọn.",
    "restore.confirm_start": "Restore {src} vào {dst} ngay bây giờ?",
    "restore.dst": "Đích: {channel}",
    "restore.dst_created": "Đích (mới tạo): {channel}",
    "status.running": "đang chạy",
    "status.paused": "tạm dừng",
    "status.stopped": "đã dừng",
    "status.waiting_flood": "chờ flood",
    "status.done": "xong",
    "status.planned": "đã lập kế hoạch",
    "status.cancelled": "đã huỷ",
    "status.failed": "lỗi",
    "control.pause_requested": "Đã yêu cầu tạm dừng lần chạy {id}; nó dừng sau batch hiện tại.",
    "control.stop_requested": "Đã yêu cầu dừng lần chạy {id}; nó dừng sau batch hiện tại.",
    # warnings
    "warn.noforwards_unadministered": (
        "Nguồn bật «Restrict saving content» và tài khoản này KHÔNG phải admin của nó; "
        "tgmirror không kiểm tra được việc bạn là chủ kênh bằng tài khoản khác."
    ),
    "warn.responsibility": (
        "CẢNH BÁO: bạn đang sao chép một nguồn cấm lưu nội dung dựa trên tuyên bố của chính "
        "bạn rằng bạn được phép. Bạn chịu hoàn toàn trách nhiệm về quyền sao chép nội dung "
        "này, kể cả với bản quyền và Điều khoản của Telegram."
    ),
    "warn.noforwards_admin": (
        "Nguồn bật «Restrict saving content». Bạn là admin nên có thể tắt tùy chọn này tạm thời, "
        "hoặc dùng --mode reupload (tải xuống rồi tải lên lại; có hỏi xác nhận)."
    ),
    "warn.topic_loss": (
        "Nguồn là forum nhưng đích không có topic: cấu trúc topic sẽ bị bỏ hoàn toàn."
    ),
    "warn.topic_as_hashtag": (
        "Nguồn là forum nhưng đích không có topic: tên topic được giữ dưới dạng hashtag, nên tin "
        "trong topic được gửi lại thay vì forward (--no-topic-as-hashtag để bỏ)."
    ),
    "warn.route_hashtags": (
        "Đích không phải forum nên không có topic: tên mà `topics.route` cho mỗi tin được giữ "
        "dưới dạng hashtag, và tin có tên được gửi lại thay vì forward."
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
    "err.server_failed": (
        "Telegram báo lỗi nội bộ cho yêu cầu này (đã thử lại nhiều lần). Thường chỉ là "
        "tạm thời: thử lại sau ít phút. Nếu lỗi lặp lại với đúng yêu cầu đó (vd. đếm một "
        "topic rất lớn), thử cách khác: `inspect --quick` không kèm --topic, hoặc quét bằng "
        "`inspect`."
    ),
    "err.generic": "{detail}",
    "err.aborted": "Đã hủy.",
    "err.no_tty": (
        "Lệnh này cần terminal tương tác (mã đăng nhập gửi qua Telegram). Chạy trong terminal."
    ),
    "err.missing_flag": "Thiếu {flag} (không có terminal để hỏi).",
    "err.conflicting_flags": "Không dùng cùng lúc {flags}.",
    "err.channel_id_as_option": (
        "«{given}» bị hiểu là một cờ vì bắt đầu bằng dấu «-». Gõ id không có dấu trừ "
        "({bare}) hoặc đặt «--» trước nó, sau các cờ khác: tgmirror ... -- {given}"
    ),
    "err.needs_yes": "Sẽ tạo kênh mới nên cần {flag} khi không có terminal để xác nhận.",
    "err.channel_not_found": (
        "Không thấy kênh/nhóm «{ref}» trong danh sách đã join. Xem `tgmirror channels`."
    ),
    "err.ambiguous": "«{ref}» khớp nhiều kênh: {matches}. Dùng id hoặc @username.",
    "err.source_restricted": (
        "«{title}» bật «Restrict saving content» và tài khoản này không phải admin, nên tgmirror "
        "không sao chép (quyết định D3). Nếu bạn là chủ kênh bằng tài khoản khác và tự chịu "
        "hoàn toàn trách nhiệm, chạy lại với --mode reupload --yes-i-administer-this-channel; "
        "hoặc nhờ chủ kênh tắt tùy chọn này."
    ),
    "err.dest_not_writable": "Bạn cần là admin có quyền đăng bài ở «{title}». Chọn đích khác.",
    "err.same_channel": "Nguồn và đích là cùng một kênh.",
    "err.title_empty": "Tên kênh không được để trống.",
    "err.title_too_long": "Tên kênh tối đa 128 ký tự.",
    "err.about_too_long": "Mô tả tối đa 255 ký tự.",
    "err.mode_unsupported": "Chế độ «{mode}» không có. Dùng auto, copy hoặc reupload.",
    "err.opt_caption_unknown": "--caption phải là keep, strip-links, append hoặc none.",
    "err.opt_caption_text_missing": '--caption append cần thêm --caption-text "<nội dung>".',
    "err.opt_caption_text_unused": "--caption-text chỉ dùng cùng --caption append.",
    "err.opt_caption_needs_reupload": (
        "Sửa caption phải gửi lại tin, forward không làm được: dùng --mode auto "
        "(chỉ tin có caption đi đường đó) hoặc --mode reupload, không dùng --mode copy."
    ),
    "err.opt_reupload_flags_need_reupload": (
        "--reset-polls, --ignore-unsupported và --placeholder chỉ có nghĩa với --mode reupload."
    ),
    "err.opt_transform_needs_reupload": (
        "Transform đổi caption nên phải gửi lại tin, forward không làm được: dùng --mode auto "
        "(chỉ tin cần sửa đi đường đó) hoặc --mode reupload, không dùng --mode copy."
    ),
    "err.opt_forward_header_needs_forward": (
        "`forward_header: keep` (--keep-forward-header) chỉ có trên tin được forward: không đi "
        "cùng --mode reupload (restore luôn tải lên lại), --caption khác keep hay "
        "--topic-as-hashtag."
    ),
    "err.transform": "Transform không hợp lệ: {detail}",
    "err.opt_route_needs_rewrite": (
        "Đích không có topic nên tên mà `topics.route` cho mỗi tin được giữ dưới dạng hashtag, "
        "và việc đó cần gửi lại tin (forward không thêm được hashtag): dùng --mode auto hoặc "
        "--mode reupload, không dùng --mode copy."
    ),
    "err.opt_topic_hashtag_needs_rewrite": (
        "--topic-as-hashtag cần gửi lại tin (forward không thêm được hashtag): dùng --mode auto "
        "hoặc --mode reupload, không dùng --mode copy."
    ),
    "err.unsupported_media": (
        "Tin {id} là {kind}, không sao chép được. Chạy lại với --ignore-unsupported để bỏ qua "
        "nó, hoặc --placeholder để đăng một dòng ghi chú thay vào chỗ đó. Tiến độ đã lưu."
    ),
    "err.needs_admin_ack_rerun": (
        "«{title}» bật «Restrict saving content» và lần chạy này chưa được bạn xác nhận. Chạy "
        "lại bằng `tgmirror clone` cùng nguồn/đích với --mode reupload "
        "--yes-i-administer-this-channel."
    ),
    "err.needs_admin_ack": (
        "«{title}» bật «Restrict saving content». Tải xuống rồi tải lên lại nội dung đó là quyết "
        "định của bạn: nếu bạn là chủ/admin và được phép sao chép, thêm "
        "--yes-i-administer-this-channel (--yes không thay được)."
    ),
    "err.run_busy": (
        "Lần chạy {id} đang được một tiến trình khác giữ. Nếu chắc chắn nó đã chết, "
        "chạy lại với --force-takeover."
    ),
    "err.run_waiting_flood": "Telegram đã yêu cầu chờ; chưa chạy lại được trước {until}.",
    "err.daily_cap": (
        "Đã gửi {sent} tin trong {window} giờ qua, chạm ngân sách {budget} tin/{window} giờ "
        "mà Telegram đã buộc ta học. Tiến độ đã lưu; chạy lại `tgmirror run` sau {until}."
    ),
    "err.run_waiting_peer_flood": (
        "Telegram đánh dấu tài khoản là spam (PEER_FLOOD) ở lần chạy trước. Nghỉ đến "
        "{until} rồi hãy chạy lại."
    ),
    "err.forwards_restricted": (
        "Nguồn bật «Restrict saving content» nên Telegram từ chối forward. Nếu bạn là admin, "
        "tắt tùy chọn này ở nguồn, hoặc chạy lại với --mode reupload."
    ),
    "err.store": "Lỗi cơ sở dữ liệu: {detail}",
    "err.schema_too_new": "Cơ sở dữ liệu do bản tgmirror mới hơn tạo ra. Hãy nâng cấp tgmirror.",
    "err.appdata_export_busy": (
        "Có lần chạy đang giữ dữ liệu (running/paused). Dừng nó (`tgmirror stop`) rồi xuất lại."
    ),
    "err.appdata_import_needs_yes": (
        "Máy này đã có dữ liệu tgmirror; chạy lại với --yes để dời sang bản sao lưu rồi nhập, "
        "hoặc chạy trong terminal để được hỏi."
    ),
    "err.appdata_schema_newer": (
        "Tệp xuất dùng schema {found}, bản tgmirror này chỉ biết tới {known}. Nâng cấp tgmirror "
        "rồi nhập lại."
    ),
    "err.appdata_checksum": (
        "Tệp {entry} trong bản xuất không khớp checksum đã ghi — tệp có thể đã hỏng."
    ),
    "err.appdata_format": "Không đọc được tệp xuất: {detail}",
    "err.backup_needs_admin_ack": (
        "«{title}» bật «Restrict saving content». Tải xuống rồi lưu nội dung đó là quyết định "
        "của bạn: nếu bạn là chủ/admin và được phép sao chép, thêm "
        "--yes-i-administer-this-channel (--yes không thay được)."
    ),
    "err.backup_filters_changed": (
        "{dir} đã được backup với filter khác. Backup không nhớ tin nào bị filter loại nên không "
        "tiếp tục an toàn với filter mới; dùng một thư mục khác."
    ),
    "err.backup_wrong_source": "{dir} đã chứa backup của «{title}», không phải nguồn bạn chọn.",
    "err.backup_busy": (
        "Lần backup {id} đang được một tiến trình khác giữ. Nếu chắc chắn nó đã chết, "
        "chạy lại với --force-takeover."
    ),
    "err.not_admin": ("Bạn không phải quản trị viên của «{title}», nên không xoá được tin ở đó."),
    "err.op_none": "Chưa có thao tác nào.",
    "err.op_not_found": "Không có thao tác {ref!r}.",
    "err.op_busy": (
        "Thao tác O{id} đang được một tiến trình khác giữ. Nếu chắc chắn nó đã chết, "
        "chạy lại với --force-takeover."
    ),
    "err.corrupt_messages_file": (
        "{path}: dòng {line} không phải JSON hợp lệ. Tệp có thể đã hỏng — sửa hoặc xoá dòng đó "
        "rồi thử lại; tgmirror sẽ không tự bỏ qua để tránh mất dữ liệu phía sau dòng hỏng."
    ),
    "err.disk_full": (
        "Hết chỗ đĩa: {detail}. Tiến độ đã lưu, không mất gì — giải phóng dung lượng rồi chạy lại."
    ),
    "err.os_error": "Lỗi hệ điều hành/tệp: {detail}",
    "err.crashed": (
        "Có lỗi ngoài dự kiến: {detail}. Chạy lại với `tgmirror --debug <lệnh> ...` "
        "(--debug đứng trước tên lệnh) để xem traceback đầy đủ."
    ),
    "err.backup_dir_missing": (
        "{dir} không còn đọc được (mất backup.json) — thư mục có thể đã bị xoá hoặc chuyển chỗ. "
        "Dùng --from-backup THƯ_MỤC_MỚI để trỏ lại cặp này, hoặc --fresh để chạy lại từ đầu."
    ),
    "err.wrong_backup_source": (
        "{dir} là backup của nguồn khác (id {found}), không phải nguồn của cặp này (id {expected})."
    ),
    "err.not_a_backup": "{dir} không phải một thư mục backup hợp lệ (không có backup.json).",
    "err.filter": "Filter không hợp lệ: {detail}",
    # filters (wizard step 3, preview, refilter)
    "filter.pick": "Lọc nội dung sao chép?",
    "filter.keep": "Giữ filter của lần chạy trước",
    "filter.use_last": "Dùng kết quả khám phá gần nhất của nguồn này ({count} tin, {when})",
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
    "filter.ask_topics": "Chỉ lấy các topic sau (phím cách để chọn; không chọn gì = tất cả)",
    "filter.ask_file": "Đường dẫn file YAML",
    "filter.file_has_transform": (
        "File {path} mang một transform; bước này chỉ nạp filter. Dùng "
        "`--selection {path}` để áp cả transform."
    ),
    "clone.preview": (
        "Xem trước: {matched} trong {scanned} tin đầu tiên của khoảng đã chọn sẽ được sao chép."
    ),
    "clone.preview_empty": "Xem trước: nguồn không có tin nào trong khoảng đã chọn.",
    "clone.preview_example": "  · {text}",
    "run.skipped": "{count} tin bị filter loại.",
    "run.skipped_unsupported": "Bỏ qua tin {id} ({reason}): không sao chép được.",
    "run.unsupported_total": (
        "{count} tin không sao chép được đã bị bỏ qua (xem `tgmirror history {id}`)."
    ),
    "run.links_left": (
        "{count} link trỏ tới tin chưa có bản sao ở đích (tin sau, hoặc bị filter loại): giữ "
        "nguyên, vẫn trỏ về nguồn."
    ),
    "run.reply_lost": (
        "{count} tin trả lời một tin không có bản sao ở đích (ngoài phạm vi sao chép, bị lỗi, "
        "hoặc ở topic khác): đã gửi, nhưng không kèm reply."
    ),
    "clone.confirm_protected": (
        "«{title}» bật «Restrict saving content»: chủ kênh đã cấm lưu nội dung của nó. Chỉ tiếp "
        "tục nếu bạn là chủ/admin và được phép sao chép. Tải xuống rồi tải lên lại từng tin?"
    ),
    "clone.confirm_unadministered": (
        "«{title}» bật «Restrict saving content» và tài khoản này không phải admin của nó. Nếu "
        "bạn là chủ kênh này qua một tài khoản khác và tự chịu hoàn toàn trách nhiệm về việc sao "
        "chép nội dung này (kể cả bản quyền và Điều khoản Telegram — tgmirror không kiểm tra được "
        "điều này), gõ nguyên văn {flag} rồi Enter để xác nhận. Gõ gì khác hoặc Esc: không sao "
        "chép nguồn này."
    ),
    "options.protected": (
        "Nguồn cấm lưu nội dung nên chỉ còn cách tải xuống rồi tải lên lại (reupload)."
    ),
    "options.customise_auto": "Đổi caption của tin media? (mặc định không)",
    "options.customise_reupload": (
        "Tùy chỉnh caption và cách xử lý tin không sao chép được? (mặc định không)"
    ),
    "options.pick_mode": "Cách sao chép?",
    "options.mode_auto": (
        "Tự động: forward phía server (nhanh, không tốn băng thông); tin cần đổi caption được "
        "gửi lại bằng mã file, không tải gì (nguồn cấm lưu thì tải xuống rồi tải lên)"
    ),
    "options.mode_copy": "Chỉ forward phía server (copy): không đổi được caption",
    "options.mode_reupload": (
        "Tải xuống rồi tải lên lại (chậm; cần để đổi caption hay khi nguồn cấm lưu)"
    ),
    "options.pick_caption": "Caption của tin media?",
    "options.caption_keep": "Giữ nguyên",
    "options.caption_strip-links": (
        "Bỏ link và mention trỏ về kênh nguồn (tin có caption được gửi lại)"
    ),
    "options.caption_append": "Thêm một đoạn chữ vào cuối (tin có caption được gửi lại)",
    "options.caption_none": "Bỏ caption (tin có caption được gửi lại)",
    "options.ask_caption_text": "Đoạn chữ thêm vào cuối caption:",
    "options.pick_flags": "Với tin không forward được (chọn cái cần):",
    "options.flag_reset_polls": "Tạo lại poll/quiz (mất toàn bộ số vote)",
    "options.flag_ignore_unsupported": "Bỏ qua game, hóa đơn, quiz chưa trả lời thay vì dừng",
    "options.flag_placeholder": "Đăng một dòng ghi chú thay cho tin bị bỏ qua",
    "run.progress_filtered": (
        "Lần chạy {id}: {done} tin đã sao chép, {skipped} bị filter loại, {failed} "
        "lỗi (tin nguồn tới id {cursor})."
    ),
    "clone.confirm_start": "Sao chép {src} → {dst} ngay bây giờ?",
    "clone.dst_will_be_created": "«{title}» (kênh mới sẽ được tạo)",
    "clone.route_by_hashtag": (
        "Chia tin theo hashtag: mỗi tin vào topic mang tên hashtag của nó (đích không có topic thì "
        "thành hashtag trong tin)? Luật phức tạp hơn thì dùng --transform"
    ),
    "clone.route_skip": (
        "Hashtag KHÔNG đặt tên topic, cách nhau bằng dấu phẩy (bỏ trống nếu không có)"
    ),
    "clone.route_pick": "Tin có nhiều hashtag: lấy hashtag nào đặt tên topic?",
    "clone.route_pick_first": "Hashtag đầu tiên",
    "clone.route_pick_last": "Hashtag cuối cùng",
    "clone.topic_as_hashtag": (
        "Giữ tên topic dưới dạng hashtag trong mỗi tin? (forward không thêm được hashtag, nên "
        "các tin trong topic sẽ được gửi lại thay vì forward)"
    ),
    "clone.confirm_topic_loss": (
        "Đích không có topic và không có cách nào giữ lại (--mode copy không thêm được "
        "hashtag): toàn bộ topic sẽ bị bỏ. Tiếp tục?"
    ),
    "err.topic_loss_needs_yes": (
        "Đích không có topic; toàn bộ topic sẽ bị bỏ vì --mode copy không thêm được hashtag. "
        "Thêm --yes để đồng ý (hoặc --no-topic-as-hashtag) khi không có terminal."
    ),
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
    "run.pick_pair": "Chạy tiếp cặp nào? (gõ để lọc)",
    "run.pick_pair_line": "#{id}  {src} → {dst}   {mode}   {status}",
    "resume.backup_dir_missing_title": "Cặp {id}: thư mục backup không đọc được",
    "resume.pick_new_dir": "Chọn thư mục backup khác",
    "resume.start_fresh": "Chạy lại từ đầu (quên tiến độ)",
    "resume.cancel": "Huỷ",
    # foreground TUI (Rich Live, ui/tui.py)
    "tui.header": 'tgmirror ▸ lần chạy {id}  "{src} → {dst}"   mode={mode}   delay={delay}s',
    "tui.progress_total": "{handled}/{total} tin (~{percent}%)",
    "tui.progress_plain": "{handled} tin đã xử lý",
    "tui.speed": "{speed} tin/s, ETA {eta}",
    "tui.speed_no_eta": "{speed} tin/s",
    "tui.counts": "{done} đã sao chép · {failed} lỗi · {skipped} bị lọc",
    "tui.floods": " · {count} lần bị giới hạn (gần nhất {ago} trước)",
    "tui.keys": "[p] tạm dừng   [r] chạy tiếp   [q] dừng",
    "control.nothing_running": "Không có clone nào đang chạy.",
    "history.empty": "Chưa có lần chạy nào. Bắt đầu bằng `tgmirror clone`.",
    "history.title": "Các lần chạy gần đây",
    "history.col_run": "Mã",
    "history.kind_run": "Chạy",
    "history.op_header": "Thao tác O{id}: {kind} · «{chat}»",
    "history.op_kind_delete": "xoá tin",
    "history.line_backup": "Backup:      {dir}{note}",
    "history.line_backup_unfinished": " (chưa xong)",
    "history.line_counts_op": (
        "Kết quả:     {done} đã xoá, {skipped} để nguyên, {gone} đã mất sẵn, {failed} lỗi "
        "(kế hoạch {planned} tin)"
    ),
    "history.kind_backup": "Backup",
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
    "history.line_counts_backup": "Kết quả:     {done} đã lưu, {skipped} bị filter loại",
    "history.line_cursor": "Tin nguồn:   từ id {start} tới id {end}",
    "history.line_filter": "Filter:      {filter}",
    "history.no_filter": "không lọc",
    "history.failed_title": "Tin lỗi ({count}):",
    "history.failed_line": "  · tin nguồn {id}: {reason}",
    "history.failed_more": "  … và còn nữa; xem bằng --json",
    "history.floods_title": "Giới hạn từ Telegram:",
    "history.flood_line": "  · {ts} {kind} {seconds}s ({method}){where}",
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
    # retry
    "retry.nothing": "Lần chạy {id} không có tin lỗi nào để thử lại.",
    "run.retry_start": (
        "Lần chạy {id}: thử lại {count} tin lỗi của lần chạy {of} ({src} → {dst})."
    ),
    "run.progress_retry": "Lần chạy {id}: {done} tin đã sao chép lại, {failed} vẫn lỗi.",
    "run.retry_hint": "Thử lại các tin lỗi: tgmirror retry {id}",
    "run.analyzed": (
        "Ước tính: tối đa {total} tin cần xem xét (số đếm của Telegram, chưa trừ filter)."
    ),
    "run.cap_rest": (
        "Ngân sách học được {budget} tin/{window} giờ: với {total} tin, cần nghỉ thêm khoảng "
        "{rest} trước khi xong (xem docs/05-chong-flood.md)."
    ),
    "rest.hours": "{n} giờ",
    "rest.days": "{n} ngày",
    "run.cap_warning": (
        "Đã gửi {sent} tin trong 24 giờ qua, vượt ngưỡng nhắc daily_cap ({cap}). Chỉ là nhắc: "
        "chỉ ngân sách học được từ flood dài của Telegram mới dừng lần chạy."
    ),
    "run.budget_learned": (
        "Telegram bắt chờ {seconds}s: ngân sách của làn {action} giờ là {budget} tin/{window} giờ "
        "(tăng dần 10% mỗi 24 giờ không bị chặn, không quá 95% của {bad} tin đã bị chặn)."
    ),
    "run.limits_reset": (
        "--reset-limits: đã quên giới hạn học được của {count} làn ghi vào đích này; không gì chặn "
        "chúng cho tới flood dài kế tiếp."
    ),
    "run.progress_total": (
        "Lần chạy {id}: {handled}/{total} tin (~{percent}%): {done} đã sao chép, "
        "{skipped} bị filter loại, {failed} lỗi."
    ),
    "run.reference_fallback": (
        "Tin {id}: Telegram không cho gửi lại bằng mã file, tải xuống rồi tải lên lại."
    ),
    "run.topics_limit": (
        "Đã tạo {limit} topic mới theo hashtag (giới hạn của `auto: all`): tin có hashtag mới "
        "hơn vào General. Tăng `limit` trong transform, hoặc liệt kê hashtag cần có."
    ),
    "run.transfer_download": "Tải xuống tin {id}: {percent}% ({done} / {total}{speed}).",
    "run.transfer_upload": "Tải lên tin {id}: {percent}% ({done} / {total}{speed}).",
    "run.retry_continue_hint": "Chạy tiếp việc thử lại: tgmirror retry {of}",
    "retry.gone": "{count} tin đã bị xóa ở nguồn nên không thể sao chép; bỏ qua.",
    "retry.still_failing": (
        "{count} tin vẫn lỗi. Xem `tgmirror history {id}`, hoặc thử lại: tgmirror retry {id}"
    ),
    "history.line_retry": "Thử lại:     tin lỗi của lần chạy {of}",
    "history.line_gone": "Đã xóa ở nguồn: {count} tin không thể sao chép (bỏ qua)",
    # status
    "status.none_live": "Không có lần chạy nào đang chạy; đây là lần gần nhất.",
    "status.abandoned": (
        "Không có tiến trình nào giữ lần chạy này (tín hiệu cuối lúc {at}). Lần chạy kế "
        "tiếp của cặp này sẽ ghi nó là bị ngắt và làm tiếp."
    ),
    "status.line_progress": "Tiến độ:     ~{percent}% (tin nguồn tới id {cursor} / {head})",
    "status.line_progress_retry": (
        "Tiến độ:     {percent}% ({handled} / {total} tin lỗi đã thử lại)"
    ),
    "status.line_progress_unknown": "Tiến độ:     tin nguồn tới id {cursor} (chưa biết tổng)",
    "status.line_progress_items": ("Tiến độ:     ~{percent}% ({handled} / tối đa {total} tin)"),
    "status.line_cap": (
        "Ngân sách:   còn tối đa {left} tin, ngân sách {cap} tin/{window} giờ: cần nghỉ thêm "
        "khoảng {rest}"
    ),
    "status.line_budget": (
        "Ngân sách:   {action}: {budget} tin/{window} giờ (đã gửi {sent}); đặt sau flood "
        "{seconds}s lúc {at}"
    ),
    "status.line_budget_none": (
        "Ngân sách:   chưa học (chưa gặp flood dài nào; chỉ pacing và FloodWait giới hạn)"
    ),
    "status.line_bounds": (
        "Giới hạn:    {action}: bị chặn ở {bad} tin, chạy sạch tới {ok}; kiểm chứng lần cuối {at}"
    ),
    "limits.none": "Chưa có làn ghi nào được ghi nhận (chưa có lần chạy nào gửi tin).",
    "limits.dest": "Đích {dst} ({title})",
    "limits.dest_unknown": "Đích {dst}",
    "limits.lane_learned": (
        "  {action}: {budget} tin/{window} giờ (đã gửi {sent}); bị chặn ở {bad}, chạy sạch tới "
        "{ok}; flood {seconds}s lúc {flood_at}; kiểm chứng lần cuối {checked}; nhịp {delay}s"
    ),
    "limits.lane_unlearned": (
        "  {action}: chưa học giới hạn (đã gửi {sent} trong 24 giờ; chỉ nhịp {delay}s và "
        "FloodWait giới hạn)"
    ),
    "limits.reset_done": (
        "Đã quên giới hạn của {count} làn ghi {where}. Nhịp đã học được giữ nguyên."
    ),
    "limits.reset_none": "Không có làn ghi nào đã học giới hạn {where}.",
    "limits.where_dst": "vào {dst}",
    "limits.where_all": "(mọi đích)",
    "limits.unknown": "-",
    "err.limits_reset_target": (
        "Cho biết đích cần quên giới hạn: --dst ĐÍCH (id hoặc tên trong `tgmirror limits`), "
        "hoặc --all cho mọi đích."
    ),
    "err.limits_reset_both": "--dst và --all không đi cùng nhau.",
    "err.limits_dst_unknown": (
        "Không thấy đích '{ref}' trong các làn đã học. Dùng id hoặc tên trong `tgmirror limits`."
    ),
    "err.limits_dst_ambiguous": "'{ref}' khớp nhiều đích: {matches}. Dùng id.",
    "err.limits_busy": (
        "Lần chạy hoặc backup {id} đang chạy ở nơi khác: nó giữ giới hạn trong bộ nhớ và sẽ ghi "
        "đè lên đây. Dừng nó (`tgmirror stop`) trước."
    ),
    "status.line_speed": "Tốc độ:      {speed} tin/giây (trung bình từ lúc bắt đầu){eta}",
    "status.line_speed_unknown": "Tốc độ:      chưa đủ dữ liệu",
    "status.eta": ", còn khoảng {eta}",
    "status.line_resume": "Chờ đến:     {at}{note}",
    "status.line_limiter": (
        "Giới hạn:    nghỉ {delay}s giữa các lần gửi; đã gửi {sent} tin trong 24 giờ qua "
        "(ngưỡng nhắc {cap})"
    ),
    "status.line_floods": "Telegram:    {count} lần bị giới hạn trong 24 giờ qua{last}",
    "status.line_floods_none": "Telegram:    chưa bị giới hạn lần nào trong 24 giờ qua",
    "status.last_flood": "; gần nhất {ago} trước ({kind}, {seconds}s)",
    "status.retry_hint": "Có {count} tin lỗi. Thử lại: tgmirror retry {id}",
    "duration.hours": "{hours} giờ {minutes} phút",
    "duration.minutes": "{minutes} phút",
    "duration.seconds": "{seconds} giây",
    # full-screen menu (ui/menu/, gõ trơn `tgmirror` có terminal thật)
    "menu.not_logged_in": "chưa đăng nhập",
    "menu.no_terminal": "Không đọc được bàn phím ở terminal này.",
    "menu.footer_main": "↑↓ chọn · Enter chọn · Ctrl+C thoát",
    "menu.footer_pick": "↑↓ chọn · Enter chọn · Esc quay lại",
    "menu.footer_back": "Nhấn phím bất kỳ để quay lại",
    "menu.footer_info": "Nhấn phím bất kỳ để tiếp tục",
    "menu.footer_filter": "Gõ để lọc · Esc quay lại",
    "menu.item_clone": "Sao chép mới…",
    "menu.item_explore": "Khám phá một kênh (đếm, lọc, rồi sao chép)…",
    "explore.title": "Khám phá ▸ {src}",
    "explore.footer": (
        "←→ cột · ↑↓ dòng · Enter lọc · x loại trừ · ⌫ bỏ bước · Tab đổi cột · l danh sách · "
        "s lưu @last · t transform · c clone · b backup · d xoá · u cập nhật · R đọc lại · "
        "Esc thoát"
    ),
    "explore.footer_building": "q hoặc Esc: dừng đọc và dùng phần đã có",
    "explore.index_line": "{messages} tin trong chỉ mục · cập nhật lúc {when}",
    "explore.partial": "Chỉ mục chưa tới cuối nguồn: bấm u để đọc tiếp.",
    "explore.flood": "Telegram bắt chờ {seconds} giây: dùng chỉ mục như đang có (bấm u sau).",
    "explore.empty": "Nguồn không có tin nào trong chỉ mục.",
    "explore.filter": "Lọc: {steps}",
    "explore.no_filter": "(chưa lọc)",
    "explore.not_filterable": "Dòng này không thành bộ lọc được.",
    "explore.route_not_filterable": (
        "Topic đích là kết quả của luật, không phải thuộc tính của tin: lọc theo "
        "hashtag, loại media, ..."
    ),
    "explore.transform_title": "Nạp transform",
    "explore.transform_prompt": ("Tệp transform có `topics.route` (để trống: bỏ cột topic đích)"),
    "explore.transform_none": (
        "Tệp {path} không có luật định tuyến nào (`topics.route` / `hashtag_to_topic`)."
    ),
    "explore.transform_loaded": (
        "Đã nạp luật định tuyến: cột «Theo topic đích» (Tab để chuyển tới)."
    ),
    "explore.transform_dropped": "Đã bỏ luật định tuyến.",
    "explore.hidden_forward": "Nguồn forward ẩn tên: không lọc theo nó được.",
    "explore.channel_post": "Bài đăng của kênh không có người gửi để lọc.",
    "explore.exclude_month": "Không loại trừ theo tháng được: chọn tháng để lọc vào.",
    "explore.saved": "Đã lưu bộ lọc ({count} tin) thành @last: dùng `--selection @last`.",
    "explore.list_title": "Tin khớp (hiện {shown} trên {total})",
    "menu.item_backup": "Backup…",
    "menu.item_delete": "Xoá tin trong kênh của mình…",
    "menu.item_restore": "Restore…",
    "menu.item_resume": "Chạy tiếp",
    "menu.item_retry": "Thử lại tin lỗi",
    "menu.item_status": "Trạng thái",
    "menu.item_history": "Lịch sử",
    "menu.item_channels": "Kênh đã join",
    "menu.item_account": "Tài khoản",
    "menu.item_config": "Cấu hình",
    "menu.item_quit": "Thoát",
    "menu.history_counts": "{done} tin, {failed} lỗi",
    "menu.history_counts_backup": "{done} tin",
    "menu.yes": "Có",
    "menu.no": "Không",
    "menu.confirm_logout": "Đăng xuất {who}?",
    "menu.item_login": "Đăng nhập",
    "menu.footer_text": "Gõ rồi Enter · Esc quay lại",
    "menu.footer_path": "Gõ · Tab gợi ý đường dẫn · Enter · Esc quay lại",
    "menu.footer_select": "↑↓ chọn · gõ để lọc · Enter chọn · Esc quay lại",
    "menu.footer_check": "↑↓ di chuyển · Space chọn/bỏ · Enter xong · Esc quay lại",
    "menu.footer_working": "Đang làm… · Ctrl+C thoát",
    "menu.working": "Đang làm…",
    "menu.no_match": "(không có mục nào khớp)",
    "menu.config_edit_title": "Sửa {name}",
    "menu.config_prompt_value": "Giá trị mới cho {name}",
    "menu.running_elsewhere": "đang chạy ở nơi khác",
    # inspect (phase 18a)
    "inspect.source": "Nguồn: {src}",
    "inspect.totals": (
        "Đã đọc {messages} tin ({units} đơn vị, {albums} album), {size}, từ {first} đến {last}."
    ),
    "inspect.selected": ("Khớp filter: {messages} tin ({units} đơn vị, {albums} album), {size}."),
    "inspect.empty": "Nguồn không có tin nào.",
    "inspect.partial": (
        "Đã dừng sớm: báo cáo chỉ gồm các tin tới id {last_id}, không phải cả nguồn."
    ),
    "inspect.progress": "Đang đọc: {messages} tin (tới id {last_id})...",
    "inspect.quick_title": "Số đếm của chính Telegram ({scope}), một request:",
    "inspect.quick_scope_all": "cả nguồn",
    "inspect.quick_scope_topic": "topic {topic}",
    "inspect.quick_note": (
        "Telegram tự phân loại nên có thể lệch vài % so với quét đầy đủ (tin bị ẩn vì vi phạm "
        'điều khoản vẫn được đếm là video/ảnh). Không đếm được theo khoảng id hay ngày; "—" là '
        "Telegram không trả loại đó. Chạy `tgmirror inspect` để quét và lọc."
    ),
    "inspect.quick_alone": (
        "--quick chỉ đếm cả nguồn hoặc một topic (--topic ID): không đi cùng bộ lọc khác, "
        "--by/--list/--csv/--save/--plan hay --offline/--refresh/--no-index."
    ),
    "inspect.quick_backup": "--quick hỏi Telegram, nên không dùng với một thư mục backup.",
    "inspect.quick_topic_not_forum": (
        "--topic chỉ dùng với nguồn là forum, mà {src} không phải forum."
    ),
    "inspect.quick_topic_unknown": (
        "Forum {src} không có topic {topic}: `tgmirror topics <nguồn>` liệt kê các topic."
    ),
    "inspect.kind.photo": "Ảnh",
    "inspect.kind.video": "Video",
    "inspect.kind.document": "Tài liệu",
    "inspect.kind.audio": "Nhạc",
    "inspect.kind.voice": "Voice",
    "inspect.kind.video_note": "Video tròn",
    "inspect.kind.gif": "GIF",
    "inspect.kind.link": "Có link",
    "inspect.kind.geo": "Vị trí",
    "inspect.kind.contact": "Danh bạ",
    "inspect.kind.poll": "Poll",
    "inspect.kind.pinned": "Tin ghim",
    "inspect.indexing": "Đang cập nhật chỉ mục: đã đọc {messages} tin (tới id {last_id})...",
    "inspect.index_state": (
        "Chỉ mục cục bộ: {messages} tin, cập nhật lúc {when} (số lượt xem/reaction và tin đã "
        "sửa hay xoá chỉ được làm mới khi chạy với --refresh)."
    ),
    "inspect.index_offline": (
        "Chỉ mục cục bộ (không gọi Telegram): {messages} tin, cập nhật lúc {when}."
    ),
    "inspect.index_partial": "Lần cập nhật cuối chưa tới cuối nguồn; chạy lại để đọc tiếp.",
    "inspect.index_not_for_backup": (
        "--refresh và --no-index chỉ dành cho một kênh: thư mục backup vốn đã đọc ngoại tuyến."
    ),
    "inspect.offline_no_index": (
        "Chưa có chỉ mục cho {ref!r}: chạy `tgmirror inspect` một lần (không có --offline) để "
        "dựng nó. --offline nhận id, @username hay tên đúng của nguồn đã được xem."
    ),
    "inspect.stopping": (
        "Đang dừng, sẽ in báo cáo của phần đã đọc (Ctrl+C lần nữa để thoát ngay)."
    ),
    "inspect.flood_waiting": (
        "Telegram yêu cầu chờ {seconds}s; đang chờ rồi đọc tiếp (Ctrl+C để dừng)."
    ),
    "inspect.throttled": (
        "Telegram giới hạn liên tiếp: tạm thời đọc chậm lại ({delay}s giữa các lần)."
    ),
    "inspect.by.media": "Theo loại media",
    "inspect.by.month": "Theo tháng",
    "inspect.by.year": "Theo năm",
    "inspect.by.hashtag": "Theo hashtag",
    "inspect.by.topic": "Theo topic",
    "inspect.by.sender": "Theo người gửi",
    "inspect.by.forward": "Theo nguồn forward",
    "inspect.by.domain": "Theo domain của link",
    "inspect.by.size": "Theo cỡ file",
    "inspect.by.duplicate": "File đăng nhiều lần",
    "inspect.by.route": "Theo topic đích",
    "inspect.route_needs_routing": (
        "--by route cần luật định tuyến: đưa --transform có `topics.route` (hoặc "
        "--hashtag-to-topic)."
    ),
    "inspect.none_route": "(không luật nào khớp)",
    "inspect.col.value": "Giá trị",
    "inspect.col.messages": "Tin",
    "inspect.col.size": "Dung lượng",
    "inspect.none_forward": "(không phải forward)",
    "inspect.none_sender": "(bài đăng của kênh)",
    "inspect.other": "(khác: {count} mục)",
    "inspect.no_rows": "(không có)",
    "inspect.not_a_forum": "(nguồn này không phải forum nên không có topic)",
    "inspect.list_title": "Tin khớp",
    "inspect.list_more": ("... còn {count} tin nữa (--limit để xem thêm, --csv FILE để xuất hết)."),
    "inspect.csv_written": "Đã ghi {count} tin vào {path}.",
    "inspect.by_unknown": "--by {value!r} không có; chọn trong: {choices}.",
    "inspect.bad_number": "{flag} phải là một số không âm.",
    # selections (phase 18a)
    "selection.exact_needs_selection": (
        "--exact cần --selection FILE (hoặc @last): đó là nơi có danh sách đã đóng băng."
    ),
    "selection.last_needs_src": (
        "--selection @last cần biết nguồn nào: thêm nguồn (--src, hoặc thư mục backup) hoặc dùng "
        "một file selection."
    ),
    "selection.no_last": (
        "Chưa có kết quả khám phá nào của «{title}»: chạy `tgmirror inspect` trên nguồn đó trước "
        "(hoặc dùng --selection FILE)."
    ),
    "selection.source_mismatch": (
        "File selection này là của nguồn {theirs}, không phải nguồn của lệnh này: id tin nhắn "
        "chỉ có nghĩa trong đúng nguồn của nó."
    ),
    "selection.no_transform": (
        "File selection {path} mang một transform, nhưng lệnh này không áp transform "
        "(chỉ clone và restore có): bỏ phần transform khỏi file hoặc dùng lệnh khác."
    ),
    "inspect.saved": "Đã lưu selection vào {path}.",
    "inspect.saved_ids": "Đã lưu selection vào {path} cùng danh sách {count} id ({ids}).",
    "inspect.freeze_needs_save": "--freeze cần --save FILE (nơi lưu danh sách đóng băng).",
    "inspect.next_title": "Dùng tiếp với đúng filter này:",
    "inspect.next_selection": "(filter này không viết được bằng cờ; dùng file selection)",
    "inspect.plan_title": "Kế hoạch {what}:",
    "inspect.plan_kind.clone": "clone (mode {mode}, caption {caption})",
    "inspect.plan_kind.backup": "backup",
    "inspect.plan_kind.restore": "restore",
    "inspect.col.way": "Cách",
    "inspect.col.units": "Đơn vị",
    "inspect.way.copy": "forward (Telegram sao chép phía server)",
    "inspect.way.reference": "gửi lại bằng mã file (không tải)",
    "inspect.way.reupload": "tải xuống rồi tải lên lại",
    "inspect.way.download": "tải xuống đĩa",
    "inspect.way.left_out": "bỏ qua",
    "inspect.plan_writes": (
        "{calls} lời gọi ghi lên Telegram (batch {batch}); limiter đợi tối thiểu {time} giữa chúng."
    ),
    "inspect.plan_no_writes": "Không ghi gì lên Telegram.",
    "inspect.plan_transfer": (
        "Phải truyền {size} (file lớn nhất {largest}); thời gian truyền phụ thuộc đường mạng nên "
        "chưa tính."
    ),
    "inspect.plan_blocked": (
        "Sẽ dừng ngay ở: {what}. Thêm --ignore-unsupported hoặc --placeholder (với --mode "
        "reupload) để bỏ qua chúng."
    ),
    "inspect.plan_left_out": "Sẽ bỏ qua: {what}.",
    "inspect.plan_topics": "Vào topic (theo luật `topics.route`): {what}.",
    "inspect.plan_topics_rest": "không luật nào khớp (theo topic nguồn, hoặc General)",
    "inspect.plan_topics_hashtags": (
        "Thành hashtag trong tin (theo luật `topics.route`; đích không có topic): {what}."
    ),
    "inspect.plan_topics_rest_hashtags": "không luật nào khớp (chuyển tiếp nguyên, không hashtag)",
    "inspect.plan_dst_no_topics_kind": (
        "--dst-no-topics chỉ dùng với --plan clone hoặc --plan restore."
    ),
    "inspect.plan_unmatched": (
        "{count} tin không khớp luật nào (sửa luật nếu đó không phải điều bạn muốn); vài tin đầu:"
    ),
    "inspect.plan_protected": (
        "Nguồn bật «Restrict saving content»: phần tải xuống cần lời tuyên bố "
        "--yes-i-administer-this-channel (quyết định D3)."
    ),
    "inspect.plan_unknown": "--plan {value!r} không có; chọn clone, backup hoặc restore.",
    "inspect.plan_flags_need_plan": (
        "--mode, --caption, --caption-text, --reset-polls, --ignore-unsupported, --placeholder "
        "và --topic-as-hashtag chỉ dùng cùng --plan."
    ),
    "inspect.plan_restore_needs_backup": (
        "--plan restore cần nguồn là một thư mục backup; với kênh dùng --plan clone hoặc backup."
    ),
    "inspect.plan_needs_channel": (
        "--plan {kind} cần nguồn là kênh; với thư mục backup dùng --plan restore."
    ),
    "inspect.plan_backup_options": (
        "--plan backup tải hết về đĩa nên không có --mode/--caption/...; bỏ các cờ đó."
    ),
}

EN: dict[str, str] = {
    "login.api_intro": (
        "Your api_id and api_hash are needed. Create them at https://my.telegram.org/apps."
    ),
    "login.prompt_api_id": "api_id",
    "login.prompt_api_hash": "api_hash (hidden while typing)",
    "login.api_id_invalid": "api_id must be a positive integer.",
    "login.api_saved_keyring": "Saved api_id/api_hash to the system keyring ({backend}).",
    "login.api_saved_config": (
        "Saved api_id/api_hash to {path} (no usable keyring on this machine)."
    ),
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
    "topics.empty": "This channel has no topics.",
    "topics.count": "{count} topics.",
    "col.closed": "Closed",
    "err.not_a_forum": "'{title}' is not a forum, so it has no topics.",
    "config.paths_title": "Paths:",
    "config.path_config": "config.toml",
    "config.path_db": "state (SQLite)",
    "config.path_sessions": "sessions",
    "config.path_line": "  {label}: {path}",
    "config.limits_title": "[limits]:",
    "config.saved": "Saved {name} = {value}.",
    # appdata (phase 10)
    "appdata.export_done": "Exported {files} files to {path}.",
    "appdata.confirm_overwrite": (
        "This machine already has tgmirror data; move it aside and import over it?"
    ),
    "appdata.backed_up": "Existing data moved aside to {path} (not deleted).",
    "appdata.import_done": "Imported {files} files.",
    "appdata.login_reminder": "Run `tgmirror login` to log in again.",
    # doctor
    "doctor.title": "tgmirror doctor",
    "doctor.session_missing_credentials": (
        "Session: no api_id/api_hash yet (run `tgmirror login`)."
    ),
    "doctor.credential_source": "Credential: from {source}.",
    "doctor.credential_move_suggested": (
        "Credential: still in config.toml, but this machine has a keyring — "
        "run `tgmirror login` again to move it there."
    ),
    "doctor.session_not_logged_in": "Session: not logged in (run `tgmirror login`).",
    "doctor.session_error": "Session: connection error — {detail}",
    "doctor.session_ok": "Session: valid, logged in as {who}.",
    "doctor.cryptg_ok": "cryptg: installed (faster crypto).",
    "doctor.cryptg_missing": (
        "cryptg: not installed — slower but still works (uv sync to add it)."
    ),
    "doctor.sessions_perm_ok": "Session directory permissions: owner-only (0700).",
    "doctor.sessions_perm_warn": (
        "Session directory permissions: {mode}, wider than 0700 — another user on this machine "
        "could read the session (having it is enough to log in, no password needed). Run "
        "tgmirror once more to tighten it automatically, or chmod 700 it yourself."
    ),
    "doctor.no_destinations": (
        "Destinations: no source/destination pair yet (run `tgmirror clone`)."
    ),
    "doctor.destinations_need_session": "Destinations: need a session to check permissions.",
    "doctor.destination_ok": "Destination {title}: still writable.",
    "doctor.destination_bad": (
        "Destination {title}: NO LONGER writable (not admin, or posting is off)."
    ),
    "doctor.destination_error": "Destination {title}: could not check it — {detail}",
    "doctor.safety_account": (
        "Safety: use an account with history; a brand-new one gets limited faster."
    ),
    "doctor.safety_sessions": (
        "Safety: do not run several tools/sessions on the same account at once."
    ),
    "doctor.safety_risk": (
        "Safety: automating a user account risks it being limited. "
        "tgmirror reduces that risk, it does not remove it."
    ),
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
    "run.slow_mode": (
        "The destination has slow mode on ({seconds}s): posts to it are at least {seconds}s apart."
    ),
    # backup (phase 11a)
    "backup.pick_dir": "Directory to back up into (created if missing)",
    "backup.pick_dir_empty": "The path cannot be empty.",
    "backup.filter_kept": "This directory already has a backup: keeping its filter.",
    "backup.preview": (
        "Preview: {matched} of the first {scanned} messages of the chosen range will be saved."
    ),
    "backup.preview_empty": "Preview: the source has no messages in the chosen range.",
    "backup.confirm_start": "Back up {src} into {dir} now?",
    "backup.start": "Backup {id}: {src} → {dir}.",
    "backup.analyzed": (
        "Estimate: up to {total} messages to look at (Telegram's count, the filter not subtracted)."
    ),
    "backup.progress": (
        "Backup {id}: {done} messages saved, {skipped} filtered out (up to id {cursor})."
    ),
    "backup.result": "Backup {id}: {status}. {done} messages saved (up to id {cursor}).",
    "backup.continue_hint": "Continue with: tgmirror backup ... {dir}",
    "backup.paused": "Paused. Press r to resume, q to stop.",
    "backup.resumed": "Resumed.",
    "backup.gone": "Message {id} was deleted at the source since it was listed; skipped.",
    "backup.flood_stopped": (
        "Telegram asked to wait {seconds}s. The backup is saved as waiting until "
        "{resume_at}; run `tgmirror backup` again after that."
    ),
    "backup.flood_waiting": (
        "Telegram asks to wait {seconds}s; waiting (Ctrl+C to stop, progress is saved on disk)."
    ),
    "backup.throttled": (
        "Telegram limited us repeatedly: pausing {delay}s between reads until things calm down."
    ),
    # delete (phase 20, decision D10)
    "delete.no_admin_channels": (
        "You administer no channel or group, so there is nothing to delete from."
    ),
    "delete.pick_source": "Delete messages from which channel or group?",
    "delete.needs_filter": (
        "An empty filter would delete EVERY message of the chat. If that is what you mean, add "
        "--all; otherwise add a filter (--media, --hashtag, --since, --selection, ...)."
    ),
    "delete.confirm_all": (
        "No filter: the plan will hold EVERY message of '{chat}'. Make the plan anyway?"
    ),
    "delete.nothing": "The filter selects no message in '{chat}': nothing to delete.",
    "delete.plan_header": (
        "Delete plan O{id} for '{chat}': {messages} messages ({units} units, {albums} albums)."
    ),
    "delete.plan_media": "  by kind: {items}",
    "delete.plan_months": "  by month: {items}",
    "delete.plan_years": "  by year: {items}",
    "delete.plan_sample": "  id {id} · {date} · {media} · {text}",
    "delete.plan_gap": "  …",
    "delete.type_count": (
        "This deletes {count} messages from '{chat}' and cannot be undone. Type {count} to delete"
    ),
    "delete.needs_expect": (
        "--yes needs --expect N, N being the number of messages in the plan ({count}): so a "
        "filter that matches more than you thought deletes nothing by mistake."
    ),
    "delete.expect_mismatch": (
        "The plan holds {count} messages, not {expected} as --expect said. Nothing deleted."
    ),
    "delete.needs_yes": (
        "Nothing deleted. Plan O{id} ({count} messages) is saved; to delete, run: "
        "tgmirror delete --resume O{id} --yes --expect {count}"
    ),
    "delete.cancelled": "Plan O{id} dropped; nothing deleted.",
    "delete.finished": "Operation O{id} is already {status}; nothing left to do.",
    "delete.start": "Operation O{id}: deleting {count} messages from '{chat}'.",
    "delete.progress": (
        "O{id}: {done}/{planned} messages deleted, {skipped} left alone (changed), "
        "{gone} already gone."
    ),
    "delete.result": "Operation O{id}: {status}. {done}/{planned} messages deleted.",
    "delete.left_alone": (
        "{count} messages changed since the plan and no longer match the filter, so they were "
        "left alone."
    ),
    "delete.gone": (
        "{count} messages were no longer there when their turn came (deleted by someone else)."
    ),
    "delete.continue_hint": "Delete the rest with: tgmirror delete --resume O{id}",
    "delete.paused": "Paused. Press r to resume, q to stop.",
    "delete.resumed": "Resumed.",
    "delete.settled": (
        "The last run stopped in the middle of a batch: {deleted} messages were deleted, {again} "
        "are still there and will be deleted now."
    ),
    "delete.flood_stopped": (
        "Telegram asked to wait {seconds}s. The operation is saved as waiting until {resume_at}; "
        "run `tgmirror delete --resume` again after that."
    ),
    "delete.flood_waiting": (
        "Telegram asks to wait {seconds}s; waiting (Ctrl+C to stop, progress is saved)."
    ),
    "delete.throttled": (
        "Telegram limited us repeatedly: pausing {delay}s between calls until things calm down."
    ),
    "delete.budget_learned": (
        "Telegram made us wait {seconds}s: the budget of lane {action} is now {budget} messages "
        "per {window} h."
    ),
    "delete.cap_warning": (
        "{sent} messages handled in the last 24 hours (daily_cap reminder: {cap})."
    ),
    # the safety net before a delete (phase 20.2)
    "delete.confirm_backup": (
        "Back up these {count} messages before deleting? Telegram cannot undo a delete; a backup "
        "can be put back with `tgmirror restore`, but the messages return at the end of the chat."
    ),
    "delete.confirm_backup_dir": "Save the backup in {dir}?",
    "delete.backup_stopped": (
        "The backup is not finished ({status}), so nothing was deleted. Plan O{id} is kept; back "
        "up the rest and delete: tgmirror delete --resume O{id} --yes --expect {count}"
    ),
    "delete.backup_done": (
        "Backup finished: {dir}. Put it back with `tgmirror restore {dir}` (the messages return "
        "at the end of the chat)."
    ),
    "delete.backup_note": "Backup taken before deleting: {dir}",
    # restore (phase 11b)
    "restore.pick_dir": "Backup directory to restore",
    "restore.pick_dir_empty": "The path cannot be empty.",
    "restore.pick_destination": "Restore into which destination?",
    "restore.preview": (
        "Preview: {matched} of the first {scanned} messages of the chosen range will be restored."
    ),
    "restore.preview_empty": "Preview: the backup has no messages in the chosen range.",
    "restore.confirm_start": "Restore {src} into {dst} now?",
    "restore.dst": "Destination: {channel}",
    "restore.dst_created": "Destination (just created): {channel}",
    "status.running": "running",
    "status.paused": "paused",
    "status.stopped": "stopped",
    "status.waiting_flood": "waiting (flood)",
    "status.done": "done",
    "status.planned": "planned",
    "status.cancelled": "cancelled",
    "status.failed": "failed",
    "control.pause_requested": "Asked run {id} to pause; it holds after the current batch.",
    "control.stop_requested": "Asked run {id} to stop; it stops after the current batch.",
    "warn.noforwards_unadministered": (
        "The source has 'Restrict saving content' on and this account is NOT an admin of it; "
        "tgmirror cannot check that you own it through another account."
    ),
    "warn.responsibility": (
        "WARNING: you are copying a source that forbids saving its content on your own "
        "statement that you may. You take full responsibility for your right to copy it, "
        "copyright and Telegram's terms included."
    ),
    "warn.noforwards_admin": (
        "The source has 'Restrict saving content' on. You are an admin, so you can turn it off "
        "temporarily, or use --mode reupload (download and send again; it asks to confirm)."
    ),
    "warn.topic_loss": (
        "The source is a forum but the destination has no topics: the topic structure will be "
        "dropped entirely."
    ),
    "warn.topic_as_hashtag": (
        "The source is a forum but the destination has no topics: topic names are kept as "
        "hashtags, so messages in topics are sent again instead of forwarded "
        "(--no-topic-as-hashtag to drop them)."
    ),
    "warn.route_hashtags": (
        "The destination is not a forum, so it has no topics: the name `topics.route` gives each "
        "message is kept as a hashtag, and the messages that get one are sent again instead of "
        "forwarded."
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
    "err.server_failed": (
        "Telegram answered this request with an internal error (after several tries). Usually "
        "passing: try again in a few minutes. If it keeps failing for that very request (e.g. "
        "counting a huge topic), try another way: `inspect --quick` without --topic, or a "
        "scan with `inspect`."
    ),
    "err.generic": "{detail}",
    "err.aborted": "Cancelled.",
    "err.no_tty": (
        "This command needs an interactive terminal (the login code arrives via Telegram)."
    ),
    "err.missing_flag": "Missing {flag} (no terminal to ask).",
    "err.conflicting_flags": "Do not use {flags} together.",
    "err.channel_id_as_option": (
        "«{given}» is read as an option because it starts with «-». Write the id without "
        "the minus ({bare}) or put «--» before it, after the other options: "
        "tgmirror ... -- {given}"
    ),
    "err.needs_yes": (
        "A new channel would be created, so {flag} is required without a terminal to confirm."
    ),
    "err.channel_not_found": (
        "No joined channel or group matches '{ref}'. See `tgmirror channels`."
    ),
    "err.ambiguous": "'{ref}' matches several chats: {matches}. Use the id or @username.",
    "err.source_restricted": (
        "'{title}' has 'Restrict saving content' on and this account is not an admin, so "
        "tgmirror will not copy it (decision D3). If you own it through another account and "
        "take full responsibility, run again with --mode reupload "
        "--yes-i-administer-this-channel; or ask the owner to turn the option off."
    ),
    "err.dest_not_writable": "You must be an admin allowed to post in '{title}'. Pick another one.",
    "err.same_channel": "Source and destination are the same chat.",
    "err.title_empty": "The channel title must not be empty.",
    "err.title_too_long": "The channel title is at most 128 characters.",
    "err.about_too_long": "The description is at most 255 characters.",
    "err.mode_unsupported": "There is no mode '{mode}'. Use auto, copy or reupload.",
    "err.opt_caption_unknown": "--caption must be keep, strip-links, append or none.",
    "err.opt_caption_text_missing": '--caption append needs --caption-text "<text>".',
    "err.opt_caption_text_unused": "--caption-text only goes with --caption append.",
    "err.opt_caption_needs_reupload": (
        "Changing captions means sending the messages again, which a forward cannot do: use "
        "--mode auto (only messages with a caption take that road) or --mode reupload, not copy."
    ),
    "err.opt_reupload_flags_need_reupload": (
        "--reset-polls, --ignore-unsupported and --placeholder only mean something with "
        "--mode reupload."
    ),
    "err.opt_transform_needs_reupload": (
        "A transform changes captions, so the messages must be sent again, which a forward "
        "cannot do: use --mode auto (only the messages that need it take that road) or "
        "--mode reupload, not copy."
    ),
    "err.opt_forward_header_needs_forward": (
        "`forward_header: keep` (--keep-forward-header) only exists on a forwarded message: it "
        "does not go with --mode reupload (restore always uploads again), a --caption other than "
        "keep, or --topic-as-hashtag."
    ),
    "err.transform": "Invalid transform: {detail}",
    "err.opt_route_needs_rewrite": (
        "The destination has no topics, so the name `topics.route` gives each message is kept as "
        "a hashtag, which needs the message sent again (a forward cannot add one): use --mode "
        "auto or --mode reupload, not --mode copy."
    ),
    "err.opt_topic_hashtag_needs_rewrite": (
        "--topic-as-hashtag needs messages sent again (a forward cannot add a hashtag): use "
        "--mode auto or --mode reupload, not --mode copy."
    ),
    "err.unsupported_media": (
        "Message {id} is a {kind}, which cannot be copied. Run again with --ignore-unsupported "
        "to leave it out, or --placeholder to post a short note in its place. Progress is saved."
    ),
    "err.needs_admin_ack_rerun": (
        "'{title}' has 'Restrict saving content' on and this run was not confirmed by you. Run "
        "`tgmirror clone` again for the same source and destination with --mode reupload "
        "--yes-i-administer-this-channel."
    ),
    "err.needs_admin_ack": (
        "'{title}' has 'Restrict saving content' on. Downloading and re-sending its content is "
        "your call: if you own or administer it and may copy it, add "
        "--yes-i-administer-this-channel (--yes does not stand in for it)."
    ),
    "err.run_busy": (
        "Run {id} is held by another process. If you are sure it is dead, run again "
        "with --force-takeover."
    ),
    "err.run_waiting_flood": "Telegram asked to wait; the clone must not run again before {until}.",
    "err.daily_cap": (
        "{sent} messages sent in the last {window} hours, the budget of {budget} messages per "
        "{window} hours that Telegram's long wait taught us is reached. Progress is saved; "
        "run `tgmirror run` again after {until}."
    ),
    "err.run_waiting_peer_flood": (
        "Telegram flagged the account as spam (PEER_FLOOD) on the previous run. Rest "
        "until {until}, then run again."
    ),
    "err.forwards_restricted": (
        "The source has 'Restrict saving content' on, so Telegram refuses to forward. If you are "
        "an admin, turn that option off at the source, or run again with --mode reupload."
    ),
    "err.store": "Database error: {detail}",
    "err.schema_too_new": "The database was made by a newer tgmirror. Please upgrade tgmirror.",
    "err.appdata_export_busy": (
        "A run is holding the data (running/paused). Stop it (`tgmirror stop`), then export again."
    ),
    "err.appdata_import_needs_yes": (
        "This machine already has tgmirror data; rerun with --yes to move it aside and import, "
        "or run in a terminal to be asked."
    ),
    "err.appdata_schema_newer": (
        "The export uses schema {found}, this tgmirror only knows up to {known}. Upgrade "
        "tgmirror, then import again."
    ),
    "err.appdata_checksum": (
        "The file {entry} in the archive does not match its recorded checksum — it may be corrupt."
    ),
    "err.appdata_format": "Cannot read the export file: {detail}",
    "err.backup_needs_admin_ack": (
        "'{title}' restricts saving content. Downloading and keeping that content is your call: "
        "if you own/administer it and may copy it, add --yes-i-administer-this-channel "
        "(--yes does not stand in for it)."
    ),
    "err.backup_filters_changed": (
        "{dir} was already backed up with a different filter. A backup keeps no record of what a "
        "filter skipped, so it cannot safely continue with a new one; use another directory."
    ),
    "err.backup_wrong_source": (
        "{dir} already holds a backup of '{title}', not the source you gave."
    ),
    "err.backup_busy": (
        "Backup {id} is being run by another process. If you are sure it is dead, "
        "run again with --force-takeover."
    ),
    "err.not_admin": (
        "You are not an administrator of '{title}', so you cannot delete messages there."
    ),
    "err.op_none": "There is no operation yet.",
    "err.op_not_found": "No operation {ref!r}.",
    "err.op_busy": (
        "Operation O{id} is being run by another process. If you are sure it is dead, "
        "run again with --force-takeover."
    ),
    "err.corrupt_messages_file": (
        "{path}: line {line} is not valid JSON. The file may be damaged — fix or remove that "
        "line and try again; tgmirror will not silently skip it, to avoid losing what comes after."
    ),
    "err.disk_full": (
        "Out of disk space: {detail}. Progress is already saved — free up space and run again."
    ),
    "err.os_error": "OS/file error: {detail}",
    "err.crashed": (
        "Something unexpected went wrong: {detail}. Run again as `tgmirror --debug <command> "
        "...` (--debug goes before the command name) for the full traceback."
    ),
    "err.backup_dir_missing": (
        "{dir} is no longer readable (backup.json is missing) — it may have been deleted or "
        "moved. Use --from-backup NEW_DIR to point this pair at it, or --fresh to start over."
    ),
    "err.wrong_backup_source": (
        "{dir} backs up a different source (id {found}), not this pair's (id {expected})."
    ),
    "err.not_a_backup": "{dir} is not a valid backup directory (no backup.json).",
    "err.filter": "Invalid filter: {detail}",
    "filter.pick": "Filter what gets copied?",
    "filter.keep": "Keep the filter of the previous run",
    "filter.use_last": "Use the latest inspect result for this source ({count} messages, {when})",
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
    "filter.ask_topics": "Only these topics (space to select; none selected = all)",
    "filter.ask_file": "Path of the YAML file",
    "filter.file_has_transform": (
        "{path} carries a transform; this step only loads the filter. Use "
        "`--selection {path}` to apply the transform too."
    ),
    "clone.preview": (
        "Preview: {matched} of the first {scanned} messages in the chosen range would be copied."
    ),
    "clone.preview_empty": "Preview: the source has no messages in the chosen range.",
    "clone.preview_example": "  · {text}",
    "run.skipped": "{count} messages were left out by the filter.",
    "run.skipped_unsupported": "Left out message {id} ({reason}): it cannot be copied.",
    "run.unsupported_total": (
        "{count} messages that cannot be copied were left out (see `tgmirror history {id}`)."
    ),
    "run.links_left": (
        "{count} links point at a message with no copy in the destination yet (a later one, or "
        "one the filter left out): left as they were, still pointing at the source."
    ),
    "run.reply_lost": (
        "{count} messages answered one that has no copy in the destination (outside what was "
        "copied, failed, or in another topic): sent, but without the reply."
    ),
    "clone.confirm_protected": (
        "'{title}' has 'Restrict saving content' on: its owner forbade saving its content. Go on "
        "only if you own or administer it and may copy it. Download and re-send every message?"
    ),
    "clone.confirm_unadministered": (
        "'{title}' has 'Restrict saving content' on and this account is not its admin. If you "
        "own this channel through a different account and take full responsibility for copying "
        "its content (copyright and Telegram's terms included — tgmirror cannot check this), "
        "type {flag} verbatim then Enter to confirm. Anything else, or Esc: this source is not "
        "copied."
    ),
    "options.protected": (
        "The source forbids saving its content, so downloading and re-sending (reupload) is the "
        "only way."
    ),
    "options.customise_auto": "Change the captions of media messages? (default: no)",
    "options.customise_reupload": ("Customise captions and what cannot be copied? (default: no)"),
    "options.pick_mode": "How to copy?",
    "options.mode_auto": (
        "Automatic: server-side forward (fast, no bandwidth); messages whose caption changes are "
        "sent again by file id, nothing downloaded (a source that restricts saving: re-uploaded)"
    ),
    "options.mode_copy": "Server-side forward only (copy): captions cannot change",
    "options.mode_reupload": (
        "Download and send again (slow; needed to change captions or when saving is restricted)"
    ),
    "options.pick_caption": "Captions of media messages?",
    "options.caption_keep": "Keep them",
    "options.caption_strip-links": (
        "Remove links and mentions that point at the source (captioned messages are sent again)"
    ),
    "options.caption_append": "Add some text at the end (captioned messages are sent again)",
    "options.caption_none": "Remove them (captioned messages are sent again)",
    "options.ask_caption_text": "Text to add at the end of each caption:",
    "options.pick_flags": "For messages that cannot be forwarded (tick what you want):",
    "options.flag_reset_polls": "Re-create polls and quizzes (they lose all their votes)",
    "options.flag_ignore_unsupported": (
        "Leave out games, invoices, unanswered quizzes instead of stopping"
    ),
    "options.flag_placeholder": "Post a short note where a message was left out",
    "run.progress_filtered": (
        "Run {id}: {done} messages copied, {skipped} left out by the filter, {failed} "
        "failed (source up to id {cursor})."
    ),
    "clone.confirm_start": "Clone {src} → {dst} now?",
    "clone.dst_will_be_created": "'{title}' (a new channel will be created)",
    "clone.route_by_hashtag": (
        "Split by hashtag: each message goes to the topic named after its hashtag (a hashtag in "
        "the message when the destination has no topics)? Finer rules: use --transform"
    ),
    "clone.route_skip": ("Hashtags that do NOT name a topic, separated by commas (empty for none)"),
    "clone.route_pick": "A message with several hashtags: which one names its topic?",
    "clone.route_pick_first": "The first hashtag",
    "clone.route_pick_last": "The last hashtag",
    "clone.topic_as_hashtag": (
        "Keep the topic name as a hashtag in each message? (a forward cannot add one, so "
        "messages in a topic are sent again instead of forwarded)"
    ),
    "clone.confirm_topic_loss": (
        "The destination has no topics and there is no way to keep them (--mode copy cannot "
        "add a hashtag): every topic will be dropped. Continue?"
    ),
    "err.topic_loss_needs_yes": (
        "The destination has no topics; every topic will be dropped since --mode copy cannot "
        "add a hashtag. Add --yes (or --no-topic-as-hashtag) to agree when there is no terminal."
    ),
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
    "run.pick_pair": "Continue which pair? (type to filter)",
    "run.pick_pair_line": "#{id}  {src} → {dst}   {mode}   {status}",
    "resume.backup_dir_missing_title": "Pair {id}: the backup directory is not readable",
    "resume.pick_new_dir": "Choose a different backup directory",
    "resume.start_fresh": "Start over (forget progress)",
    "resume.cancel": "Cancel",
    # foreground TUI (Rich Live, ui/tui.py)
    "tui.header": 'tgmirror ▸ run {id}  "{src} → {dst}"   mode={mode}   delay={delay}s',
    "tui.progress_total": "{handled}/{total} messages (~{percent}%)",
    "tui.progress_plain": "{handled} messages handled",
    "tui.speed": "{speed} msg/s, ETA {eta}",
    "tui.speed_no_eta": "{speed} msg/s",
    "tui.counts": "{done} copied · {failed} failed · {skipped} filtered out",
    "tui.floods": " · {count} throttled (last {ago} ago)",
    "tui.keys": "[p] pause   [r] run   [q] stop",
    "control.nothing_running": "No clone is running.",
    "history.empty": "No runs yet. Start with `tgmirror clone`.",
    "history.title": "Recent runs",
    "history.col_run": "ID",
    "history.kind_run": "Run",
    "history.op_header": "Operation O{id}: {kind} · '{chat}'",
    "history.op_kind_delete": "delete messages",
    "history.line_backup": "Backup:      {dir}{note}",
    "history.line_backup_unfinished": " (not finished)",
    "history.line_counts_op": (
        "Result:      {done} deleted, {skipped} left alone, {gone} already gone, {failed} failed "
        "(plan of {planned} messages)"
    ),
    "history.kind_backup": "Backup",
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
    "history.line_counts_backup": "Result:      {done} copied, {skipped} left out by the filter",
    "history.line_cursor": "Source:      from id {start} to id {end}",
    "history.line_filter": "Filter:      {filter}",
    "history.no_filter": "no filter",
    "history.failed_title": "Failed messages ({count}):",
    "history.failed_line": "  · source message {id}: {reason}",
    "history.failed_more": "  … and more; see --json",
    "history.floods_title": "Limits from Telegram:",
    "history.flood_line": "  · {ts} {kind} {seconds}s ({method}){where}",
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
    "retry.nothing": "Run {id} has no failed messages to retry.",
    "run.retry_start": ("Run {id}: retrying {count} failed messages of run {of} ({src} → {dst})."),
    "run.progress_retry": "Run {id}: {done} messages copied again, {failed} still failing.",
    "run.retry_hint": "Retry the failed messages with: tgmirror retry {id}",
    "run.analyzed": (
        "Estimate: up to {total} messages to look at (Telegram's count, the filter not subtracted)."
    ),
    "run.cap_rest": (
        "Learned budget of {budget} messages per {window} hours: with {total} messages the run "
        "has to rest about {rest} more before it is done (see docs/05-chong-flood.md)."
    ),
    "rest.hours": "{n} h",
    "rest.days": "{n} day(s)",
    "run.cap_warning": (
        "{sent} messages sent in the last 24 hours, past the daily_cap reminder ({cap}). Only a "
        "reminder: nothing stops the run but the budget learned from Telegram's long waits."
    ),
    "run.budget_learned": (
        "Telegram asked to wait {seconds}s: the {action} lane's budget is now {budget} messages "
        "per {window} hours (it grows 10% per 24 hours without a block, never past 95% of the "
        "{bad} that drew a block)."
    ),
    "run.limits_reset": (
        "--reset-limits: forgot the limits learned by {count} write lanes into this destination; "
        "nothing stops them until their next long wait."
    ),
    "run.progress_total": (
        "Run {id}: {handled}/{total} messages (~{percent}%): {done} copied, "
        "{skipped} left out by the filter, {failed} failed."
    ),
    "run.reference_fallback": (
        "Message {id}: Telegram would not send it again by file id, downloading and uploading it."
    ),
    "run.topics_limit": (
        "Created {limit} new topics from hashtags (the limit of `auto: all`): messages with "
        "newer hashtags go to General. Raise `limit` in the transform, or list the hashtags "
        "you want."
    ),
    "run.transfer_download": "Downloading message {id}: {percent}% ({done} / {total}{speed}).",
    "run.transfer_upload": "Uploading message {id}: {percent}% ({done} / {total}{speed}).",
    "run.retry_continue_hint": "Carry on retrying with: tgmirror retry {of}",
    "retry.gone": "{count} messages no longer exist at the source and cannot be copied; left out.",
    "retry.still_failing": (
        "{count} messages still fail. See `tgmirror history {id}`, or retry: tgmirror retry {id}"
    ),
    "history.line_retry": "Retry of:    the failed messages of run {of}",
    "history.line_gone": "Gone at source: {count} messages that cannot be copied (left out)",
    "status.none_live": "No run is running; this is the latest one.",
    "status.abandoned": (
        "No process holds this run (last sign of life at {at}). The next run of this pair "
        "records it as interrupted and carries on."
    ),
    "status.line_progress": "Progress:    ~{percent}% (source up to id {cursor} / {head})",
    "status.line_progress_retry": (
        "Progress:    {percent}% ({handled} / {total} failed messages retried)"
    ),
    "status.line_progress_unknown": "Progress:    source up to id {cursor} (total not known)",
    "status.line_progress_items": (
        "Progress:    ~{percent}% ({handled} / at most {total} messages)"
    ),
    "status.line_cap": (
        "Budget:      up to {left} messages left, budget {cap} per {window} h: about {rest} more "
        "of rest"
    ),
    "status.line_budget": (
        "Budget:      {action}: {budget} messages per {window} h ({sent} sent); set after a "
        "{seconds}s wait at {at}"
    ),
    "status.line_budget_none": (
        "Budget:      not learned yet (no long wait so far; only pacing and FloodWait limit it)"
    ),
    "status.line_bounds": (
        "Limit:       {action}: blocked at {bad} messages, ran clean up to {ok}; last put to the "
        "test {at}"
    ),
    "limits.none": "No write lane is recorded yet (no run has sent anything).",
    "limits.dest": "Destination {dst} ({title})",
    "limits.dest_unknown": "Destination {dst}",
    "limits.lane_learned": (
        "  {action}: {budget} messages per {window} h ({sent} sent); blocked at {bad}, ran clean "
        "up to {ok}; {seconds}s wait at {flood_at}; last put to the test {checked}; pace {delay}s"
    ),
    "limits.lane_unlearned": (
        "  {action}: limit not learned ({sent} sent in the last 24 hours; only the {delay}s pace "
        "and FloodWait limit it)"
    ),
    "limits.reset_done": (
        "Forgot the limits of {count} write lanes {where}. The learned pace stays."
    ),
    "limits.reset_none": "No write lane has learned a limit {where}.",
    "limits.where_dst": "into {dst}",
    "limits.where_all": "(every destination)",
    "limits.unknown": "-",
    "err.limits_reset_target": (
        "Say which destination to forget the limits of: --dst DEST (an id or a title from "
        "`tgmirror limits`), or --all for every destination."
    ),
    "err.limits_reset_both": "--dst and --all cannot be combined.",
    "err.limits_dst_unknown": (
        "No destination '{ref}' among the lanes that learned something. Use an id or a title "
        "from `tgmirror limits`."
    ),
    "err.limits_dst_ambiguous": "'{ref}' matches several destinations: {matches}. Use the id.",
    "err.limits_busy": (
        "Run or backup {id} is running elsewhere: it keeps the limits in memory and would write "
        "them back over this. Stop it (`tgmirror stop`) first."
    ),
    "status.line_speed": "Speed:       {speed} messages/s (average since it began){eta}",
    "status.line_speed_unknown": "Speed:       not enough data yet",
    "status.eta": ", about {eta} left",
    "status.line_resume": "Waiting until: {at}{note}",
    "status.line_limiter": (
        "Limits:      {delay}s between sends; {sent} messages sent in the last 24 hours "
        "(reminder at {cap})"
    ),
    "status.line_floods": "Telegram:    limited {count} times in the last 24 hours{last}",
    "status.line_floods_none": "Telegram:    not limited once in the last 24 hours",
    "status.last_flood": "; latest {ago} ago ({kind}, {seconds}s)",
    "status.retry_hint": "{count} messages failed. Retry: tgmirror retry {id}",
    "duration.hours": "{hours} h {minutes} min",
    "duration.minutes": "{minutes} min",
    "duration.seconds": "{seconds} s",
    # full-screen menu (ui/menu/, launched by a bare `tgmirror` on a real terminal)
    "menu.not_logged_in": "not logged in",
    "menu.no_terminal": "Could not read the keyboard on this terminal.",
    "menu.footer_main": "up/down select - Enter choose - Ctrl+C quit",
    "menu.footer_pick": "up/down select - Enter choose - Esc back",
    "menu.footer_back": "Press any key to go back",
    "menu.footer_info": "Press any key to continue",
    "menu.footer_filter": "Type to filter - Esc back",
    "menu.item_clone": "New clone...",
    "menu.item_explore": "Explore a channel (count, filter, then copy)...",
    "explore.title": "Explore > {src}",
    "explore.footer": (
        "left/right column - up/down row - Enter filter - x exclude - Backspace undo - Tab "
        "other column - l list - s save @last - t transform - c clone - b backup - d "
        "delete - u update - R re-read - Esc"
    ),
    "explore.footer_building": "q or Esc: stop reading and use what there is",
    "explore.index_line": "{messages} messages in the index - updated {when}",
    "explore.partial": "The index does not reach the end of the source: press u to read on.",
    "explore.flood": "Telegram asked to wait {seconds} s: using the index as it is (u: retry).",
    "explore.empty": "The source has no messages in the index.",
    "explore.filter": "Filter: {steps}",
    "explore.no_filter": "(not filtered yet)",
    "explore.not_filterable": "This row cannot become a filter.",
    "explore.route_not_filterable": (
        "A destination topic is what the rules give, not something a message has: filter "
        "by hashtag, media kind, ..."
    ),
    "explore.transform_title": "Load a transform",
    "explore.transform_prompt": (
        "Transform file with `topics.route` (empty: drop the destination-topic column)"
    ),
    "explore.transform_none": "{path} holds no routing rule (`topics.route` / `hashtag_to_topic`).",
    "explore.transform_loaded": (
        'Routing rules loaded: the "By destination topic" column (Tab to reach it).'
    ),
    "explore.transform_dropped": "Routing rules dropped.",
    "explore.hidden_forward": "The forward hides its origin: it cannot be filtered on.",
    "explore.channel_post": "A channel post has no sender to filter on.",
    "explore.exclude_month": "A month cannot be excluded: pick a month to keep instead.",
    "explore.saved": "Filter saved ({count} messages) as @last: use `--selection @last`.",
    "explore.list_title": "Matching messages (showing {shown} of {total})",
    "menu.item_backup": "Backup...",
    "menu.item_delete": "Delete messages from a chat of mine...",
    "menu.item_restore": "Restore...",
    "menu.item_resume": "Continue",
    "menu.item_retry": "Retry failures",
    "menu.item_status": "Status",
    "menu.item_history": "History",
    "menu.item_channels": "Joined channels",
    "menu.item_account": "Account",
    "menu.item_config": "Config",
    "menu.item_quit": "Quit",
    "menu.history_counts": "{done} copied, {failed} failed",
    "menu.history_counts_backup": "{done} copied",
    "menu.yes": "Yes",
    "menu.no": "No",
    "menu.confirm_logout": "Log out {who}?",
    "menu.item_login": "Log in",
    "menu.footer_text": "Type, then Enter - Esc back",
    "menu.footer_path": "Type - Tab to complete - Enter - Esc back",
    "menu.footer_select": "up/down select - type to filter - Enter choose - Esc back",
    "menu.footer_check": "up/down move - Space tick - Enter done - Esc back",
    "menu.footer_working": "Working... - Ctrl+C quit",
    "menu.working": "Working...",
    "menu.no_match": "(nothing matches)",
    "menu.config_edit_title": "Edit {name}",
    "menu.config_prompt_value": "New value for {name}",
    "menu.running_elsewhere": "running elsewhere",
    # inspect (phase 18a)
    "inspect.source": "Source: {src}",
    "inspect.totals": (
        "Read {messages} messages ({units} units, {albums} albums), {size}, from {first} to {last}."
    ),
    "inspect.selected": (
        "Matches the filter: {messages} messages ({units} units, {albums} albums), {size}."
    ),
    "inspect.empty": "The source has no messages.",
    "inspect.partial": (
        "Stopped early: the report only covers messages up to id {last_id}, not the whole source."
    ),
    "inspect.progress": "Reading: {messages} messages (up to id {last_id})...",
    "inspect.quick_title": "Telegram's own counters ({scope}), one request:",
    "inspect.quick_scope_all": "the whole source",
    "inspect.quick_scope_topic": "topic {topic}",
    "inspect.quick_note": (
        "Telegram classifies by itself, so it can differ a few % from a full scan (a message "
        "hidden for violating the terms still counts as a video/photo). No range by id or date; "
        '"—" means Telegram does not return that kind. Run `tgmirror inspect` to scan and filter.'
    ),
    "inspect.quick_alone": (
        "--quick counts the whole source or one topic (--topic ID): not with other filters, "
        "--by/--list/--csv/--save/--plan or --offline/--refresh/--no-index."
    ),
    "inspect.quick_backup": "--quick asks Telegram, so it does not apply to a backup directory.",
    "inspect.quick_topic_not_forum": (
        "--topic only applies to a forum source, and {src} is not a forum."
    ),
    "inspect.quick_topic_unknown": (
        "The forum {src} has no topic {topic}: `tgmirror topics <source>` lists its topics."
    ),
    "inspect.kind.photo": "Photos",
    "inspect.kind.video": "Videos",
    "inspect.kind.document": "Documents",
    "inspect.kind.audio": "Music",
    "inspect.kind.voice": "Voice",
    "inspect.kind.video_note": "Round videos",
    "inspect.kind.gif": "GIFs",
    "inspect.kind.link": "With a link",
    "inspect.kind.geo": "Locations",
    "inspect.kind.contact": "Contacts",
    "inspect.kind.poll": "Polls",
    "inspect.kind.pinned": "Pinned",
    "inspect.indexing": "Updating the index: read {messages} messages (up to id {last_id})...",
    "inspect.index_state": (
        "Local index: {messages} messages, updated {when} (views/reactions and messages edited "
        "or deleted since are only refreshed by running with --refresh)."
    ),
    "inspect.index_offline": (
        "Local index (no Telegram call): {messages} messages, updated {when}."
    ),
    "inspect.index_partial": (
        "The last update did not reach the end of the source; run again to go on."
    ),
    "inspect.index_not_for_backup": (
        "--refresh and --no-index are for a channel: a backup directory is read offline anyway."
    ),
    "inspect.offline_no_index": (
        "There is no index for {ref!r}: run `tgmirror inspect` once (without --offline) to build "
        "it. --offline takes the id, @username or exact title of a source inspected before."
    ),
    "inspect.stopping": (
        "Stopping; the report of what was read will be printed (Ctrl+C again quits at once)."
    ),
    "inspect.flood_waiting": (
        "Telegram asks to wait {seconds}s; waiting, then reading on (Ctrl+C stops)."
    ),
    "inspect.throttled": (
        "Telegram is limiting repeatedly: reading more slowly for now ({delay}s between requests)."
    ),
    "inspect.by.media": "By media type",
    "inspect.by.month": "By month",
    "inspect.by.year": "By year",
    "inspect.by.hashtag": "By hashtag",
    "inspect.by.topic": "By topic",
    "inspect.by.sender": "By sender",
    "inspect.by.forward": "By forward origin",
    "inspect.by.domain": "By link domain",
    "inspect.by.size": "By file size",
    "inspect.by.duplicate": "Files posted more than once",
    "inspect.by.route": "By destination topic",
    "inspect.route_needs_routing": (
        "--by route needs routing rules: give a --transform with `topics.route` (or "
        "--hashtag-to-topic)."
    ),
    "inspect.none_route": "(no rule matched)",
    "inspect.col.value": "Value",
    "inspect.col.messages": "Messages",
    "inspect.col.size": "Size",
    "inspect.none_forward": "(not forwarded)",
    "inspect.none_sender": "(channel post)",
    "inspect.other": "(other: {count} items)",
    "inspect.no_rows": "(none)",
    "inspect.not_a_forum": "(this source is not a forum, so it has no topics)",
    "inspect.list_title": "Matching messages",
    "inspect.list_more": (
        "... {count} more messages (--limit to see more, --csv FILE to export them all)."
    ),
    "inspect.csv_written": "Wrote {count} messages to {path}.",
    "inspect.by_unknown": "--by {value!r} does not exist; choose from: {choices}.",
    "inspect.bad_number": "{flag} must be a non-negative number.",
    # selections (phase 18a)
    "selection.exact_needs_selection": (
        "--exact needs --selection FILE (or @last): that is where the frozen list is."
    ),
    "selection.last_needs_src": (
        "--selection @last needs to know which source: give one (--src, or a backup directory) "
        "or use a selection file."
    ),
    "selection.no_last": (
        "There is no inspect result for «{title}» yet: run `tgmirror inspect` on that source "
        "first (or use --selection FILE)."
    ),
    "selection.source_mismatch": (
        "This selection file is for source {theirs}, not this command's source: message ids only "
        "mean something in their own source."
    ),
    "selection.no_transform": (
        "The selection file {path} carries a transform, but this command does not apply one "
        "(only clone and restore do): remove its transform section or use another command."
    ),
    "inspect.saved": "Selection saved to {path}.",
    "inspect.saved_ids": "Selection saved to {path} with a list of {count} ids ({ids}).",
    "inspect.freeze_needs_save": "--freeze needs --save FILE (where the frozen list goes).",
    "inspect.next_title": "Continue with exactly this filter:",
    "inspect.next_selection": "(this filter cannot be written as flags; use the selection file)",
    "inspect.plan_title": "Plan for {what}:",
    "inspect.plan_kind.clone": "clone (mode {mode}, caption {caption})",
    "inspect.plan_kind.backup": "backup",
    "inspect.plan_kind.restore": "restore",
    "inspect.col.way": "How",
    "inspect.col.units": "Units",
    "inspect.way.copy": "forward (Telegram copies server-side)",
    "inspect.way.reference": "sent again by file id (nothing downloaded)",
    "inspect.way.reupload": "downloaded and uploaded again",
    "inspect.way.download": "downloaded to disk",
    "inspect.way.left_out": "left out",
    "inspect.plan_writes": (
        "{calls} write calls to Telegram (batch {batch}); the limiter waits at least {time} "
        "between them."
    ),
    "inspect.plan_no_writes": "Nothing is written to Telegram.",
    "inspect.plan_transfer": (
        "{size} has to move (largest file {largest}); how long that takes depends on the "
        "connection and is not counted."
    ),
    "inspect.plan_blocked": (
        "It would stop at once at: {what}. Add --ignore-unsupported or --placeholder (with "
        "--mode reupload) to leave them out."
    ),
    "inspect.plan_left_out": "It would leave out: {what}.",
    "inspect.plan_topics": "Into topics (by the `topics.route` rules): {what}.",
    "inspect.plan_topics_rest": "no rule matched (the source topic's own, or General)",
    "inspect.plan_topics_hashtags": (
        "Into hashtags in the message (by the `topics.route` rules; the destination has no "
        "topics): {what}."
    ),
    "inspect.plan_topics_rest_hashtags": "no rule matched (forwarded as it is, no hashtag)",
    "inspect.plan_dst_no_topics_kind": (
        "--dst-no-topics only goes with --plan clone or --plan restore."
    ),
    "inspect.plan_unmatched": (
        "{count} messages matched no rule (fix the rules if that is not what you want); the "
        "first few:"
    ),
    "inspect.plan_protected": (
        "The source restricts saving content: the downloaded part needs your statement, "
        "--yes-i-administer-this-channel (decision D3)."
    ),
    "inspect.plan_unknown": "--plan {value!r} does not exist; choose clone, backup or restore.",
    "inspect.plan_flags_need_plan": (
        "--mode, --caption, --caption-text, --reset-polls, --ignore-unsupported, --placeholder "
        "and --topic-as-hashtag only go with --plan."
    ),
    "inspect.plan_restore_needs_backup": (
        "--plan restore needs a backup directory as the source; for a channel use --plan clone "
        "or backup."
    ),
    "inspect.plan_needs_channel": (
        "--plan {kind} needs a channel as the source; for a backup directory use --plan restore."
    ),
    "inspect.plan_backup_options": (
        "--plan backup downloads everything to disk, so it has no --mode/--caption/...; drop them."
    ),
}


# ---- edit and undo (phase 20.3) ---------------------------------------------------------------
# The messages ``tgmirror edit`` and ``tgmirror undo`` share with ``delete`` are the same sentences
# with another verb and command, so they are written once and filled in here (``<verb>``,
# ``<cmd>``).

_OP_VI = {
    "needs_expect": (
        "--yes cần đi kèm --expect N, với N là số tin trong kế hoạch ({count}): để một bộ lọc "
        "khớp nhiều hơn bạn nghĩ không <verb> nhầm."
    ),
    "expect_mismatch": (
        "Kế hoạch có {count} tin, không phải {expected} như --expect. Chưa <verb> gì."
    ),
    "needs_yes": (
        "Chưa <verb> gì. Kế hoạch O{id} ({count} tin) đã được lưu; để <verb>, chạy: "
        "<cmd> --resume O{id} --yes --expect {count}"
    ),
    "cancelled": "Đã bỏ kế hoạch O{id}; chưa <verb> gì.",
    "finished": "Thao tác O{id} đang ở trạng thái «{status}»; không còn gì để làm.",
    "start": "Thao tác O{id}: <verb> {count} tin trong «{chat}».",
    "progress": (
        "O{id}: {done}/{planned} tin đã <verb>, {skipped} để nguyên (đã đổi), {gone} đã mất "
        "sẵn, {failed} lỗi."
    ),
    "result": "Thao tác O{id}: {status}. {done}/{planned} tin đã <verb>.",
    "left_alone": (
        "{count} tin đã thay đổi từ lúc lập kế hoạch nên được để nguyên (không còn đọc như lúc "
        "lập kế hoạch)."
    ),
    "gone": "{count} tin đã không còn khi tới lượt (có người xoá trước).",
    "failed": "{count} tin Telegram không cho <verb> (lý do ở `tgmirror history O{id}`).",
    "continue_hint": "Làm tiếp phần còn lại: <cmd> --resume O{id}",
    "settled": (
        "Lần trước dừng giữa chừng: {deleted} tin đã <verb> xong, {again} tin chưa đổi sẽ làm tiếp."
    ),
    "flood_stopped": (
        "Telegram yêu cầu chờ {seconds}s. Thao tác được lưu ở trạng thái chờ đến {resume_at}; "
        "chạy lại với `<cmd> --resume` sau đó."
    ),
}

_OP_EN = {
    "needs_expect": (
        "--yes must come with --expect N, N being the number of messages in the plan ({count}): "
        "so a filter that matches more than you thought <verb>s nothing it should not."
    ),
    "expect_mismatch": (
        "The plan holds {count} messages, not {expected} as --expect says. Nothing done."
    ),
    "needs_yes": (
        "Nothing done. Plan O{id} ({count} messages) is saved; to go on, run: "
        "<cmd> --resume O{id} --yes --expect {count}"
    ),
    "cancelled": "Plan O{id} dropped; nothing done.",
    "finished": "Operation O{id} is «{status}»; there is nothing left to do.",
    "start": "Operation O{id}: <verb> {count} messages in '{chat}'.",
    "progress": (
        "O{id}: {done}/{planned} messages done, {skipped} left alone (changed), {gone} already "
        "gone, {failed} failed."
    ),
    "result": "Operation O{id}: {status}. {done}/{planned} messages done.",
    "left_alone": (
        "{count} messages changed since the plan was made and were left alone (they no longer "
        "read as they did)."
    ),
    "gone": "{count} messages were no longer there when their turn came (someone deleted them).",
    "failed": (
        "{count} messages Telegram would not let us <verb> (reasons in `tgmirror history O{id}`)."
    ),
    "continue_hint": "Carry on with the rest: <cmd> --resume O{id}",
    "settled": (
        "The last go stopped midway: {deleted} messages were done, {again} still unchanged will "
        "be done now."
    ),
    "flood_stopped": (
        "Telegram asks to wait {seconds}s. The operation is saved waiting until {resume_at}; run "
        "`<cmd> --resume` after that."
    ),
}


def _op_texts(table: dict[str, str], prefix: str, verb: str, cmd: str) -> dict[str, str]:
    return {
        f"{prefix}.{key}": text.replace("<verb>", verb).replace("<cmd>", cmd)
        for key, text in table.items()
    }


VI.update(_op_texts(_OP_VI, "edit", "sửa", "tgmirror edit"))
VI.update(_op_texts(_OP_VI, "undo", "trả lại", "tgmirror undo"))
EN.update(_op_texts(_OP_EN, "edit", "edit", "tgmirror edit"))
EN.update(_op_texts(_OP_EN, "undo", "put back", "tgmirror undo"))

VI.update(
    {
        "edit.no_admin_channels": "Bạn không quản trị kênh/nhóm nào, nên không có gì để sửa.",
        "edit.pick_source": "Sửa tin trong kênh/nhóm nào?",
        "edit.needs_filter": (
            "Bộ lọc trống sẽ xét TẤT CẢ tin của kênh. Nếu đúng ý bạn, thêm --all; nếu không, "
            "thêm bộ lọc (--media, --hashtag, --since, --selection, ...)."
        ),
        "edit.confirm_all": (
            "Không có bộ lọc: kế hoạch sẽ xét TẤT CẢ tin của «{chat}». Vẫn lập kế hoạch?"
        ),
        "edit.needs_transform": (
            "Cần --transform (hoặc một selection có `transform:`) với ít nhất một thao tác "
            "`caption`: sửa tin chỉ làm được những gì transform nói."
        ),
        "edit.unsupported": (
            "Transform có {what}: sửa tin tại chỗ chỉ làm được các thao tác `caption` (và "
            "`text_too`). Đổi topic hay đăng lại là việc của `repost`/`topics split`; reply "
            "và header forward chỉ có nghĩa khi sao chép; `links: rewrite` chỉ đi với "
            "--mirror R<n>."
        ),
        "edit.nothing": (
            "Không tin nào trong «{chat}» đổi với transform này: {unchanged} tin được chọn giữ "
            "nguyên, {lossy} tin có định dạng không viết lại nguyên vẹn được (không bao giờ sửa)."
        ),
        "edit.plan_header": (
            "Kế hoạch sửa O{id} trong «{chat}»: {messages} tin sẽ đổi ({units} đơn vị)."
        ),
        "edit.plan_left_out": (
            "  để nguyên: {unchanged} tin được chọn nhưng transform không đổi gì; {lossy} tin có "
            "định dạng không viết lại nguyên vẹn được (không bao giờ sửa)."
        ),
        "edit.plan_diff": "  id {id}\n    trước: {before}\n    sau:   {after}",
        "edit.type_count": (
            "Việc này sửa {count} tin trong «{chat}». Bản gốc được lưu: `tgmirror undo O{id}` "
            "trả lại. Gõ {count} để sửa"
        ),
        "edit.undo_hint": "Trả lại bản gốc: tgmirror undo O{id}",
        "edit.plan_mirror": (
            "Chỉ sửa các bản sao mà cặp «{src}» → «{dst}» (lần chạy R{run}) đã đăng: "
            "{copies} bản sao."
        ),
        "undo.type_count": (
            "Việc này đặt lại nội dung gốc của {count} tin trong «{chat}». Gõ {count} để làm"
        ),
        "undo.plan_header": (
            "Kế hoạch trả lại O{id} (hoàn tác sửa O{edit}) trong «{chat}»: {messages} tin."
        ),
        "undo.not_an_edit": "O{id} không phải một thao tác sửa: chỉ `edit` mới hoàn tác được.",
        "undo.never_applied": "Sửa O{id} chưa từng được áp dụng (chỉ mới là kế hoạch hoặc đã bỏ).",
        "undo.already": "Sửa O{id} đã được hoàn tác bởi O{undo}.",
        "undo.nothing": "Sửa O{id} chưa sửa được tin nào, không có gì để trả lại.",
        "undo.no_edit": "Chưa có thao tác sửa nào để hoàn tác.",
        "op.wrong_kind": (
            "O{id} là thao tác «{kind}», không phải «{expected}»: dùng `tgmirror {kind} --resume "
            "O{id}`."
        ),
        "history.op_kind_edit": "sửa tin",
        "history.op_kind_undo": "trả lại bản gốc",
        "history.line_undo_of": "Hoàn tác:    sửa O{id}",
        "history.failed_items": (
            "Tin Telegram không cho sửa ({count}); lý do là lời Telegram trả lời:"
        ),
        "history.failed_item": "  id {id}: {reason}",
        "history.line_counts_edit": (
            "Kết quả:     {done} đã sửa, {skipped} để nguyên, {gone} đã mất sẵn, {failed} lỗi "
            "(kế hoạch {planned} tin)"
        ),
        "history.line_counts_undo": (
            "Kết quả:     {done} đã trả lại, {skipped} để nguyên, {gone} đã mất sẵn, {failed} "
            "lỗi (kế hoạch {planned} tin)"
        ),
    }
)
EN.update(
    {
        "edit.no_admin_channels": (
            "You do not administer any channel or group, so there is nothing to edit."
        ),
        "edit.pick_source": "Edit messages in which channel or group?",
        "edit.needs_filter": (
            "An empty filter takes in EVERY message of the chat. If that is what you mean, add "
            "--all; otherwise add a filter (--media, --hashtag, --since, --selection, ...)."
        ),
        "edit.confirm_all": (
            "No filter: the plan will look at EVERY message of '{chat}'. Plan it anyway?"
        ),
        "edit.needs_transform": (
            "Needs --transform (or a selection with a `transform:`) with at least one `caption` "
            "operation: editing only does what a transform says."
        ),
        "edit.unsupported": (
            "The transform has {what}: editing in place only does the `caption` operations (and "
            "`text_too`). Moving to a topic or posting again is for `repost`/`topics split`; "
            "replies and the forward header only mean something when copying; `links: rewrite` "
            "only "
            "goes with --mirror R<n>."
        ),
        "edit.nothing": (
            "No message in '{chat}' changes with this transform: {unchanged} selected messages "
            "stay as they are, {lossy} have formatting that cannot be written back exactly (never "
            "edited)."
        ),
        "edit.plan_header": (
            "Edit plan O{id} in '{chat}': {messages} messages would change ({units} units)."
        ),
        "edit.plan_left_out": (
            "  left as they are: {unchanged} selected messages the transform does not change; "
            "{lossy} with formatting that cannot be written back exactly (never edited)."
        ),
        "edit.plan_diff": "  id {id}\n    before: {before}\n    after:  {after}",
        "edit.type_count": (
            "This edits {count} messages in '{chat}'. The originals are kept: `tgmirror undo "
            "O{id}` puts them back. Type {count} to edit"
        ),
        "edit.undo_hint": "Put the originals back: tgmirror undo O{id}",
        "edit.plan_mirror": (
            "Only the copies the pair '{src}' -> '{dst}' (run R{run}) posted are edited: "
            "{copies} copies."
        ),
        "undo.type_count": (
            "This puts the original text back on {count} messages in '{chat}'. Type {count} to "
            "do it"
        ),
        "undo.plan_header": (
            "Undo plan O{id} (reverses edit O{edit}) in '{chat}': {messages} messages."
        ),
        "undo.not_an_edit": "O{id} is not an edit: only `edit` can be undone.",
        "undo.never_applied": "Edit O{id} was never applied (it is only a plan, or was dropped).",
        "undo.already": "Edit O{id} was already undone by O{undo}.",
        "undo.nothing": "Edit O{id} changed no message, so there is nothing to put back.",
        "undo.no_edit": "There is no edit to undo yet.",
        "op.wrong_kind": (
            "O{id} is a '{kind}' operation, not '{expected}': use `tgmirror {kind} --resume O{id}`."
        ),
        "history.op_kind_edit": "edit messages",
        "history.op_kind_undo": "put the originals back",
        "history.line_undo_of": "Undoes:      edit O{id}",
        "history.failed_items": (
            "Messages Telegram would not let us edit ({count}); the reasons are its answers:"
        ),
        "history.failed_item": "  id {id}: {reason}",
        "history.line_counts_edit": (
            "Result:      {done} edited, {skipped} left alone, {gone} already gone, {failed} "
            "failed (plan of {planned} messages)"
        ),
        "history.line_counts_undo": (
            "Result:      {done} put back, {skipped} left alone, {gone} already gone, "
            "{failed} failed (plan of {planned} messages)"
        ),
    }
)

VI.update(_op_texts(_OP_VI, "repost", "đăng lại", "tgmirror repost"))
EN.update(_op_texts(_OP_EN, "repost", "repost", "tgmirror repost"))
VI.update(
    {
        "repost.no_admin_channels": (
            "Bạn không quản trị kênh/nhóm nào, nên không có gì để đăng lại."
        ),
        "repost.pick_source": "Đăng lại tin trong kênh/nhóm nào?",
        "repost.needs_filter": (
            "Bộ lọc trống sẽ đăng lại TẤT CẢ tin của kênh. Nếu đúng ý bạn, thêm --all; nếu không, "
            "thêm bộ lọc (--media, --hashtag, --since, --selection, ...)."
        ),
        "repost.confirm_all": (
            "Không có bộ lọc: kế hoạch sẽ gồm TẤT CẢ tin của «{chat}». Vẫn lập kế hoạch?"
        ),
        "repost.nothing": "Bộ lọc không chọn tin nào trong «{chat}»: không có gì để đăng lại.",
        "repost.plan_header": (
            "Kế hoạch đăng lại O{id} trong «{chat}»: {messages} tin ({units} đơn vị, "
            "{albums} album)."
        ),
        "repost.plan_how": (
            "  Mỗi đơn vị được TẢI XUỐNG rồi TẢI LÊN LẠI ở cuối kênh thành tin mới có file id mới "
            "({notify}). Tin mới mất lượt xem, reaction, bình luận và ngày gốc; link hoặc reply "
            "trỏ tới id cũ không còn đúng."
        ),
        "repost.plan_silent": "im lặng, không thông báo cho người theo dõi",
        "repost.plan_notify": "CÓ thông báo cho người theo dõi",
        "repost.plan_delete": "  Sau khi bản mới có, bản gốc bị XOÁ (không hoàn tác được).",
        "repost.plan_keep": "  Bản gốc được giữ nguyên.",
        "repost.plan_left": (
            "  Poll, game, hoá đơn không đăng lại được: để nguyên (và không bao giờ bị xoá)."
        ),
        "repost.type_count": (
            "Việc này tải xuống và đăng lại {count} tin trong «{chat}», xong thì {after}. "
            "Gõ {count} để làm"
        ),
        "repost.after_delete": "xoá bản gốc (không hoàn tác được)",
        "repost.after_keep": "giữ bản gốc",
        "repost.same_file": (
            "{count} tin mới vẫn mang file id cũ: Telegram dùng lại file có nội dung trùng."
        ),
        "repost.same_file_result": (
            "{count} tin mới vẫn mang file id cũ (Telegram dùng lại file có nội dung trùng)."
        ),
        "repost.settled_repost": (
            "Lần trước dừng giữa chừng: {copied} tin đã có bản đăng lại, {again} tin sẽ đăng lại, "
            "{unsure} tin không chắc nên để nguyên."
        ),
        "repost.unsupported": (
            "{count} tin (poll, game, hoá đơn, ...) không đăng lại được nên được để nguyên."
        ),
        "history.op_kind_repost": "đăng lại tin",
        "history.line_counts_repost": (
            "Kết quả:     {done} đã đăng lại, {skipped} để nguyên, {gone} đã mất sẵn, {failed} lỗi "
            "(kế hoạch {planned} tin)"
        ),
    }
)
EN.update(
    {
        "repost.no_admin_channels": (
            "You do not administer any channel or group, so there is nothing to repost."
        ),
        "repost.pick_source": "Repost messages in which channel or group?",
        "repost.needs_filter": (
            "An empty filter would repost EVERY message of the chat. If that is what you mean, "
            "add --all; otherwise add a filter (--media, --hashtag, --since, --selection, ...)."
        ),
        "repost.confirm_all": (
            "No filter: the plan will hold EVERY message of '{chat}'. Plan it anyway?"
        ),
        "repost.nothing": "The filter selects no message in '{chat}': nothing to repost.",
        "repost.plan_header": (
            "Repost plan O{id} in '{chat}': {messages} messages ({units} units, {albums} albums)."
        ),
        "repost.plan_how": (
            "  Each unit is DOWNLOADED and UPLOADED AGAIN at the end of the chat as a new message "
            "with a new file id ({notify}). The new messages lose views, reactions, comments and "
            "the original date; links and replies to the old ids no longer point at them."
        ),
        "repost.plan_silent": "silent, the chat's followers are not notified",
        "repost.plan_notify": "the chat's followers ARE notified",
        "repost.plan_delete": (
            "  Once the new one exists, the original is DELETED (this cannot be undone)."
        ),
        "repost.plan_keep": "  The originals are kept.",
        "repost.plan_left": (
            "  Polls, games and invoices cannot be posted again: they are left alone (and never "
            "deleted)."
        ),
        "repost.type_count": (
            "This downloads and posts again {count} messages in '{chat}', then {after}. "
            "Type {count} to do it"
        ),
        "repost.after_delete": "deletes the originals (this cannot be undone)",
        "repost.after_keep": "keeps the originals",
        "repost.same_file": (
            "{count} new messages still carry the old file id: Telegram reuses a file whose "
            "content is the same."
        ),
        "repost.same_file_result": (
            "{count} new messages still carry the old file id (Telegram reuses a file whose "
            "content is the same)."
        ),
        "repost.settled_repost": (
            "The last go stopped midway: {copied} messages already have their copy, {again} will "
            "be posted again, {unsure} were unclear and are left alone."
        ),
        "repost.unsupported": (
            "{count} messages (polls, games, invoices, ...) cannot be posted again and were left "
            "alone."
        ),
        "history.op_kind_repost": "repost messages",
        "history.line_counts_repost": (
            "Result:      {done} reposted, {skipped} left alone, {gone} already gone, {failed} "
            "failed (plan of {planned} messages)"
        ),
    }
)


VI.update(
    {
        "sync.nothing_asked": "Tắt cả --edits lẫn --deletes thì không còn gì để đồng bộ.",
        "sync.plan_header": (
            "Đồng bộ thay đổi của cặp «{src}» → «{dst}» (lần chạy R{run}): đã xét {copies} bản sao."
        ),
        "sync.plan_edits": (
            "Sửa O{id}: {count} bản sao có chữ khác với nguồn bây giờ (nguồn đã sửa). Giữ được "
            "bản gốc: `tgmirror undo O{id}`."
        ),
        "sync.plan_deletes": (
            "Xoá O{id}: {count} bản sao có tin nguồn đã bị xoá (id dưới đây là id ở đích). Xoá "
            "bản sao có thể là xoá bản duy nhất còn lại của tin đó, và không hoàn tác được."
        ),
        "sync.plan_left": (
            "  để nguyên: {in_sync} bản sao đã khớp nguồn, {lossy} có định dạng không viết lại "
            "nguyên vẹn được (không bao giờ sửa), {missing} bản sao đã mất ở đích; {already} tin "
            "nguồn đã xoá mà bản sao cũng đã mất."
        ),
        "sync.plan_many_gone": (
            "  Chú ý: {percent}% bản sao của cặp sắp bị xoá vì không còn thấy tin nguồn. Nếu tài "
            "khoản không còn xem được lịch sử của nguồn thì đó không phải tin bị xoá — xem kỹ mẫu "
            "rồi mới xác nhận."
        ),
        "sync.edits_off_topic_hashtag": (
            "Cặp này thêm hashtag topic vào bản sao nên không tính lại được chữ của chúng: phần "
            "sửa bị bỏ qua (phần xoá vẫn xét)."
        ),
        "sync.nothing": (
            "Không có gì để đồng bộ vào «{dst}»: {copies} bản sao đã khớp nguồn ({in_sync} tin "
            "đã sửa nhưng bản sao đã đúng, {lossy} có định dạng không viết lại nguyên vẹn được, "
            "{missing} bản sao đã mất, {already} tin nguồn đã xoá mà bản sao cũng đã mất)."
        ),
        "sync.type_count": (
            "Việc này sửa {edits} bản sao và XOÁ {deletes} bản sao trong «{chat}» (phần xoá không "
            "hoàn tác được). Gõ {total} để làm"
        ),
        "sync.needs_expect": (
            "--yes cần đi cùng --expect N: kế hoạch có {count} thay đổi (sửa cộng xoá). Chưa đổi "
            "gì; kế hoạch đã bỏ."
        ),
        "sync.expect_mismatch": (
            "Kế hoạch có {count} thay đổi, không phải {expected}: chưa đổi gì, kế hoạch đã bỏ."
        ),
        "sync.needs_yes": (
            "Chưa đổi gì: cần xác nhận {count} thay đổi (kế hoạch {ids} đã lưu). Làm tiếp bằng: "
            "{commands} — hoặc chạy lại lệnh này với --yes --expect {count}."
        ),
        "sync.cancelled": "Đã bỏ kế hoạch {ids}; chưa đổi gì.",
        "sync.edits_unfinished": (
            "Phần sửa O{id} chưa xong ({status}), nên phần xoá chưa bắt đầu. Làm tiếp: {commands}."
        ),
        "history.line_sync": "Đồng bộ:     theo nguồn {src}, lần chạy R{run}",
    }
)
EN.update(
    {
        "sync.nothing_asked": "With both --edits and --deletes off there is nothing left to sync.",
        "sync.plan_header": (
            "Syncing the changes of the pair '{src}' -> '{dst}' (run R{run}): looked at {copies} "
            "copies."
        ),
        "sync.plan_edits": (
            "Edit O{id}: {count} copies read differently from their source now (it was edited). "
            "The originals are kept: `tgmirror undo O{id}`."
        ),
        "sync.plan_deletes": (
            "Delete O{id}: {count} copies whose source message was deleted (the ids below are the "
            "destination's). Deleting a copy can mean deleting the last one left of that message, "
            "and it cannot be undone."
        ),
        "sync.plan_left": (
            "  left as they are: {in_sync} copies already match their source, {lossy} have "
            "formatting that cannot be written back exactly (never edited), {missing} copies are "
            "gone from the destination; {already} deleted source messages whose copy is gone too."
        ),
        "sync.plan_many_gone": (
            "  Careful: {percent}% of the pair's copies would be deleted because their source "
            "message is not found. If the account can no longer see the source's history that "
            "does not mean they were deleted — look closely at the sample before confirming."
        ),
        "sync.edits_off_topic_hashtag": (
            "This pair adds a topic hashtag to its copies, so their text cannot be worked out "
            "again: the edits are skipped (the deletes are still looked at)."
        ),
        "sync.nothing": (
            "Nothing to sync into '{dst}': {copies} copies already match their source ({in_sync} "
            "edited ones whose copy is right, {lossy} with formatting that cannot be written "
            "back exactly, {missing} copies gone, {already} deleted source messages whose copy is "
            "gone too)."
        ),
        "sync.type_count": (
            "This edits {edits} copies and DELETES {deletes} copies in '{chat}' (the deletes "
            "cannot be undone). Type {total} to do it"
        ),
        "sync.needs_expect": (
            "--yes needs --expect N: the plan has {count} changes (edits plus deletes). Nothing "
            "changed; the plan is dropped."
        ),
        "sync.expect_mismatch": (
            "The plan has {count} changes, not {expected}: nothing changed, the plan is dropped."
        ),
        "sync.needs_yes": (
            "Nothing changed: {count} changes need confirming (plan {ids} is saved). Carry on "
            "with: {commands} — or run this command again with --yes --expect {count}."
        ),
        "sync.cancelled": "Plan {ids} dropped; nothing changed.",
        "sync.edits_unfinished": (
            "Edit O{id} did not finish ({status}), so the deletes have not started. Carry on "
            "with: {commands}."
        ),
        "history.line_sync": "Synced:      from source {src}, run R{run}",
    }
)


def t(key: str, env: Mapping[str, str] | None = None, **params: object) -> str:
    """Look up ``key`` in the active language, falling back to English, then format it."""
    lang = (os.environ if env is None else env).get(ENV_LANG, "vi").lower()
    table = EN if lang == "en" else VI
    template = table.get(key) or EN[key]
    return template.format(**params) if params else template
