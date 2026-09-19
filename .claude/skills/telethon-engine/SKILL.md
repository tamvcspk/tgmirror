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
                                    wait_time=READ_WAIT):   # phase 2: no filter/search yet
    ...
```

Implemented in `TelethonGateway.iter_messages`: it resolves the peer with `get_input_entity` (a chat missing from the session cache becomes `NoPermission`, so `tgmirror channels` refreshes it), converts with `src_message`/`media_kind`, and raises `NotImplementedError` for any non-empty `ServerFilter` until phase 3 (never silently ignore a filter). Telethon's `MessageEmpty` is also a `custom.Message`: exclude it by name.

- `reverse=True` yields oldest → newest (D4). Confirm it composes correctly with `search`/`filter`/`min_id` in a spike; add a regression test using FakeGateway semantics.
- Set `wait_time` explicitly; read calls are rate-limited too.
- Skip service messages (`m.action is not None`) and empty messages.
- Group consecutive messages sharing `grouped_id` into one `Unit`. An album may arrive at a page boundary — buffer until the `grouped_id` changes.
- Always re-apply the client matcher after server pushdown (`docs/03-filters.md`).

## Strategy A — server-side copy

```python
sent = await client.forward_messages(dst, ids, from_peer=src, drop_author=True)
```

- `ids` = all message ids of the batch, ascending, **whole albums only**.
- The result is a list aligned with `ids`. After a call that returned normally, a `None` entry means Telegram created nothing for that id (deleted at the source) → `failed('not_copied')`. `MessageIdInvalidError` (every id gone) maps to `PerMessage` and the runner retries unit by unit. Only a cut-off call (`Transient`) leaves the outcome unknown → reconcile (`docs/04-state-checkpoint.md`). Read from the Telethon 1.45 source, still unverified on a real account (`docs/06-lo-trinh.md`).
- `last_message_id(chat)` (`get_messages(limit=1)`) is how a job records where the destination stood at creation.
- `drop_author` (and `drop_media_captions`) exist in `forward_messages` since the Telethon version we require (`>=1.45`, checked in the spike); no raw `ForwardMessagesRequest` needed for broadcast/supergroup targets.
- Forum targets: `forward_messages` has no topic parameter, so the gateway calls `ForwardMessagesRequest(..., top_msg_id=<dst topic>)` itself (still inside the gateway + limiter). One call = one destination topic; the batcher cuts a batch when the topic changes. Topic mapping rules: `docs/01-kien-truc.md`. Unverified until the phase 8 spike.
- Batch size defaults to 20, hard max 100.
- `CHAT_FORWARDS_RESTRICTED` / `ChatForwardsRestrictedError` → apply decision D3 (below), do not "work around" it.

## noforwards (decision D3)

At job creation: `src.noforwards` true →
- user is not creator/admin of the source: refuse with a clear message. No reupload fallback.
- user is admin: tell them they can turn off "Restrict saving content" temporarily; offer `--mode reupload` only after an explicit confirmation prompt (or `--yes-i-administer-this-channel` non-interactively).

Do not add code paths that download/re-send protected content from channels the user does not administer.

## Strategy B — reupload (phase 6)

- `client.download_media(msg, file=tmp_path)` → `client.send_file(dst, tmp_path, caption=msg.raw_text, formatting_entities=msg.entities, ...)`.
- Preserve video attributes (`duration`, `w`, `h`, `supports_streaming=True`), thumbs when available, voice/video_note flags.
- Albums: `send_file(dst, [paths...], caption=[...])`.
- Always delete temp files in `finally`. Cap disk use (`tmp/` budget in config).
- Upload cost goes through the limiter with `kind="upload"`.
- Special media (decided 2026-09-19, table in `docs/01-kien-truc.md`): poll/quiz, location/venue, contact are re-sent with `client.send_message(dst, file=message.media)` — Telethon builds the `InputMedia*` itself (`utils.get_input_media`); do **not** hand-assemble `InputMediaPoll` (and `PollAnswerSyntax` does not exist). Polls need `--reset-polls` and lose all votes. Telethon raises `TypeError` for an unanswered quiz and for `MessageMediaInvoice`, so detect those (and `MessageMediaGame`) *before* the call and skip with a warning (`msg_map.status='skipped'`, `reason='unsupported:<kind>'`). With `--placeholder`, post a text stub for each skipped item through the gateway/limiter and store its `dst_msg_id` on the `skipped` row. Never auto-answer a quiz, and never prefix sender names.
- Telethon has no built-in parallel upload; a custom multi-connection uploader belongs in `core/uploader.py` and must still respect `upload_concurrency`.

## Error mapping

Map Telethon exceptions to tgmirror errors at the gateway boundary (`core/errors.py`): `FloodWait(seconds)`, `PeerFlood`, `NoPermission`, `ForwardsRestricted`, `FileRefExpired`, `Transient`, `PerMessage(reason)`, plus the login errors and `TooManyChannels`/`SessionBusy`. The engine only sees these. Every gateway method wraps its Telethon calls in `mapped_errors()`; the table is `map_exception`. Add new mappings there with a test built from the real Telethon exception class. Gotcha: for Telethon's generated error classes `exc.message` is only the generic `BAD_REQUEST`; name them by class (`_rpc_name`). See `flood-safety` for how they are handled.

## Testing

Anything new in the gateway needs a `FakeGateway` counterpart and an engine-level test. Only `scripts/smoke.py` (manual, dedicated test channels) talks to real Telegram.

## Doc sync

Before finishing, run the `doc-sync` skill. Typical updates from this area: resolved spikes (remove the matching "verify" caveats here and tick them in `docs/06-lo-trinh.md`), gateway/strategy changes in `docs/01-kien-truc.md`, new or changed error mappings.
