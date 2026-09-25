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
    # doctor
    "doctor.title": "tgmirror doctor",
    "doctor.session_missing_credentials": (
        "Session: chưa có api_id/api_hash (chạy `tgmirror login`)."
    ),
    "doctor.session_not_logged_in": "Session: chưa đăng nhập (chạy `tgmirror login`).",
    "doctor.session_error": "Session: lỗi kết nối — {detail}",
    "doctor.session_ok": "Session: hợp lệ, đang đăng nhập {who}.",
    "doctor.cryptg_ok": "cryptg: đã cài (giải mã nhanh hơn).",
    "doctor.cryptg_missing": (
        "cryptg: chưa cài — chạy chậm hơn nhưng vẫn hoạt động (uv sync cài lại)."
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
    "status.running": "đang chạy",
    "status.paused": "tạm dừng",
    "status.stopped": "đã dừng",
    "status.waiting_flood": "chờ flood",
    "status.done": "xong",
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
        "tắt tùy chọn này ở nguồn, hoặc chạy lại với --mode reupload."
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
    "filter.ask_topics": "Chỉ lấy các topic sau (phím cách để chọn; không chọn gì = tất cả)",
    "filter.ask_file": "Đường dẫn file YAML",
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
    "run.cap_days": (
        "Cap {cap} tin/ngày: với {total} tin, cần nghỉ thêm khoảng {days} ngày trước khi xong "
        "(tăng [limits] daily_cap nếu muốn nhanh hơn, xem docs/05-chong-flood.md)."
    ),
    "run.progress_total": (
        "Lần chạy {id}: {handled}/{total} tin (~{percent}%): {done} đã sao chép, "
        "{skipped} bị filter loại, {failed} lỗi."
    ),
    "run.reference_fallback": (
        "Tin {id}: Telegram không cho gửi lại bằng mã file, tải xuống rồi tải lên lại."
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
        "Cap ngày:    còn tối đa {left} tin, cap {cap}/ngày: cần nghỉ thêm khoảng {days} ngày"
    ),
    "status.line_speed": "Tốc độ:      {speed} tin/giây (trung bình từ lúc bắt đầu){eta}",
    "status.line_speed_unknown": "Tốc độ:      chưa đủ dữ liệu",
    "status.eta": ", còn khoảng {eta}",
    "status.line_resume": "Chờ đến:     {at}{note}",
    "status.line_limiter": (
        "Giới hạn:    nghỉ {delay}s giữa các lần gửi; hôm nay đã gửi {sent}/{cap} tin"
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
    "menu.item_resume": "Chạy tiếp",
    "menu.item_retry": "Thử lại tin lỗi",
    "menu.item_status": "Trạng thái",
    "menu.item_history": "Lịch sử",
    "menu.item_channels": "Kênh đã join",
    "menu.item_account": "Tài khoản",
    "menu.item_config": "Cấu hình",
    "menu.item_quit": "Thoát",
    "menu.history_counts": "{done} tin, {failed} lỗi",
    "menu.yes": "Có",
    "menu.no": "Không",
    "menu.confirm_logout": "Đăng xuất {who}?",
    "menu.item_login": "Đăng nhập",
    "menu.footer_text": "Gõ rồi Enter · Esc quay lại",
    "menu.footer_select": "↑↓ chọn · gõ để lọc · Enter chọn · Esc quay lại",
    "menu.footer_check": "↑↓ di chuyển · Space chọn/bỏ · Enter xong · Esc quay lại",
    "menu.footer_working": "Đang làm… · Ctrl+C thoát",
    "menu.working": "Đang làm…",
    "menu.no_match": "(không có mục nào khớp)",
    "menu.config_edit_title": "Sửa {name}",
    "menu.config_prompt_value": "Giá trị mới cho {name}",
    "menu.running_elsewhere": "đang chạy ở nơi khác",
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
    # doctor
    "doctor.title": "tgmirror doctor",
    "doctor.session_missing_credentials": (
        "Session: no api_id/api_hash yet (run `tgmirror login`)."
    ),
    "doctor.session_not_logged_in": "Session: not logged in (run `tgmirror login`).",
    "doctor.session_error": "Session: connection error — {detail}",
    "doctor.session_ok": "Session: valid, logged in as {who}.",
    "doctor.cryptg_ok": "cryptg: installed (faster crypto).",
    "doctor.cryptg_missing": (
        "cryptg: not installed — slower but still works (uv sync to add it)."
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
    "status.running": "running",
    "status.paused": "paused",
    "status.stopped": "stopped",
    "status.waiting_flood": "waiting (flood)",
    "status.done": "done",
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
        "an admin, turn that option off at the source, or run again with --mode reupload."
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
    "filter.ask_topics": "Only these topics (space to select; none selected = all)",
    "filter.ask_file": "Path of the YAML file",
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
    "retry.nothing": "Run {id} has no failed messages to retry.",
    "run.retry_start": ("Run {id}: retrying {count} failed messages of run {of} ({src} → {dst})."),
    "run.progress_retry": "Run {id}: {done} messages copied again, {failed} still failing.",
    "run.retry_hint": "Retry the failed messages with: tgmirror retry {id}",
    "run.analyzed": (
        "Estimate: up to {total} messages to look at (Telegram's count, the filter not subtracted)."
    ),
    "run.cap_days": (
        "Cap of {cap} messages/day: with {total} messages the run has to rest about {days} more "
        "day(s) before it is done (raise [limits] daily_cap for more, see docs/05-chong-flood.md)."
    ),
    "run.progress_total": (
        "Run {id}: {handled}/{total} messages (~{percent}%): {done} copied, "
        "{skipped} left out by the filter, {failed} failed."
    ),
    "run.reference_fallback": (
        "Message {id}: Telegram would not send it again by file id, downloading and uploading it."
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
        "Daily cap:   up to {left} messages left, cap {cap}/day: about {days} more day(s) of rest"
    ),
    "status.line_speed": "Speed:       {speed} messages/s (average since it began){eta}",
    "status.line_speed_unknown": "Speed:       not enough data yet",
    "status.eta": ", about {eta} left",
    "status.line_resume": "Waiting until: {at}{note}",
    "status.line_limiter": (
        "Limits:      {delay}s between sends; {sent}/{cap} messages sent today"
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
    "menu.item_resume": "Continue",
    "menu.item_retry": "Retry failures",
    "menu.item_status": "Status",
    "menu.item_history": "History",
    "menu.item_channels": "Joined channels",
    "menu.item_account": "Account",
    "menu.item_config": "Config",
    "menu.item_quit": "Quit",
    "menu.history_counts": "{done} copied, {failed} failed",
    "menu.yes": "Yes",
    "menu.no": "No",
    "menu.confirm_logout": "Log out {who}?",
    "menu.item_login": "Log in",
    "menu.footer_text": "Type, then Enter - Esc back",
    "menu.footer_select": "up/down select - type to filter - Enter choose - Esc back",
    "menu.footer_check": "up/down move - Space tick - Enter done - Esc back",
    "menu.footer_working": "Working... - Ctrl+C quit",
    "menu.working": "Working...",
    "menu.no_match": "(nothing matches)",
    "menu.config_edit_title": "Edit {name}",
    "menu.config_prompt_value": "New value for {name}",
    "menu.running_elsewhere": "running elsewhere",
}


def t(key: str, env: Mapping[str, str] | None = None, **params: object) -> str:
    """Look up ``key`` in the active language, falling back to English, then format it."""
    lang = (os.environ if env is None else env).get(ENV_LANG, "vi").lower()
    table = EN if lang == "en" else VI
    template = table.get(key) or EN[key]
    return template.format(**params) if params else template
