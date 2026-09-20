-- Schema version 1 (docs/04-state-checkpoint.md). Later versions are appended as migrations in
-- db.py; this file is never edited once released. (Before the first release the job-based tables
-- were replaced by runs + mirrors directly, without a migration.)

-- The checkpoint of a source/destination pair: what makes the next run a delta and a crash
-- recoverable. Never shown to the user.
CREATE TABLE mirrors (
  id            INTEGER PRIMARY KEY,
  account       TEXT NOT NULL,
  src_id        INTEGER NOT NULL,
  src_title     TEXT,
  src_kind      TEXT NOT NULL,
  dst_id        INTEGER NOT NULL,
  dst_title     TEXT,
  mode          TEXT NOT NULL,
  filters_json  TEXT NOT NULL,
  options_json  TEXT NOT NULL,
  cursor_src_id INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX mirrors_pair ON mirrors(src_id, dst_id);

-- The log: one row per execution of `clone` / `run`.
CREATE TABLE runs (
  id           INTEGER PRIMARY KEY,
  mirror_id    INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,
  mode         TEXT NOT NULL,
  filters_json TEXT NOT NULL,
  options_json TEXT NOT NULL,
  status       TEXT NOT NULL,
  control      TEXT NOT NULL DEFAULT 'none',
  cursor_from  INTEGER NOT NULL DEFAULT 0,
  cursor_to    INTEGER NOT NULL DEFAULT 0,
  resume_at    TEXT,
  fail_reason  TEXT,
  stats_json   TEXT NOT NULL DEFAULT '{}',
  started_at   TEXT NOT NULL,
  ended_at     TEXT,
  updated_at   TEXT NOT NULL
);
CREATE INDEX runs_mirror ON runs(mirror_id, id);

CREATE TABLE msg_map (
  mirror_id    INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,
  src_msg_id   INTEGER NOT NULL,
  dst_msg_id   INTEGER,
  grouped_id   INTEGER,
  src_topic_id INTEGER,
  status       TEXT NOT NULL,
  reason       TEXT,
  batch_id     INTEGER,
  run_id       INTEGER,
  ts           TEXT NOT NULL,
  PRIMARY KEY (mirror_id, src_msg_id)
);
CREATE INDEX msg_map_status ON msg_map(mirror_id, status);
CREATE INDEX msg_map_run ON msg_map(run_id, status);

CREATE TABLE topic_map (
  mirror_id    INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,
  src_topic_id INTEGER NOT NULL,
  dst_topic_id INTEGER NOT NULL,
  title        TEXT,
  PRIMARY KEY (mirror_id, src_topic_id)
);

CREATE TABLE flood_log (
  id         INTEGER PRIMARY KEY,
  run_id     INTEGER,
  ts         TEXT NOT NULL,
  kind       TEXT NOT NULL,
  seconds    INTEGER,
  method     TEXT,
  delay_ms   INTEGER,
  batch_size INTEGER
);

CREATE TABLE limiter_state (
  account    TEXT PRIMARY KEY,
  delay_ms   INTEGER NOT NULL,
  day        TEXT NOT NULL,
  sent_today INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
