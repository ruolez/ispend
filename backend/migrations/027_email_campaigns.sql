-- Messages from the operator: to one person or to everyone matching a people-list filter.
--
-- 'service' messages are about the account (an outage, a change to the terms) and go to everyone
-- addressed; 'marketing' ones carry a one-click unsubscribe and skip anyone who used it. The
-- suppression list keys on a hash of the address, so it outlives an erased account.

CREATE TABLE IF NOT EXISTS email_campaigns (
    id SERIAL PRIMARY KEY,
    subject TEXT NOT NULL CHECK (length(subject) BETWEEN 1 AND 200),
    body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 20000),
    category TEXT NOT NULL CHECK (category IN ('service', 'marketing')),
    audience JSONB NOT NULL,
    recipients INT NOT NULL DEFAULT 0,
    sent INT NOT NULL DEFAULT 0,
    failed INT NOT NULL DEFAULT 0,
    skipped INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'sending' CHECK (status IN ('sending', 'done', 'canceled', 'error')),
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

ALTER TABLE email_log DROP CONSTRAINT IF EXISTS email_log_campaign_fk;
ALTER TABLE email_log ADD CONSTRAINT email_log_campaign_fk
    FOREIGN KEY (campaign_id) REFERENCES email_campaigns(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_email_log_campaign ON email_log (campaign_id, status) WHERE campaign_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS email_suppressions (
    email_sha256 TEXT PRIMARY KEY,
    reason TEXT NOT NULL CHECK (reason IN ('unsubscribed', 'bounced', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A campaign message is claimed ('sending') before it goes out, so two workers never send it twice.
ALTER TABLE email_log DROP CONSTRAINT IF EXISTS email_log_status_check;
ALTER TABLE email_log ADD CONSTRAINT email_log_status_check
    CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'skipped'));
