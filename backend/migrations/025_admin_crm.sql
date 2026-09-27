-- Admin-only notes and tags on people, and invitation links.
--
-- Notes and tags are the operator's own bookkeeping ("refund given", "VIP", "asked about CSV");
-- users never see them. Both go when the person is deleted.

CREATE TABLE IF NOT EXISTS admin_tags (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
    color TEXT NOT NULL DEFAULT 'c1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_admin_tags_name ON admin_tags (lower(name));

CREATE TABLE IF NOT EXISTS user_admin_tags (
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag_id INT NOT NULL REFERENCES admin_tags(id) ON DELETE CASCADE,
    created_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_user_admin_tags_tag ON user_admin_tags (tag_id);

CREATE TABLE IF NOT EXISTS admin_notes (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    author_id INT REFERENCES users(id) ON DELETE SET NULL,
    body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
    pinned BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_notes_user ON admin_notes (user_id, created_at DESC);

-- Searching people by part of an email address.
CREATE INDEX IF NOT EXISTS idx_users_email_trgm ON users USING gin (email gin_trgm_ops);

-- An admin-created account can be sent an invitation that sets its first password.
ALTER TABLE auth_tokens DROP CONSTRAINT IF EXISTS auth_tokens_kind_check;
ALTER TABLE auth_tokens ADD CONSTRAINT auth_tokens_kind_check CHECK (kind IN ('reset', 'verify', 'invite'));
