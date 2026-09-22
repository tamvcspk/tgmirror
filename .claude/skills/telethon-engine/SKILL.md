---
name: telethon-engine
description: How tgmirror talks to Telegram through Telethon — client setup, login, error mapping, listing joined channels, creating a destination channel, iterating source messages, server-side copy (forward with drop_author), album handling, and the reupload fallback. Use when writing or changing anything in core/telethon_gateway.py, engine/copy.py or engine/reupload.py, or when a Telethon API detail is in question.
---

# Telethon engine rules

Read `docs/01-kien-truc.md` first. Everything here lives behind the `TelegramGateway` protocol; engine code must not import Telethon.

## Verify before relying

Telethon's API drifts between versions. Before using any call below for the first time in a session, check the installed version (`uv pip show telethon`) and read the signature (`python -c "import inspect, telethon; print(inspect.signature(telethon.TelegramClient.forward_messages))"`). Items in `docs/06-lo-trinh.md` ("Việc cần xác minh sớm") are known unknowns — resolve them with a spike and record the answer in that doc.

## Client setup

```python
client = TelegramClient(
    str(session_path), api_id, api_hash,
    flood_sleep_threshold=0,     # D6: never sleep silently; our limiter decides
    request_retries=5, connection_retries=5, retry_delay=1, auto_reconnect=True,
)
```

- `make_client` in `core/telethon_gateway.py` is the only place that builds a `TelegramClient` (a test enforces this and the `flood_sleep_threshold=0`); it is also the only module allowed to import Telethon (another test).
- Install `cryptg` (faster AES); `tgmirror doctor` warns if missing.
- Session files are secrets. Never log `api_hash`, phone numbers, codes or session strings.
- One process per session file.

## Listing channels (wizard step 1/2)

```python
async for d in client.iter_dialogs():
    e = d.entity
    if d.is_channel or d.is_group:
        ...   # ChannelInfo(id=d.id, title=d.name, kind=..., noforwards=bool(getattr(e, "noforwards", False)), ...)
```

`kind`: `broadcast` (`Channel.broadcast`), `forum` (`Channel.megagroup and Channel.forum`), `supergroup` (other `megagroup`), `group` (`Chat`). All four are valid sources; see `docs/01-kien-truc.md` ("Loại nguồn").

- Rights come from the entity (`channel_info`): `is_admin = creator or admin_rights`; `can_post` = creator / `admin_rights.post_messages` for broadcast, admin or not restricted (`banned_rights`, `default_banned_rights`, honouring `until_date`) for groups. This costs no extra request per dialog; `get_permissions` would cost one each. Whether it matches Telegram for every kind is still unverified on a real account (spike 4).
- Destination candidates: `is_admin and can_post` and same kind as the source (`engine.endpoints.eligible_destinations`). Hide the rest.
- Cache entities; do not call `get_entity` in loops.

## Login

`TelethonAuth` implements `core.auth.TelegramAuth` (`send_code_request` → `sign_in(phone, code)` → `sign_in(password=...)` on `SessionPasswordNeededError`). The flow lives in `core.auth.login` and is tested with `FakeAuth`; `AccountInfo` never carries the phone number. `telethon_session()` connects and always disconnects; it maps a locked session file to `SessionBusy` (one process per session).

## Creating a destination channel

```python
from telethon.tl.functions.channels import CreateChannelRequest
res = await client(CreateChannelRequest(title=title, about=about, broadcast=True))
dst = res.chats[0]
```

Avatar copy (optional): download source photo, `client.upload_file(...)`, then `EditPhotoRequest`. New channels start private; making them public (username) is a separate, optional step and not part of v1.

## Reading the source (ascending order)

```python
async for m in client.iter_messages(peer, min_id=cursor, reverse=True,
                                    wait_time=READ_WAIT):   # + filter=/search=/max_id= from ServerFilter
    ...
```

Implemented in `TelethonGateway.iter_messages`: it resolves the peer with `get_input_entity` (a chat missing from the session cache becomes `NoPermission`, so `tgmirror channels` refreshes it), converts with `src_message`/`media_kind` (which also fills `hashtags` from `MessageEntityHashtag` entities, and `size`/`duration`/`mime` only for real photo/document attachments, `views`), and translates the `ServerFilter`: `media` via `_MEDIA_FILTERS` (kept equal to `filters.pushdown.PUSHABLE_MEDIA` by a test), `search`, `max_id` as `max_id + 1` (Telethon *excludes* its `max_id`), and `since`/`until` by looking up one message with `get_messages(offset_date=..., reverse=True, limit=1)` and turning it into `min_id`/`max_id` with an `ALBUM_MARGIN` margin. Do **not** pass `offset_date` to `iter_messages` together with `search`/`filter`: Telethon sends it as `max_date` and it breaks under `reverse=True`. Telethon's `MessageEmpty` is also a `custom.Message`: exclude it by name.

- `reverse=True` yields oldest → newest (D4). That it composes correctly with `search`/`filter`/`min_id` against real Telegram is still unverified (spike 3, `docs/06-lo-trinh.md`); `--no-pushdown` is the way to compare.
- Set `wait_time` explicitly; read calls are rate-limited too.
- Skip service messages (`m.action is not None`) and empty messages.
- Group consecutive messages sharing `grouped_id` into one `Unit`. An album may arrive at a page boundary — buffer until the `grouped_id` changes.
- Always re-apply the client matcher after server pushdown (`docs/03-filters.md`). A `filter`/`search` read drops album members that do not match themselves: `engine/planner.py` completes the album with one unfiltered read (`complete_albums`).

## Strategy A — server-side copy

```python
sent = await client.forward_messages(dst, ids, from_peer=src, drop_author=True)
```

- `ids` = all message ids of the batch, ascending, **whole albums only**.
- The result is a list aligned with `ids`. After a call that returned normally, a `None` entry means Telegram created nothing for that id (deleted at the source) → `failed('not_copied')`. `MessageIdInvalidError` (every id gone) maps to `PerMessage` and the runner retries unit by unit. Only a cut-off call (`Transient`) leaves the outcome unknown → reconcile (`docs/04-state-checkpoint.md`). Read from the Telethon 1.45 source, still unverified on a real account (`docs/06-lo-trinh.md`).
- `last_message_id(chat)` (`get_messages(limit=1)`) is how the first run of a pair records where the destination stood (`dst_base_id`), and every ordinary run records where the source stood (`src_last_id`, the total `status` measures against).
- `count(src, min_id, filters)` is the run's analysis (`Runner._analyze`, docs/01 "Analyze và tiến độ"): raw `messages.search` requests with `limit=1`. Telegram applies the media filter but **ignores `min_id`/`max_id` when it counts** (spike 10), so the range is counted by *position*: `offset_id_offset` for `offset_id = x` is how many matches have an id `>= x`, and two positions bound the range (no position reported → the whole total, still an upper bound). Date bounds become ids as when reading, without the album margin. Telethon's own `get_messages(limit=0)` is no help: it ignores the range too. The answer is an *upper bound* (service messages counted, client-side filters not subtracted) and is capped by the id span in the runner.
- `get_messages(src, ids)` (`client.get_messages(peer, ids=[...])`, at most 100 ids per call — the caller chunks) reads the messages `retry` must send again: it returns the ones that still exist, ascending; Telethon answers `None`/`MessageEmpty` for a deleted id and `src_message` drops it. Read from the Telethon 1.45 source, unverified on a real account.
- `drop_author` (and `drop_media_captions`) exist in `forward_messages` since the Telethon version we require (`>=1.45`, checked in the spike); no raw `ForwardMessagesRequest` needed for broadcast/supergroup targets.
- Forum targets: `forward_messages` has no topic parameter, so the gateway calls `ForwardMessagesRequest(..., top_msg_id=<dst topic>)` itself (still inside the gateway + limiter). One call = one destination topic; the batcher cuts a batch when the topic changes. Topic mapping rules: `docs/01-kien-truc.md`. Unverified until the phase 8 spike.
- Batch size defaults to 20, hard max 100.
- `CHAT_FORWARDS_RESTRICTED` / `ChatForwardsRestrictedError` → apply decision D3 (below), do not "work around" it.

## noforwards (decision D3)

Before a clone starts (`engine/endpoints.py::plan_endpoints`, then `cli/commands/clone.py::_confirm_protected`): `src.noforwards` true →
- this account is not creator/admin of the source: refuse with a clear message that names `--yes-i-administer-this-channel` (exit 4). With that flag it goes on, with `warn.noforwards_unadministered`: the user says they own the channel through another account and takes full responsibility (decision D3 as changed 2026-09-20; tgmirror cannot check the claim). The prompt does not do it: only the flag.
- user is admin: tell them they can turn off "Restrict saving content" temporarily; `--mode reupload` is allowed only after the user's own confirmation: the prompt (default no) or `--yes-i-administer-this-channel`. `--yes` does **not** count, and without a terminal and without the flag it is exit 2. It happens before the destination is created or anything is stored.
- `engine/runs.py::begin_run` re-reads the source before any run that could re-upload (`mode` reupload, or `auto` with `--caption` other than `keep`), so `run`/`retry`, which never go through `plan_endpoints`, cannot copy a source that became protected without the user's statement (`RunOptions.protected_ack`, recorded by `clone`, carried by `run`/`retry`): not an admin → `SourceRestricted`, an admin → `NeedsAcknowledgement`. With the statement the run goes on even if the account has since lost admin rights. Every run that relies on it prints `warn.responsibility` (`cli/commands/run.py::execute`).

Do not add code paths that download/re-send protected content without that statement: no default, no `--yes`, no config key that supplies it silently.

## Strategy B — reupload (phase 6)

Two gateway calls, so the read can run ahead of the write (`engine/reupload.py::Pipeline`):

- `prepare(src, unit, tmp)`: reads the unit's messages again (`get_messages(ids=...)`; a missing or empty one → `PerMessage('gone_from_source')`) and downloads their media with `download_media` into `<id>.part`, renamed to `<id><ext>` when done (a finished file is reused, so a FloodWait retry does not download twice; `download_media` returns the real path, use it). A video, GIF or round video also gets its cover (`<id>.thumb.jpg`, the largest `PhotoSize`/`PhotoCachedSize`; a stripped preview is too small). Returns `Prepared(unit, files, handle)`; `files` are the engine's to delete.
- `send_prepared(dst, prepared, caption_policy)`:
  - a single media message: `send_file(path, caption=..., formatting_entities=..., parse_mode=None, thumb=...)` plus, for a Document, the source's `attributes`, `mime_type` and `supports_streaming`. The attributes are what keep a video a video, a voice note a voice note, a sticker a sticker (Telethon's `get_attributes` merges them over its own guesses). **`force_document=True` only for a plain file** (`media_kind` is `DOCUMENT`): Telethon passes it on as `force_file` in `InputMediaUploadedDocument`, and Telegram then shows the upload as a file whatever the attributes say (a real bug: videos arrived as files). A plain video also gets `nosound_video=True`, or a silent one turns into a GIF. `parse_mode=None` always: the entities are the formatting, and the client's default markdown parser would corrupt a text that contains `**`.
  - an album (2026-09-22, through the pool): each member is uploaded on its own — a big-enough `Document` through `_upload_parallel`, everything else (a real photo, a small file, a cover) through `client.upload_file` — then `messages.UploadMediaRequest` turns the upload into an `InputMediaPhoto`/`Document` (`SendMultiMediaRequest` refuses a bare `InputMediaUploaded*`: `MediaInvalidError`, same as Telethon's own `_send_album`), wrapped in an `InputSingleMedia` (its own caption/entities), and one `messages.SendMultiMediaRequest` posts the batch; ids come back via `UpdateMessageID.random_id`. Attributes, `mime_type`, `force_file` and `nosound_video` are decided **per member** from its own message (`media_kind`), and its own already-downloaded cover (`_Item.thumb`) is attached — no `hachoir`, no one setting forced onto the whole group (the gap the old `send_file([...])` list-send had).
  - text (or a link preview): `send_message(text, formatting_entities=..., parse_mode=None, link_preview=<the original had one>)`.
  - poll, location/venue, contact, dice: `send_message(text, file=message.media)`; Telethon builds the `InputMedia*` itself. Do **not** hand-assemble `InputMediaPoll` (and `PollAnswerSyntax` does not exist). Anything else → `PerMessage('unsupported_media:<type>')`.
  - `send_text(dst, text)` posts a placeholder.
- Caption rewrite (`rewrite_caption`, `strip_source_links`) lives in the gateway module because it works on Telethon entity types; offsets are UTF-16 code units. It applies to captions of messages that have a file only; `append` adds to a caption that exists, never creates one.
- `MediaCaptionTooLong`/`MessageTooLong` map to `PerMessage` (`append` can cause them).
- What cannot be copied is decided by `engine/reupload.py::plan_unit` **before** Telegram is called, from `SrcMessage.media`, `quiz_unanswered` and `title` (set by `src_message`; Telethon raises `TypeError` for an unanswered quiz and for `MessageMediaInvoice`, so they are detected up front): poll/quiz without `--reset-polls` → dropped with a warning (`skipped`, `unsupported:poll|quiz`, never a placeholder); game, invoice, unanswered quiz → `--ignore-unsupported` drops, `--placeholder` posts `[Game: <title> — không thể sao chép]` and keeps its id on the `skipped` row, neither → `UnsupportedMedia` stops the run (exit 2). Never auto-answer a quiz, never prefix sender names.
- **Sending by file id** (`Strategy.REFERENCE`, `--mode auto` with a caption change, source not protected, unit made of files only): `fetch` re-reads the messages for fresh file references (no download), `send_by_reference` calls `send_file(dst, message.media | [media...], caption=..., formatting_entities=..., parse_mode=None)` with no upload arguments. Media Telegram will not reuse (`FILE_REFERENCE_*`, `FILE_ID_INVALID`, `MEDIA_EMPTY`, `MEDIA_INVALID`, `GROUPED_MEDIA_INVALID`) becomes `FileRefExpired`; `send_unit_by_reference` refreshes once, then falls back to download + upload inside the same guarded write. Never used for a protected source (`RunOptions.src_protected`, D3) or under `--mode reupload`. Verified by hand on an unprotected channel (docs/06 spike 11). On a protected source Telegram refuses it (`ChatForwardsRestrictedError` from `SendMultiMediaRequest`, album spike 2026-09-21, single message not tried), which is why it is never chosen there.
- Files: `<data>/tmp/run-<id>/`, emptied when a run starts (leftovers of a killed one) and when it ends. `[limits] prefetch` (default 1) and `tmp_budget_mb` bound how much is on disk (`Window`). `Window` reserves before downloading (`reserve_size`: a file without a size counts 1 MiB, never 0) and the runner books what is really on disk afterwards (`Window.grow`), so downloading can never run past the budget however slow the upload. Sending stays sequential and in order; only the read half looks ahead. `prepare` and `send_prepared` take `on_transfer(phase, msg_id, done, total)` and hand Telethon a `progress_callback` only when someone listens (an album reports real bytes per member, folded into a running total under the first message's id — `TelethonGateway._album_progress`); a callback must never raise into the transfer. **Upload ahead of the post**: `upload_prepared(prepared)` uploads a single pooled file (`InputFileBig`, kept in `_Item.uploaded`, `Prepared.uploaded = True`) without creating a message; `send_prepared` then only posts it (`send_file(handle, ...)`), so a FloodWait on the post repeats the post, not the upload. Anything else (album, small file, pool off, nothing to upload) is returned as it is and `send_prepared` uploads inline as before. **Parallel parts** (`core/pool.py`, `TransferSettings` from `[limits] max_requests|upload_connections|pool_min_mb`, `max_requests = 0` turns it off): a document of `pool_min_mb` or more is downloaded as 1 MiB `GetFileRequest`s with many in flight on the file's data-centre connection (`client._sender`, or `_borrow_exported_sender` for another DC), each part written at its offset (`_FileAt`), on a connection of its own (`_download_sender`, not the main one; a foreign data centre uses Telethon's borrowed one), the parts already written kept in `_partial` so a repeat after a flood or a lost connection resumes; a video/document of 10 MB or more, single or an album member (a real photo always keeps Telethon's `upload_file`), is uploaded as 512 KiB `SaveBigFilePartRequest`s spread over `upload_connections` connections of their own (extras on the same auth key, made lazily by `_new_sender`, closed by `aclose`; the main connection only if none could be made) and posted with the resulting `InputFileBig` and the same attributes as a path. Transport errors (`InvalidBufferError`, closed connection, no answer in `request_timeout`, wrong length) become `TransportPressure`, which halves the budget and repeats the part, except HTTP 429 (`flood=True`) which ends the transfer as `FloodWait(60, transport=True)`; a `FloodWait` ends the transfer for `FloodGuard` to sit out. These use private Telethon calls (`_call`, `_borrow_exported_sender`, `_sender`, `_get_dc`, `_connection`) that may change with a Telethon upgrade. **Telethon is pinned to one version** (`pyproject.toml`, `uv.lock`, `TELETHON_CHECKED`; `tests/unit/test_telethon_pin.py` fails on any other version and on any private member the gateway newly touches). Never bump it casually: follow the checklist in `docs/06-lo-trinh.md`, "Nâng cấp Telethon". **Unverified on a real account**: the upload path (that a video posted from an `InputFileBig` stays a video, thumbnails, streaming) — after any Telethon upgrade, or if a video shows up as a file, set `max_requests = 0` and check.
- **Still unverified on a real account** (`docs/06-lo-trinh.md`, "Phase 6 — ghi chú"): all of the above against real Telegram, in particular that downloading protected media as an admin works, that `force_document=True` + the source attributes yields a video/voice/sticker (not a plain file), an album through the pool (`UploadMediaRequest` + `SendMultiMediaRequest` per member, 2026-09-22, no real run yet), the cover upload, `send_message(file=media)` for polls/locations/contacts, and that `message.chat` carries the username `strip-links` looks for.

## Error mapping

Map Telethon exceptions to tgmirror errors at the gateway boundary (`core/errors.py`): `FloodWait(seconds, slow_mode=)` (SlowModeWait too), `PeerFlood`, `NoPermission`, `ForwardsRestricted`, `FileRefExpired`, `Transient`, `PerMessage(reason)`, plus the login errors and `TooManyChannels`/`SessionBusy`. The engine only sees these. Every gateway method wraps its Telethon calls in `mapped_errors()`; the table is `map_exception`. Add new mappings there with a test built from the real Telethon exception class. Gotcha: for Telethon's generated error classes `exc.message` is only the generic `BAD_REQUEST`; name them by class (`_rpc_name`). See `flood-safety` for how they are handled.

## Testing

Anything new in the gateway needs a `FakeGateway` counterpart and an engine-level test. Only `scripts/smoke.py` (manual, dedicated test channels) talks to real Telegram.

## Doc sync

Before finishing, run the `doc-sync` skill. Typical updates from this area: resolved spikes (remove the matching "verify" caveats here and tick them in `docs/06-lo-trinh.md`), gateway/strategy changes in `docs/01-kien-truc.md`, new or changed error mappings.
