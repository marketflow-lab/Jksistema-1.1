CREATE TABLE IF NOT EXISTS bridge_heartbeats (
  machine_id TEXT PRIMARY KEY,
  client_id TEXT,
  username TEXT,
  app_version TEXT,
  status TEXT NOT NULL DEFAULT 'online',
  last_seen_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bridge_heartbeats_seen
  ON bridge_heartbeats(last_seen_at);

ALTER TABLE inbox ADD COLUMN offline_notified_at INTEGER;
ALTER TABLE outbox ADD COLUMN next_attempt_at INTEGER;

CREATE INDEX IF NOT EXISTS idx_outbox_retry_due
  ON outbox(status, next_attempt_at, created_at);
