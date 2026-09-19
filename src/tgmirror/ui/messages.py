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
    "new.pick_source": "Chọn kênh/nhóm nguồn (gõ để lọc)",
    "new.pick_destination": "Chọn đích (gõ để lọc)",
    "new.create_new": "+ Tạo kênh mới",
    "new.prompt_title": "Tên kênh mới",
    "new.prompt_about": "Mô tả (có thể để trống)",
    "new.confirm_create": "Tạo kênh «{title}»?",
    "new.no_candidates": "Không có đích có sẵn phù hợp, sẽ tạo mới.",
    "new.src": "Nguồn: {channel}",
    "new.dst": "Đích:  {channel}",
    "new.dst_created": "Đích:  {channel} (vừa tạo)",
    "new.not_saved": "Chưa lưu job: lưu job và chạy clone có từ phase 2.",
    "kind.broadcast": "kênh",
    "kind.supergroup": "supergroup",
    "kind.forum": "forum",
    "kind.group": "nhóm",
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
    "err.bad_api": "Telegram từ chối api_id/api_hash. Kiểm tra lại tại https://my.telegram.org/apps.",
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
}

EN: dict[str, str] = {
    "login.api_intro": "Your api_id and api_hash are needed. Create them at https://my.telegram.org/apps.",
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
    "new.pick_source": "Pick the source channel/group (type to filter)",
    "new.pick_destination": "Pick the destination (type to filter)",
    "new.create_new": "+ Create a new channel",
    "new.prompt_title": "New channel title",
    "new.prompt_about": "Description (may be empty)",
    "new.confirm_create": "Create channel «{title}»?",
    "new.no_candidates": "No suitable existing destination; a new one will be created.",
    "new.src": "Source:      {channel}",
    "new.dst": "Destination: {channel}",
    "new.dst_created": "Destination: {channel} (just created)",
    "new.not_saved": "The job is not saved: saving jobs and running the clone arrive in phase 2.",
    "kind.broadcast": "channel",
    "kind.supergroup": "supergroup",
    "kind.forum": "forum",
    "kind.group": "group",
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
}


def t(key: str, env: Mapping[str, str] | None = None, **params: object) -> str:
    """Look up ``key`` in the active language, falling back to English, then format it."""
    lang = (os.environ if env is None else env).get(ENV_LANG, "vi").lower()
    table = EN if lang == "en" else VI
    template = table.get(key) or EN[key]
    return template.format(**params) if params else template
