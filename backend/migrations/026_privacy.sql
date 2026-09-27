-- Data rights: a person can download everything iSpend holds about them, and have it erased.
--
-- data_exports tracks the archives (built in the background, kept 7 days). erasures is the record
-- that an erasure happened — kept after the person is gone, holding no personal data beyond a
-- one-way hash of the email address, so a repeat request can be recognised.

CREATE TABLE IF NOT EXISTS data_exports (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_by INT REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'error', 'expired')),
    token TEXT NOT NULL,
    file_path TEXT,
    size_bytes BIGINT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    downloaded_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_data_exports_user ON data_exports (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS erasures (
    id SERIAL PRIMARY KEY,
    erased_user_id INT NOT NULL,
    email_sha256 TEXT,
    requested_by TEXT NOT NULL CHECK (requested_by IN ('self', 'admin')),
    admin_id INT,
    reason TEXT,
    stripe_canceled BOOLEAN NOT NULL DEFAULT false,
    counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    files_removed INT NOT NULL DEFAULT 0,
    bytes_removed BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
