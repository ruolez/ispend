-- Admin security foundation.
--
-- audit_log learns where an action came from (ip, user agent), who it was about (target_user_id)
-- and whether an administrator did it (by_admin). Admin actions are kept longer than ordinary
-- user activity, so the flag also drives the split retention in util.audit().
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS ip INET;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS user_agent TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS target_user_id INT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS by_admin BOOLEAN NOT NULL DEFAULT false;

UPDATE audit_log a SET target_user_id = (a.detail->>'id')::int, by_admin = true
 WHERE a.action ~ '^(user\.(create|update|password_reset|lock|unlock|restore|delete)|billing\.(comp|extend_trial|extend_grace|cancel))$'
   AND a.detail->>'id' ~ '^\d{1,9}$'
   AND EXISTS (SELECT 1 FROM users u WHERE u.id = (a.detail->>'id')::int);
UPDATE audit_log SET by_admin = true
 WHERE NOT by_admin
   AND (action LIKE 'admin.%'
        OR action IN ('user.create', 'user.purge', 'settings.admin_update', 'billing.config_update',
                      'billing.sync_stale'));

CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log (target_user_id, created_at DESC)
    WHERE target_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_log_admin ON audit_log (created_at DESC) WHERE by_admin;

-- Every sign-in attempt, successful or not. identity is only kept when no account matched, so a
-- typo'd username is not stored against a real person. ip_prefix (/24 or /48) is what the
-- "admin signed in from a new network" check compares.
CREATE TABLE IF NOT EXISTS login_events (
    id BIGSERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('password', 'signup', 'reset', 'step_up')),
    ok BOOLEAN NOT NULL,
    reason TEXT,
    identity TEXT,
    ip INET,
    ip_prefix CIDR,
    user_agent TEXT,
    browser TEXT,
    os TEXT,
    device TEXT CHECK (device IN ('desktop', 'mobile', 'tablet', 'bot', 'unknown')),
    country CHAR(2),
    new_network BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_login_events_user ON login_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_events_created ON login_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_events_failed ON login_events (ip, created_at DESC) WHERE NOT ok;
