ALTER TABLE inbox ADD COLUMN media_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE inbox ADD COLUMN media_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE inbox ADD COLUMN media_next_attempt_at INTEGER;
ALTER TABLE inbox ADD COLUMN media_last_attempt_at INTEGER;
ALTER TABLE inbox ADD COLUMN media_lease_until INTEGER;
ALTER TABLE inbox ADD COLUMN media_error_class TEXT;
ALTER TABLE inbox ADD COLUMN media_expires_at INTEGER;

UPDATE inbox
SET
  media_state = CASE
    WHEN media_id IS NULL THEN 'none'
    WHEN media_object_key IS NOT NULL THEN 'stored'
    WHEN status = 'media_fetching' THEN 'pending'
    WHEN status = 'media_retry' THEN 'retry_wait'
    WHEN status IN ('completed', 'failed', 'dead_letter', 'unsupported') THEN 'failed'
    ELSE 'pending'
  END,
  media_next_attempt_at = CASE
    WHEN media_id IS NOT NULL AND media_object_key IS NULL AND status NOT IN ('completed', 'failed', 'dead_letter', 'unsupported')
      THEN COALESCE(media_next_attempt_at, CAST(strftime('%s', 'now') AS INTEGER))
    ELSE media_next_attempt_at
  END,
  media_expires_at = CASE
    WHEN media_id IS NOT NULL THEN COALESCE(media_expires_at, received_at + 172800)
    ELSE media_expires_at
  END;

CREATE INDEX IF NOT EXISTS idx_inbox_media_retry_due
  ON inbox(media_state, media_next_attempt_at, media_expires_at);

CREATE INDEX IF NOT EXISTS idx_inbox_media_expiration
  ON inbox(media_expires_at, media_state);

ALTER TABLE outbox ADD COLUMN idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_idempotency
  ON outbox(idempotency_key)
  WHERE idempotency_key IS NOT NULL;
