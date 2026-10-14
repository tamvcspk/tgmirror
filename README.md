# tgmirror

CLI tool that clones a Telegram channel, group or forum you have joined into another one (existing or newly created; forum topics are mapped topic to topic), using your own Telegram API credentials (MTProto, via [Telethon](https://github.com/LonamiWebs/Telethon)).

> Status: **`v0.1.1` released** (2026-09-28: PyPI, Docker Hub `tamvo1808/tgmirror`, GitHub Release with a Windows PyInstaller zip; winget submission pending review). Phases 0-13, 15 and 16 are done (see `docs/06-lo-trinh.md`): server-side copy and download-then-reupload, filters with server-side narrowing, the rate limiter (AIMD delay, FloodWait auto-wait, daily cap), `retry`/`status`, group/supergroup/forum sources with topic mapping, OS keyring for `api_id`/`api_hash`, `tgmirror appdata export`/`import`, `tgmirror backup`/`restore` (restore confirmed against real Telegram), a Docker image, and a full-screen keyboard-driven app when you type bare `tgmirror`. Still **not run end to end with a real account**: a real multi-topic forum, a real keyring, a real cross-machine export/import (see the roadmap for exactly what has and has not been tried live). Next, toward 0.2.0 (unreleased): `inspect`/report with reusable selections is implemented on `main` and was tried on a real account (reply/forward/reactions/duplicates, a 13,000-message scan in 2 min 22 s), transforms (caption operations, and routing messages into forum topics by hashtag) are implemented on `main` too — caption operations were tried on a real account, topic routing not yet; then 0.3.0 (bulk delete/edit/repost in place, always behind a confirmed plan) — see `docs/07-inspect-transform.md`. `clone` copies at once, Ctrl+C or `docker stop` stops it and saves progress; running the same pair again is a **delta** (only new messages). `tgmirror doctor` checks the session, `cryptg`, destination permissions and prints the safety notes below. See [docs/](docs/) for the design and [.claude/skills/](.claude/skills/) for the project skills.

## Goals

- Pick a source (channel, supergroup/group or forum) from what you joined, pick a destination (existing or create new).
- One account, and everything is run by hand as an ordinary command in your terminal (`clone` copies at once, Ctrl+C stops it): no background daemon, no schedule, no jobs to manage.
- Filter what gets cloned: media types, hashtags, regex, date/ID range, size, ...
- Pause / stop / resume with checkpoints (keys `p` / `r` / `q`), then run the same clone again for a **delta** that only copies new messages.
- Fast (server-side copy, batching) and gentle (adaptive rate limiting, FLOOD_WAIT handling, anti-spam hygiene).
- Back up a source to a directory on disk (`backup`) and rebuild it on a channel later (`restore`) — same filters, pause/stop/resume and "Restrict saving content" handling as a clone.
- Change what is copied on the way: caption operations (strip links, remove hashtags, replace, append) and, for a forum destination, routing messages into topics by hashtag — from a YAML file or plain flags (`--transform`, `--hashtag-to-topic`, ...).
- See what a source holds and what a filter selects before copying (`inspect`: counts by media, month, hashtag, forward origin, link domain, ...; a pre-flight of what a clone/backup/restore would do), and reuse that selection for the real command (`--selection @last`, `--save`/`--freeze`, `--exact`). Read-only: it never writes to Telegram.

## Install

A tagged release (`v*`) has CI publish to PyPI over Trusted Publishing (OIDC) — `uv tool install tgmirror`/`pipx install tgmirror`, once that has actually run once against a real tag (the workflow exists but hasn't yet: see `docs/06-lo-trinh.md`). Until then, install straight from the source:

```bash
uv tool install git+https://github.com/tamvcspk/tgmirror.git   # latest main, straight from GitHub
uv tool install .                                               # from a local checkout (this repo)
pipx install .                                                  # pipx works the same way
```

Any of these put a `tgmirror` command on your PATH, in its own isolated environment (`uv tool`/`pipx` both manage that for you — no manual venv needed). Run `tgmirror doctor` afterwards to check the session, `cryptg`, and destination permissions.

### Docker

A tagged release (`v*`) has CI push the image to `docker.io/tamvo1808/tgmirror:<version>` (and `:latest` for a non-prerelease version) — `docker pull tamvo1808/tgmirror`. That hasn't happened for a real tag yet (the workflow exists but hasn't been run against one: see `docs/06-lo-trinh.md`), so build it locally in the meantime:

```bash
docker build -t tgmirror .

# first run: log in interactively, session goes on the /data volume
docker run -it --rm -v tgm-data:/data -v tgm-config:/config tgmirror login

# every run after that: no -it needed, everything is flags
docker run --rm -v tgm-data:/data -v tgm-config:/config tgmirror run --yes
```

- `-v tgm-data:/data -v tgm-config:/config` are required: the session, database and config live there, and `run`/`retry` depend on the session's cached channel entities surviving between runs. Use a bind-mounted host directory instead of named volumes if you want to look at the files; a bind mount keeps the host's ownership, so `chown -R 1000:1000` it (or pass `--user "$(id -u):$(id -g)"`) so the non-root `tgmirror` user inside the container can write to it.
- Credentials: `-e TGMIRROR_API_ID=... -e TGMIRROR_API_HASH=...`, or `-e TGMIRROR_API_ID_FILE=/run/secrets/api_id` pointing at a Docker/Kubernetes secret mount (the value never shows up in `docker inspect`). The image disables the OS keyring (`PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`) since a container has none to use.
- One `docker run` is one `tgmirror` command that copies once and exits — no daemon inside the image, matching the CLI itself; schedule repeat runs with your own cron/Kubernetes CronJob if you want that.
- `docker stop` sends SIGTERM, which the container handles exactly like the first Ctrl+C (finishes the current batch, saves, exits) for `clone`/`run`/`retry`/`backup`/`restore`. Its default 10s grace period before `SIGKILL` can be too short for a batch plus a possible FloodWait; raise it with `docker stop -t 60 <container>` (or `stop_grace_period: 60s` in Compose). A `SIGKILL` mid-batch is still safe — the same write-ahead/reconcile that protects a killed local process covers it.
- `daily_cap` is counted by the container's calendar day; the image defaults `TZ=UTC`, override with `-e TZ=...` if you want the cap's midnight to match your own.

## Usage

**Naming a channel** (`<src>` of `inspect`/`topics`/`backup`, and `--src`/`--dst` of `clone`): the chat must be one your account joined. Any of these works:

- `"@username"` — keep the quotes in PowerShell (it swallows an unquoted `@name`), or write the bare username `my_channel`;
- the id, with or without the `-100` prefix: `-1002428805566`, `1002428805566` or `2428805566` (`tgmirror channels` lists them);
- the exact title, any case: `"My channel"` (it must be unique).

An id that starts with `-` is read as an option when it is a positional argument (`inspect`, `topics`, `backup`): drop the minus (`tgmirror inspect 1002428805566`) or put `--` before it, after the other options (`tgmirror inspect --by topic -- -1002428805566`). `--src -1002428805566` on `clone` is fine as it is. Links such as `https://t.me/...` are not accepted yet.

Available now:

```bash
tgmirror                      # a real terminal: full-screen, keyboard-driven app (see "Full-screen menu" below)
tgmirror login                # api_id / api_hash (from my.telegram.org/apps) + account login
tgmirror whoami | logout
tgmirror channels [--search TEXT] [--writable] [--json]
tgmirror topics "@my_forum" [--json]   # a forum's topics (id, title, closed), for --topic or the wizard
tgmirror clone                # wizard: pick source, pick or create destination, filter, mode, one question, then it copies
tgmirror clone --src "@my_channel" --dst-new "My channel (copy)" --yes   # keep the quotes in PowerShell
tgmirror clone --src "@my_channel" --dst-new "Videos" --media video --hashtag "#news" --since 2024-01-01 --yes
tgmirror clone --src "@my_channel" --dst "Copy" --filter-file filters.yaml --preview   # YAML: include/exclude/date/id/album
tgmirror clone --src "@my_channel" --dst "Copy" --yes   # the same pair again: only what is new, same filter
tgmirror clone --src "@my_channel" --dst "Copy" --media photo --yes   # another filter: read again, nothing copied twice
tgmirror clone --src "@my_channel" --dst "Copy" --fresh   # start the pair over: forget what it copied, copy everything again (asks first)
tgmirror clone --src "@protected_channel" --dst "Copy" --mode reupload --yes-i-administer-this-channel   # "Restrict saving content" source: your own statement, your own responsibility (see "Responsible use")
tgmirror clone --src "@my_channel" --dst-new "Sorted" --hashtag-to-topic --yes   # a new forum: each message into a topic named after its first hashtag
tgmirror clone --src "@my_channel" --dst "Copy" --transform t.yaml --yes   # caption operations / topic rules from a YAML file (see "YAML files")
tgmirror run [n]              # the latest run's clone again (or run n of `history`); Ctrl+C stops it, saving progress
tgmirror run --fresh [--yes]   # the same, for the latest run's pair
tgmirror run --wait           # sit out FloodWaits of any length (default: end the run after [limits] max_auto_wait)
tgmirror pause | stop         # from another terminal: pause in place / stop after the current batch
tgmirror history [n] [--json] # what earlier runs did (n: one run in detail, with failed messages and why)
tgmirror retry [n]            # send again the messages run n failed to copy (default: the latest run); a run of its own
tgmirror status [--json]      # progress, speed, ETA, failures and Telegram's limits of the running clone (works from a second terminal)
tgmirror config get [KEY] [--json]   # paths + every [limits] key, or one of them
tgmirror config set KEY VALUE        # change one [limits] key (validated before it is written)
tgmirror doctor                      # session, cryptg, destination permissions, safety notes
tgmirror appdata export tgmirror-backup.zip        # snapshot tgmirror.db + config.toml (no secrets) into a zip
tgmirror appdata import tgmirror-backup.zip [--yes] # restore it on another machine; existing data is moved aside, not deleted; log in again after
tgmirror inspect "@my_channel" --media video --since 2024-01-01 --by month  # what the source holds and what the filter selects (read-only; or a backup directory, offline)
tgmirror inspect "@my_channel" --media video --plan clone --caption strip-links  # pre-flight: how its units would travel, how much moves, how long the limiter keeps it
tgmirror clone --src "@my_channel" --dst-new "Videos" --selection @last --yes  # copy exactly the filter the last inspect of that source used
tgmirror backup "@my_channel" ./backups/my_channel  # save a source to a directory on disk (or `tgmirror backup` for the wizard); run it again for the same directory to get only what is new
tgmirror restore ./backups/my_channel --dst-new --yes  # rebuild a channel from a backup directory (or `tgmirror restore` for the wizard); always re-uploads, since there is no server-side copy from disk
```

While a clone runs in a terminal: `p` pause (it holds until resumed), `r` resume, `q` stop; Ctrl+C also stops (exit 130). Everything runs in your terminal: no background process and no schedule.

### Reference: filters, selections and flags

`tgmirror <command> --help` lists every flag of a command; this is the lookup table. The full semantics are in [docs/03-filters.md](docs/03-filters.md) (filters, selections) and [docs/02-cli-ux.md](docs/02-cli-ux.md) (commands).

#### Filters

The same flags on `clone`, `backup`, `restore` and `inspect` (the wizard asks for the same values). A filter is checked before anything is read or written: a bad one exits with code 2.

| Flag | Keeps | Notes |
|---|---|---|
| `--media video,photo` | these media types | `photo video audio voice document gif sticker video_note poll geo contact game invoice webpage other text`; `text` = text only, `other` = a checklist, story, paid media or giveaway (forwarded as it is, never sent again) |
| `--hashtag "#news"` | messages with this hashtag | repeat for any-of; whole tag, any case |
| `--contains "word"` | text/caption containing it | repeat for any-of; substring, any case |
| `--regex "pat"` | text/caption matching it | case-sensitive (`(?i)` to ignore case); a pattern taking over 0.5 s on a message stops the run with an error naming it |
| `--since 2024-01-01` / `--until 2025-01-01` | a date range | UTC; `--until` day is **not** included |
| `--min-size 10MB` / `--max-size 2GB` | files of that size | the unit is required (`KB`, `MB`, `GB`, 1 KB = 1024 B); a text message has no size, so `--max-size` drops it |
| `--from-user ID` | messages of this sender | group/forum sources; ids only; repeat for any-of |
| `--topic ID` | messages of this forum topic | ids from `tgmirror topics <src>`; `1` is General; repeat for any-of |
| `--forwarded` / `--not-forwarded` | forwarded messages / the rest | |
| `--forwarded-from ID` | forwarded from this channel/user | marked id as `inspect --by forward` prints it (`-100...`); repeat for any-of |
| `--has-link` | messages containing a link | |
| `--link-domain example.com` | links to this domain | subdomains count (`blog.example.com`), look-alikes do not; repeat for any-of |
| `--exclude-regex "pat"` | **leaves out** matches | repeat; each one is its own exclusion |
| `--exclude-media sticker` | **leaves out** these types | |
| `--album any\|all\|first` | how an album is judged | default `any`: one matching member takes the whole album. An album is never split |
| `--filter-file f.yaml` | a YAML filter | the flags above go on top of it and only narrow it; not together with `--selection` |

How they combine: the "keeps" flags describe **one** rule that every message must satisfy together (`--media video --hashtag "#a"` = video **and** `#a`); repeating one flag is any-of; every `--exclude-*` is a separate exclusion that wins. Anything richer goes in YAML:

```yaml
include:                      # rules are OR-ed; the predicates inside one rule are AND-ed
  - {media: [video], hashtag: ["#tutorial"], size: {min: 10MB}}
  - {regex: "(?i)giveaway"}
exclude:
  - {contains: ["ads"]}
date: {from: 2024-01-01, to: 2025-01-01}
id: {from: 1000, to: 50000}
album: any
```

YAML also has predicates with no flag: `mime` (`video/*`), `duration` (`5m`, `1h30m`), `views`, `forwards`, `reactions` (`{min, max}`), `filename` (`"*.pdf"`), `is_reply`, `edited`, `has_caption`. Hashtags need quotes in YAML (`"#news"`: a bare `#` starts a comment). A message lacking what a predicate looks at (the `size` of a text, the `views` of a group post) never matches it, `max` included.

#### YAML files

Three kinds of file, each checked before anything is read or written (a bad one exits with code 2 and names the key). A file is the **base**; the flags of the same command go on top of it, so the command line shows what was changed without opening the file.

| File | Given with | Holds | Flags on top of it |
|---|---|---|---|
| filter | `--filter-file f.yaml` | `include` / `exclude` / `date` / `id` / `album` (see [Filters](#filters)) | filter flags **narrow** it (AND-ed into every rule; a predicate named in both takes the flag's value) |
| selection | `--selection f.yaml` or `@last` | a filter + the source it was made for, optionally the frozen ids and a `transform:` section | filter flags narrow it; `--caption` and the topic flags go on top of its `transform:` |
| transform | `--transform t.yaml` | `caption:` operations and `topics:` routing/renames (see [Transform](#transform-captions-and-topics)) | `--caption`, `--hashtag-to-topic`, `--topic-map`, `--topic-default`, `--strip-routing-hashtag` |

Refused with exit code 2, because there would be two bases: `--filter-file` together with `--selection`, and `--transform` together with a `--selection` that already carries a `transform:`. In every file hashtags need quotes (`"#news"`: a bare `#` starts a YAML comment), and an unknown key is an error, not ignored.

#### Look first, then copy: `inspect` and selections

```bash
tgmirror inspect "@my_channel" --media video --since 2024-01-01   # counts: what the source holds, what the filter selects
tgmirror inspect "@my_channel" --media video --plan clone         # + what a clone of exactly that would do
tgmirror clone --src "@my_channel" --dst-new "Videos" --selection @last --yes   # then copy exactly that
```

| Flag | Meaning |
|---|---|
| `inspect --by media --by month` | tables to show (repeat the flag): `media month year hashtag topic sender forward domain size duplicate`; default `media month hashtag` |
| `inspect --top N` | rows per ranking table, the rest as "other" (`0` = all; month/year/size always show everything) |
| `inspect --list [--limit N]` | also list the selected messages (default 50 shown) |
| `inspect --csv FILE` | **every** selected message, one row each (UTF-8 with BOM, opens in Excel); not the tables |
| `inspect --json` | the report, the plan and every selected message on stdout (progress goes to stderr) |
| `inspect --plan clone\|backup\|restore` | pre-flight: units by way (forward / by file id / download + upload / left out), volume to move, write calls, the limiter's time floor, days at `daily_cap`, what would stop the run. Takes `--mode --caption --caption-text --reset-polls --ignore-unsupported --placeholder --topic-as-hashtag` like the real command; `restore` only for a backup directory |
| `inspect --refresh` | the first `inspect` of a source builds a local index of its messages (`<data dir>/index/<id>.db`); later ones read only what is newer and report from it. `--refresh` reads the whole source again (catches edited/deleted messages, view and reaction counts) |
| `inspect --offline` | report from the index as it is: no account, no Telegram (the source by id, `@username` or exact title, once inspected before) |
| `inspect --no-index` | read the source live and keep nothing |
| `inspect --quick [--topic ID]` | Telegram's own counters (photos, videos, documents, links, ...) for the whole source or one forum topic, in one request: a first look at a big channel. Telegram's classification, a few % off a scan; no filters, no range |
| `inspect --save FILE [--freeze]` | save the filter as a selection file; `--freeze` also writes the ids of the selected messages (`FILE.ids`, one per line, editable) |
| `--selection FILE\|@last` | on `clone`/`backup`/`restore`: use a selection instead of filter flags. `@last` is what `inspect` last made of that source (kept after every complete scan) |
| `--exact` | with `--selection`: take exactly the frozen ids, even if more messages match now |

`inspect` is read-only (it never writes to Telegram or downloads media, so it needs no "Restrict saving content" statement); it reads the **whole** source (about 11 s per 1,000 messages measured) and prints the commands to continue with at the end. A backup directory works as the source (`tgmirror inspect ./backups/my_channel`), offline. A selection names its source: using it on another one is refused.

#### Transform: captions and topics

A transform changes what is copied on the way. It is available on `clone` and `restore` (same code path) and on `inspect --plan clone|restore`, which counts what it would do. `backup` refuses a selection that carries one: a backup saves the source as it is.

```yaml
# t.yaml — every part is optional
version: 1
caption:                              # applied in this order, to the captions of media messages
  - strip_links: source               # links pointing at the source (`all`: every link)
  - remove_mentions: source           # @source_channel (`all`: every @mention)
  - remove_hashtags: ["#ads", "#promo"]   # or: all
  - replace: {regex: "(?i)contact @\\w+", with: ""}   # `with` is literal text, no \1
  - append: "— Channel B"             # after two blank lines
  # - clear: true                     # drop the caption (= --caption none)
  - text_too: true                    # also rewrite plain text messages (default: media captions only)
topics:
  hashtag_to_topic:                   # the destination must be a forum
    map:                              # in priority order: the first rule a message's hashtag matches
      "#python": "Python"
      "#rust": "Rust lang"
    auto: ["#go", "#machine_learning"]   # topic named after the hashtag: "Go", "Machine learning"
    # auto: all                       # or: every hashtag gets its own topic, as it is met ...
    # limit: 30                       #   ... at most 30 new topics per run (then General); 30 is the default
    # skip: ["#ads"]                  #   ... except these
    default: "Other"                  # no rule matched and the message sits in no source topic
    strip: true                       # take the hashtag that routed a message out of its caption
  topic_map:                          # forum source: rename / merge topics (by title, any case, or id)
    "Old chat": "Chat"
    12: "Chat"                        # several into one name = merged
  hashtag_format: "#t_{slug}"         # destination without topics + --topic-as-hashtag: how a topic is written
```

- **A transform never picks messages — a filter does.** `hashtag_to_topic` only says where a message goes; a message no rule matches is still cloned (into `default`, into the topic its source topic maps to, or into General). To take only some hashtags, filter them: `--hashtag "#python" --hashtag "#rust"`. Likewise `topic_map` only renames; to take some topics use `--topic`.
- **Routing:** a message goes to the topic of the first *rule* (file order, not the order in the message) one of its hashtags matches. An album is judged as a whole and never split. Order *inside* each topic is the source order. Destination topics are looked up by title before one is created, so running the pair again — or two sources into one forum — shares them.
- **The destination must be a forum** when routing: `--dst-new` creates one (even from a plain channel); an existing destination that is not a forum is refused before anything is written.
- **Cost:** routing alone is a plain server-side forward into a topic. What changes a caption (`caption:` operations, `strip`) sends those messages again — by file id when the source allows saving content, downloaded and re-uploaded otherwise (then the "Restrict saving content" statement applies, as for `--mode reupload`). So `--mode copy` together with those is an error; routing alone works with it. `inspect --plan` shows how many units go each way and how many land in each topic.
- **A file that does nothing is an error**, not a plain clone: a key left with nothing under it (`hashtag_to_topic:` and no lines below — write `auto: all` for a topic per hashtag), or a `--transform` file with no operation and no rule, exits with code 2.
- **Remembered with the pair:** `run` and `retry` reuse it. Changing the transform later does not rewrite messages already copied.

Without a file, the same things as flags (on `clone`, `restore` and `inspect --plan`):

| Flag | Same as in the file | On top of a file |
|---|---|---|
| `--hashtag-to-topic` | `hashtag_to_topic: {auto: all}` | adds `auto: all` to the file's rules (its `map` names still win) |
| `--topic-map "Old=New"` (repeat) | one `topic_map` entry | added to the file's; wins for the same topic. Left of `=`: a topic title (any case) or, when all digits, its id; split at the first `=` |
| `--topic-default NAME` | `default:` | replaces the file's |
| `--strip-routing-hashtag` | `strip: true` | switches it on |
| `--caption …` / `--caption-text` | `caption:` operations | run **after** the file's operations |

`--topic-default` and `--strip-routing-hashtag` only mean something with routing (`--hashtag-to-topic`, or `hashtag_to_topic:` in the file): without it they are a usage error, not silently ignored. `limit`, `skip` and `hashtag_format` have no flag; put them in the file.

```bash
# a new forum, one topic per first hashtag; the rest into "Other"; the routing hashtag leaves the caption
tgmirror clone --src "@my_channel" --dst-new "Sorted" --hashtag-to-topic --topic-default "Other" --strip-routing-hashtag --yes
# only #python and #rust messages, into the topics the file names
tgmirror clone --src "@my_channel" --dst-new "Sorted" --transform t.yaml --hashtag "#python" --hashtag "#rust" --yes
# forum to forum: rename one topic by title, merge another one (by id) into it
tgmirror clone --src "@my_forum" --dst-new "Mirror" --topic-map "Old chat=Chat" --topic-map "12=Chat" --yes
# look first: units per destination topic and how they travel; nothing is written
tgmirror inspect "@my_channel" --plan clone --hashtag-to-topic --json
```

Forum to forum with a transform file has been run against real Telegram. Not yet confirmed there: whether a forward into a topic lands in the right one when the *source* is not a forum, whether an album forwarded into a topic keeps its group, and what General is called on an account with a localised interface. Try a small channel first.

#### Copy options (`clone`; `backup`/`restore` take the ones that apply)

| Flag | Meaning |
|---|---|
| `--src`, `--dst` \| `--dst-new "Title"` (+ `--about`) | source, existing destination or a new one |
| `--mode auto\|copy\|reupload` | `auto` (default): server-side copy, and what needs a new caption/topic hashtag is sent again by file id; `copy`: forward only; `reupload`: download and upload everything (the only way to copy a "Restrict saving content" source) |
| `--caption keep\|strip-links\|append\|none` (+ `--caption-text`) | what happens to the captions of media messages |
| `--transform FILE` | a YAML transform: caption operations and topic rules (see "Transform") |
| `--hashtag-to-topic`, `--topic-map "Old=New"`, `--topic-default NAME`, `--strip-routing-hashtag` | route messages into forum topics by hashtag / rename or merge topics / topic for the rest / take the routing hashtag out of the caption (see "Transform") |
| `--topic-as-hashtag` / `--no-topic-as-hashtag` | forum source into a destination without topics: keep each topic's name as a hashtag |
| `--reset-polls`, `--ignore-unsupported`, `--placeholder` | with `reupload`: re-create polls (votes are lost) / leave out what cannot be copied / and post a note where it was |
| `--yes-i-administer-this-channel` | your own statement for a "Restrict saving content" source (see "Responsible use"); `--yes` never stands in for it |
| `--no-filter`, `--fresh` | drop the remembered filter / start the pair over (asks first) |
| `--pushdown` / `--no-pushdown` | let Telegram narrow the reading (default), or read everything and filter here (to check nothing is missed) |
| `--preview` / `--no-preview` | sample the first 100 messages against the filter before starting |
| `--batch-size N` | messages per copy call (default from `[limits] batch_size`) |
| `--wait` | sit out a FloodWait of any length (default: stop after `[limits] max_auto_wait`) |
| `--force-takeover` | run although another process seems to hold the pair (only if it is dead) |
| `--yes`, `-y` | skip the one confirmation question |

Exit codes: `0` done (or stopped with `q`/`tgmirror stop`), `1` general error, `2` wrong usage (including a bad filter), `3` stopped by a FloodWait/PeerFlood/daily cap, `4` missing permission (including a source that restricts saving content), `130` Ctrl+C or `docker stop` (progress saved; `inspect` prints what it had read).

### Full-screen menu

Typing bare `tgmirror` on a real terminal opens a full-screen app instead (arrow keys + Enter, Esc to go back) with the same commands as menu items: New clone, Explore, Backup, Restore, Continue / Retry failures (only once a pair exists), Status, History, Joined channels, Account, Config, Quit. Not logged in yet: a shorter menu (Log in, Status, History, Config, Quit). **Explore** shows a source's messages by media, month, hashtag and (Tab) forward origin, domain, topic or sender: Enter keeps a row, `x` leaves it out, Backspace undoes the last step, and `c`/`b` open the clone/backup wizard with that filter already filled in (`l` lists the messages, `s` saves the filter as `@last`, `u` reads what is new, `R` reads everything again). It works on the source's local index, so every step is instant and calls nothing. Ctrl+C during a run started from the menu stops the run **and** exits the app, same as the classic CLI. No terminal attached (redirected, a script, CI): bare `tgmirror` still prints help, as before.

## Development

```bash
uv sync                 # create .venv, install deps + dev tools
uv run pytest           # tests (no network; uses FakeGateway)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run tgmirror --version
```

`.github/workflows/ci.yml` runs `ruff check`, `ruff format --check` and `pytest` on `ubuntu-latest` and `windows-latest` for every push to `main` and every PR. `.github/workflows/release.yml` runs on a pushed `v*` tag: re-runs the checks, builds the wheel/sdist, builds and pushes the Docker image (see "Docker" above), and attaches everything plus `SHA256SUMS` to a GitHub Release.

## Docs

| File | Content |
|---|---|
| [docs/00-tong-quan.md](docs/00-tong-quan.md) | Goals, non-goals, decisions, legal notes |
| [docs/01-kien-truc.md](docs/01-kien-truc.md) | Architecture, clone strategies, runner loop |
| [docs/02-cli-ux.md](docs/02-cli-ux.md) | Commands, wizard flow, config |
| [docs/03-filters.md](docs/03-filters.md) | Filter model and DSL |
| [docs/04-state-checkpoint.md](docs/04-state-checkpoint.md) | SQLite schema (runs and mirrors), checkpoint, delta |
| [docs/05-chong-flood.md](docs/05-chong-flood.md) | Rate limiting, FLOOD_WAIT, anti-spam |
| [docs/06-lo-trinh.md](docs/06-lo-trinh.md) | Roadmap, open questions |
| [docs/07-inspect-transform.md](docs/07-inspect-transform.md) | `inspect`, selections and transforms (design) |

## Responsible use

Only clone content you own or are allowed to copy. tgmirror respects channels with "Restrict saving content" enabled (see decision D3 in the overview). Automating a user account can get it limited by Telegram; the tool reduces that risk but cannot eliminate it.
