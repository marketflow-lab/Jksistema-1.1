CREATE TABLE IF NOT EXISTS voice_phone_settings (
  subject_id TEXT PRIMARY KEY,
  allow_voice_calls INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY(subject_id) REFERENCES bindings(subject_id)
);

CREATE TABLE IF NOT EXISTS voice_calls (
  id TEXT PRIMARY KEY,
  openai_call_id TEXT NOT NULL UNIQUE,
  webhook_id TEXT NOT NULL UNIQUE,
  meta_call_id TEXT,
  subject_id TEXT,
  wa_id TEXT,
  client_id TEXT,
  username TEXT,
  machine_id TEXT,
  direction TEXT NOT NULL DEFAULT 'inbound',
  status TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  accepted_at INTEGER,
  connected_at INTEGER,
  ended_at INTEGER,
  duration_seconds INTEGER NOT NULL DEFAULT 0,
  usage_json TEXT,
  error TEXT,
  lease_owner TEXT,
  lease_until INTEGER,
  idempotency_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_voice_calls_machine ON voice_calls(machine_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_voice_calls_phone ON voice_calls(wa_id, status, created_at);

CREATE TABLE IF NOT EXISTS voice_call_events (
  id TEXT PRIMARY KEY,
  call_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  detail_json TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE(call_id, sequence),
  FOREIGN KEY(call_id) REFERENCES voice_calls(id)
);
CREATE INDEX IF NOT EXISTS idx_voice_call_events_call ON voice_call_events(call_id, sequence);

CREATE TABLE IF NOT EXISTS machine_voice_heartbeats (
  machine_id TEXT PRIMARY KEY,
  updated_at INTEGER NOT NULL,
  active_call_count INTEGER NOT NULL DEFAULT 0,
  capacity INTEGER NOT NULL DEFAULT 0,
  key_fingerprint TEXT
);
