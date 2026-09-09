-- Email becomes a login identity alongside username, and the account row gains the bookkeeping
-- the billing and password-reset flows need.
--
-- Existing installs have no email addresses: the column is nullable and the unique index is
-- partial, so the seeded `admin` account and every admin-created user keep signing in with
-- their username and unchanged password.
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;

-- Bumped on password change and on a completed reset. Flask sessions are stateless, so without
-- this a password reset does NOT sign a thief out: their signed cookie keeps working.
ALTER TABLE users ADD COLUMN IF NOT EXISTS session_epoch INT NOT NULL DEFAULT 0;

-- The API always stores a lower-cased address; this functional index is the backstop. A partial
-- index rather than citext, which would need a second extension and would change comparison
-- semantics for every future query on the column.
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (lower(email)) WHERE email IS NOT NULL;
