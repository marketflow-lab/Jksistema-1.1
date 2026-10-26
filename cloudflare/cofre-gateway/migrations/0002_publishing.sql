ALTER TABLE cofre_company_state ADD COLUMN fence TEXT;

CREATE TABLE cofre_message_ownership (
  company TEXT NOT NULL,
  guild_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  owner_username TEXT NOT NULL CHECK(length(owner_username) BETWEEN 1 AND 180),
  PRIMARY KEY(company,guild_id,channel_id,message_id)
) WITHOUT ROWID;
CREATE TABLE cofre_deleted_channels (
  company TEXT NOT NULL, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
  PRIMARY KEY(company,guild_id,channel_id)
) WITHOUT ROWID;
CREATE TABLE cofre_mutation_receipts (
  company TEXT NOT NULL, request_id TEXT NOT NULL, actor TEXT NOT NULL,
  fingerprint TEXT NOT NULL, message_id TEXT, created_at INTEGER NOT NULL,
  PRIMARY KEY(company,request_id)
) WITHOUT ROWID;

CREATE TABLE cofre_asset_budget (
  id INTEGER PRIMARY KEY CHECK(id=1),
  bytes_reserved INTEGER NOT NULL CHECK(bytes_reserved BETWEEN 0 AND 367001600)
);
INSERT INTO cofre_asset_budget VALUES(1,0);
CREATE TABLE cofre_asset_days (
  day TEXT PRIMARY KEY,
  writes_reserved INTEGER NOT NULL DEFAULT 0 CHECK(writes_reserved BETWEEN 0 AND 250),
  reservations_reserved INTEGER NOT NULL DEFAULT 0 CHECK(reservations_reserved BETWEEN 0 AND 250)
) WITHOUT ROWID;
CREATE TABLE cofre_assets (
  company TEXT NOT NULL, asset_id TEXT NOT NULL, request_id TEXT NOT NULL,
  owner_username TEXT NOT NULL, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
  filename TEXT NOT NULL, size INTEGER NOT NULL CHECK(size BETWEEN 1 AND 10485760),
  sha256 TEXT NOT NULL CHECK(length(sha256)=64), content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  status TEXT NOT NULL CHECK(status IN ('pending','uploading','ready','deleting')),
  attached INTEGER NOT NULL DEFAULT 0 CHECK(attached IN (0,1)),
  created_at INTEGER NOT NULL, upload_fence TEXT, lease_until INTEGER,
  PRIMARY KEY(company,asset_id), UNIQUE(company,request_id)
) WITHOUT ROWID;
CREATE INDEX cofre_assets_cleanup ON cofre_assets(attached,created_at,status);
CREATE TABLE cofre_asset_links (
  company TEXT NOT NULL, asset_id TEXT NOT NULL,
  guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, message_id TEXT NOT NULL,
  PRIMARY KEY(company,asset_id,guild_id,channel_id,message_id),
  FOREIGN KEY(company,asset_id) REFERENCES cofre_assets(company,asset_id)
) WITHOUT ROWID;
CREATE TRIGGER cofre_assets_reserve AFTER INSERT ON cofre_assets BEGIN
  UPDATE cofre_asset_budget SET bytes_reserved=bytes_reserved+NEW.size WHERE id=1;
  INSERT INTO cofre_asset_days(day,reservations_reserved)
  VALUES(date(NEW.created_at,'unixepoch'),1)
  ON CONFLICT(day) DO UPDATE SET reservations_reserved=reservations_reserved+1;
END;
CREATE TRIGGER cofre_assets_release AFTER DELETE ON cofre_assets BEGIN
  UPDATE cofre_asset_budget SET bytes_reserved=bytes_reserved-OLD.size WHERE id=1;
END;
CREATE TRIGGER cofre_assets_write_budget AFTER UPDATE OF upload_fence ON cofre_assets
WHEN NEW.status='uploading' AND NEW.upload_fence IS NOT NULL
 AND (OLD.upload_fence IS NULL OR OLD.upload_fence<>NEW.upload_fence)
BEGIN
  INSERT INTO cofre_asset_days(day,writes_reserved)
  VALUES(date(NEW.lease_until-300,'unixepoch'),1)
  ON CONFLICT(day) DO UPDATE SET writes_reserved=writes_reserved+1;
END;
