ALTER TABLE inbox ADD COLUMN quoted_message_id TEXT;
ALTER TABLE inbox ADD COLUMN quoted_text TEXT;

CREATE INDEX IF NOT EXISTS idx_outbox_meta_subject
  ON outbox(meta_message_id, subject_id);

CREATE TABLE IF NOT EXISTS outbound_quote_context (
  meta_message_id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  username TEXT NOT NULL,
  event_type TEXT NOT NULL,
  text_body TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  UNIQUE(fingerprint, subject_id, client_id, username)
);

CREATE INDEX IF NOT EXISTS idx_outbound_quote_context_tenant
  ON outbound_quote_context(meta_message_id, subject_id, client_id, username);
