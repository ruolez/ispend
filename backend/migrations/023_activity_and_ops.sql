-- Engagement and operations data: how people use iSpend over time (counts and days, never
-- contents), where sign-ups come from, and what went wrong on the server.

-- last_seen_at moves on any request (throttled), last_active_at on a value event; the first_*
-- stamps are permanent, because deleting a statement must not un-activate someone.
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_upload_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_commit_at TIMESTAMPTZ;
UPDATE users SET last_seen_at = last_login_at WHERE last_seen_at IS NULL;
UPDATE users u SET first_upload_at = s.fu, first_commit_at = s.fc
  FROM (SELECT user_id, MIN(created_at) AS fu, MIN(committed_at) AS fc FROM statements GROUP BY user_id) s
 WHERE s.user_id = u.id;
-- Statements deleted since leave only their audit trail (kept for the retention window).
UPDATE users u SET first_commit_at = LEAST(COALESCE(u.first_commit_at, a.t), a.t)
  FROM (SELECT user_id, MIN(created_at) AS t FROM audit_log
         WHERE action = 'statement.commit' AND user_id IS NOT NULL GROUP BY 1) a
 WHERE a.user_id = u.id;
UPDATE users u SET last_active_at = a.t
  FROM (SELECT user_id, MAX(created_at) AS t FROM transaction_events WHERE user_id IS NOT NULL GROUP BY 1) a
 WHERE a.user_id = u.id AND u.last_active_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at);
CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users (last_seen_at);

-- One row per person per day they used the app; kinds is a bit set
-- (1 seen, 2 import, 4 categorize, 8 report, 16 dashboard, 32 upload). See activity.py.
CREATE TABLE IF NOT EXISTS user_activity_days (
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day DATE NOT NULL,
    kinds SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE INDEX IF NOT EXISTS idx_uad_day ON user_activity_days (day);
INSERT INTO user_activity_days (user_id, day, kinds)
SELECT user_id, d, bit_or(k)::smallint FROM (
    SELECT user_id, committed_at::date AS d, 2 AS k FROM statements WHERE committed_at IS NOT NULL
    UNION ALL
    SELECT user_id, created_at::date, 32 FROM statements
    UNION ALL
    SELECT user_id, created_at::date, CASE WHEN action = 'statement.commit' THEN 2 ELSE 4 END
      FROM audit_log
     WHERE user_id IS NOT NULL
       AND action IN ('statement.commit', 'transaction.update', 'transactions.bulk', 'review.resolve',
                      'transactions.pair', 'transaction.split', 'rule.create', 'rule.apply', 'rule.run_all')
    UNION ALL
    SELECT user_id, created_at::date, 1 FROM audit_log WHERE user_id IS NOT NULL AND action = 'auth.login'
) s
WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = s.user_id)
GROUP BY 1, 2
ON CONFLICT (user_id, day) DO NOTHING;

-- Import health: how long reading and importing a file took.
ALTER TABLE statements ADD COLUMN IF NOT EXISTS parse_ms INT;
ALTER TABLE statements ADD COLUMN IF NOT EXISTS parse_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE statements ADD COLUMN IF NOT EXISTS commit_ms INT;

-- AI spend: whose key paid, and what it cost. Reading a statement with AI was logged as
-- "categorize" with no items; it gets its own purpose.
ALTER TABLE ai_calls DROP CONSTRAINT IF EXISTS ai_calls_purpose_check;
ALTER TABLE ai_calls ADD CONSTRAINT ai_calls_purpose_check
    CHECK (purpose IN ('categorize', 'insights', 'test', 'extract'));
ALTER TABLE ai_calls ADD COLUMN IF NOT EXISTS key_source TEXT CHECK (key_source IN ('own', 'shared'));
ALTER TABLE ai_calls ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(12, 6);
UPDATE ai_calls SET purpose = 'extract' WHERE purpose = 'categorize' AND item_count = 0;

-- Every outgoing email, queued before the send and updated after it.
CREATE TABLE IF NOT EXISTS email_log (
    id BIGSERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id) ON DELETE CASCADE,
    to_address TEXT NOT NULL,
    template TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('auth', 'lifecycle', 'billing', 'admin', 'campaign', 'test')),
    subject TEXT,
    campaign_id INT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed', 'skipped')),
    error TEXT,
    message_id TEXT,
    sent_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_email_log_user ON email_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_log_created ON email_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_log_unsent ON email_log (status, created_at DESC) WHERE status <> 'sent';

-- Server errors, grouped by fingerprint. Operational data only: never backed up, and no foreign
-- key, because a restore truncates backed-up tables without CASCADE.
CREATE TABLE IF NOT EXISTS app_errors (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('request', 'job', 'tick')),
    fingerprint TEXT NOT NULL,
    error_type TEXT NOT NULL,
    message TEXT,
    location TEXT,
    status INT,
    user_id INT,
    traceback TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_errors_created ON app_errors (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_errors_fp ON app_errors (fingerprint, created_at DESC);

-- Where a sign-up came from (first-party; see attribution.py).
CREATE TABLE IF NOT EXISTS signup_attribution (
    user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    utm_term TEXT,
    utm_content TEXT,
    referrer_host TEXT,
    landing_path TEXT,
    first_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_signup_attr_channel ON signup_attribution (channel);
