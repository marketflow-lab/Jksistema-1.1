ALTER TABLE bindings ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1));

CREATE UNIQUE INDEX IF NOT EXISTS uq_bindings_active_primary_per_user
  ON bindings(client_id, username COLLATE NOCASE)
  WHERE active = 1 AND is_primary = 1;

CREATE INDEX IF NOT EXISTS idx_bindings_user_primary
  ON bindings(client_id, username, active, is_primary);
