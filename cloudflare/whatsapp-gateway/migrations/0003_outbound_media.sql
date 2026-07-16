CREATE TABLE IF NOT EXISTS outbound_media (
  fingerprint TEXT PRIMARY KEY,
  inbound_message_id TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  artifact_type TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  caption TEXT,
  status TEXT NOT NULL,
  meta_media_id TEXT,
  meta_message_id TEXT,
  error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  sent_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_outbound_media_created
  ON outbound_media(created_at);

CREATE INDEX IF NOT EXISTS idx_outbound_media_meta_message
  ON outbound_media(meta_message_id);
