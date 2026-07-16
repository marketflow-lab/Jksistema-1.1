CREATE TABLE IF NOT EXISTS message_progress (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  stage TEXT NOT NULL,
  text_body TEXT NOT NULL,
  status TEXT NOT NULL,
  outbox_id TEXT,
  error TEXT,
  created_at INTEGER NOT NULL,
  sent_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_message_progress_message ON message_progress(message_id, sequence);
CREATE INDEX IF NOT EXISTS idx_message_progress_created ON message_progress(created_at);
