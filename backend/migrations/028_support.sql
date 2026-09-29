-- Problem reports: a customer writes to the operator from Help, the operator answers from the
-- console, and both sides see one thread per report.
--
-- The first user message is the report's description. Internal messages are the operator's notes
-- on the thread; the customer never sees them. Screenshots live under the customer's statements
-- folder (<uid>/support/...), so erasing the account removes them with everything else.

CREATE TABLE IF NOT EXISTS support_reports (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('bug', 'data', 'question', 'idea', 'billing')),
    subject TEXT NOT NULL CHECK (length(subject) BETWEEN 1 AND 140),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'waiting', 'resolved')),
    impact TEXT CHECK (impact IN ('blocking', 'annoying', 'minor')),
    priority SMALLINT CHECK (priority BETWEEN 1 AND 3),
    context JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (octet_length(context::text) <= 16384),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_user_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_admin_at TIMESTAMPTZ,
    user_seen_at TIMESTAMPTZ,
    admin_seen_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_support_reports_user ON support_reports (user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_reports_status ON support_reports (status, last_user_at);

CREATE TABLE IF NOT EXISTS support_messages (
    id SERIAL PRIMARY KEY,
    report_id INT NOT NULL REFERENCES support_reports(id) ON DELETE CASCADE,
    author_id INT REFERENCES users(id) ON DELETE SET NULL,
    author_role TEXT NOT NULL CHECK (author_role IN ('user', 'admin')),
    internal BOOLEAN NOT NULL DEFAULT false CHECK (NOT internal OR author_role = 'admin'),
    body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_messages_report ON support_messages (report_id, id);

CREATE TABLE IF NOT EXISTS support_attachments (
    id SERIAL PRIMARY KEY,
    message_id INT NOT NULL REFERENCES support_messages(id) ON DELETE CASCADE,
    report_id INT NOT NULL REFERENCES support_reports(id) ON DELETE CASCADE,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stored_path TEXT NOT NULL,
    mime TEXT NOT NULL CHECK (mime IN ('image/png', 'image/jpeg', 'image/webp')),
    bytes INT NOT NULL CHECK (bytes > 0),
    width INT NOT NULL,
    height INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_support_attachments_message ON support_attachments (message_id);
CREATE INDEX IF NOT EXISTS idx_support_attachments_report ON support_attachments (report_id);

-- The id a customer quotes ("Report" on an error toast) finds the server error it belongs to.
ALTER TABLE app_errors ADD COLUMN IF NOT EXISTS request_id TEXT;
CREATE INDEX IF NOT EXISTS idx_app_errors_request ON app_errors (request_id) WHERE request_id IS NOT NULL;

ALTER TABLE email_log DROP CONSTRAINT IF EXISTS email_log_category_check;
ALTER TABLE email_log ADD CONSTRAINT email_log_category_check
    CHECK (category IN ('auth', 'lifecycle', 'billing', 'admin', 'campaign', 'test', 'support'));
