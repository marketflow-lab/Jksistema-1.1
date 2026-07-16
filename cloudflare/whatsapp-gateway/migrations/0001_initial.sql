PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pairing_codes (
  code_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  username TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER,
  used_by_subject TEXT
);

CREATE TABLE IF NOT EXISTS bindings (
  subject_id TEXT PRIMARY KEY,
  wa_id TEXT,
  phone_number TEXT,
  client_id TEXT NOT NULL,
  username TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  last_inbound_at INTEGER,
  created_at INTEGER NOT NULL,
  revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bindings_machine ON bindings(machine_id, active);

CREATE TABLE IF NOT EXISTS inbox (
  message_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  wa_id TEXT,
  phone_number_id TEXT,
  message_type TEXT NOT NULL,
  text_body TEXT,
  media_id TEXT,
  media_mime TEXT,
  media_size INTEGER NOT NULL DEFAULT 0,
  media_object_key TEXT,
  media_filename TEXT,
  received_at INTEGER NOT NULL,
  status TEXT NOT NULL,
  lease_owner TEXT,
  lease_until INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0,
  task_id TEXT,
  error TEXT,
  completed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_inbox_claim ON inbox(status, received_at);
CREATE INDEX IF NOT EXISTS idx_inbox_subject ON inbox(subject_id, received_at);

CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY,
  inbound_message_id TEXT,
  subject_id TEXT NOT NULL,
  recipient TEXT NOT NULL,
  message_type TEXT NOT NULL DEFAULT 'text',
  text_body TEXT,
  template_name TEXT,
  template_params_json TEXT,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  sent_at INTEGER,
  meta_message_id TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, created_at);
CREATE INDEX IF NOT EXISTS idx_outbox_subject ON outbox(subject_id, created_at);

CREATE TABLE IF NOT EXISTS message_status (
  meta_message_id TEXT NOT NULL,
  status TEXT NOT NULL,
  status_at INTEGER NOT NULL,
  recipient_id TEXT,
  raw_json TEXT,
  PRIMARY KEY(meta_message_id, status, status_at)
);

CREATE TABLE IF NOT EXISTS template_registry (
  name TEXT PRIMARY KEY,
  language TEXT NOT NULL,
  category TEXT NOT NULL,
  status TEXT NOT NULL,
  components_json TEXT,
  last_verified_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_events (
  fingerprint TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT,
  text_body TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  sent_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_proactive_subject ON proactive_events(subject_id, created_at);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  subject_id TEXT,
  detail_json TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);

CREATE TABLE IF NOT EXISTS usage_counters (
  counter_key TEXT PRIMARY KEY,
  counter_value INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
