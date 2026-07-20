ALTER TABLE outbound_media ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE outbound_media ADD COLUMN lease_owner TEXT;
ALTER TABLE outbound_media ADD COLUMN lease_until INTEGER;
ALTER TABLE outbound_media ADD COLUMN last_attempt_at INTEGER;

UPDATE outbound_media
SET
  attempts = CASE WHEN attempts < 1 THEN 1 ELSE attempts END,
  lease_owner = NULL,
  lease_until = NULL,
  last_attempt_at = COALESCE(last_attempt_at, updated_at)
WHERE status NOT IN ('sent', 'delivered', 'read');

CREATE INDEX IF NOT EXISTS idx_outbound_media_lease
  ON outbound_media(status, lease_until, attempts);
