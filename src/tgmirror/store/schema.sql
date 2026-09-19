-- Schema version 1 (docs/04-state-checkpoint.md). Later versions are appended as migrations in
-- db.py; this file is never edited once released.

CREATE TABLE jobs (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  account       TEXT NOT NULL,
  src_id        INTEGER NOT NULL,
  src_title     TEXT,
  src_kind      TEXT NOT NULL,
  dst_id        INTEGER NOT NULL,
  dst_title     TEXT,
  mode          TEXT NOT NULL,
  filters_json  TEXT NOT NULL,
  options_json  TEXT NOT NULL,
  status        TEXT NOT NULL,
  control       TEXT NOT NULL DEFAULT 'none',
  cursor_src_id INTEGER NOT NULL DEFAULT 0,
  resume_at     TEXT,
  fail_reason   TEXT,
  stats_json    TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE msg_map (
  job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  src_msg_id   INTEGER NOT NULL,
  dst_msg_id   INTEGER,
  grouped_id   INTEGER,
  src_topic_id INTEGER,
  status       TEXT NOT NULL,
  reason       TEXT,
  batch_id     INTEGER,
  ts           TEXT NOT NULL,
  PRIMARY KEY (job_id, src_msg_id)
);
CREATE INDEX msg_map_status ON msg_map(job_id, status);

CREATE TABLE topic_map (
  job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  src_topic_id INTEGER NOT NULL,
  dst_topic_id INTEGER NOT NULL,
  title        TEXT,
  PRIMARY KEY (job_id, src_topic_id)
);

CREATE TABLE flood_log (
  id         INTEGER PRIMARY KEY,
  job_id     INTEGER,
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
