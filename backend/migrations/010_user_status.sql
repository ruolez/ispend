-- Admin console: an explicit user lifecycle (active / locked / deleted) replacing the is_active
-- flag, plus the indexes the instance-wide statistics need.
--
-- is_active kept the two very different ideas "the admin froze this account" and "the admin
-- deleted this account" in one boolean, so a deleted user could not be told from a locked one
-- and neither could be restored. status separates them; deleted_at makes deletion recoverable.

ALTER TABLE users ADD COLUMN IF NOT EXISTS status      TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_at   TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS lock_reason TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at  TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_by  INT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ NOT NULL DEFAULT now();
-- Written by auth.login. audit_log records 'auth.login' too, but it self-prunes at 180 days and
-- goes NULL when a user is purged, so it cannot answer "when did this account last sign in?".
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

-- Carry the old flag over before it becomes derived.
UPDATE users SET status = 'locked', locked_at = now() WHERE NOT is_active;

ALTER TABLE users ADD CONSTRAINT users_status_check CHECK (status IN ('active','locked','deleted'));
ALTER TABLE users ADD CONSTRAINT users_deleted_at_check
  CHECK ((status = 'deleted') = (deleted_at IS NOT NULL));

-- is_active stays readable, becomes derived: it can never drift from status, and any writer we
-- missed fails loudly instead of silently disagreeing with the login gate.
ALTER TABLE users DROP COLUMN is_active;
ALTER TABLE users ADD COLUMN is_active BOOLEAN
  GENERATED ALWAYS AS (status = 'active') STORED;

CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);

-- Instance-wide time series scan by created_at; every existing index leads with user_id.
CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions (created_at);
CREATE INDEX IF NOT EXISTS idx_statements_created_at   ON statements (created_at);
-- Per-user activity feed in the admin drawer, and the action filter on the Activity tab.
CREATE INDEX IF NOT EXISTS idx_audit_log_user_created  ON audit_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action        ON audit_log (action, created_at DESC);
