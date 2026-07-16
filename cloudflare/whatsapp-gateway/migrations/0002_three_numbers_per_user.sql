CREATE INDEX IF NOT EXISTS idx_bindings_user_active
  ON bindings(client_id, username, active, created_at);
