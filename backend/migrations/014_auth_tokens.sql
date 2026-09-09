-- One table for both single-use email links (password reset and email verification): the
-- lifecycle, the entropy, the expiry and the atomic "mark used" query are identical, and one
-- table means one place where that can be got wrong.
--
-- The token itself is never stored. It is 256 bits of CSPRNG output, so SHA-256 is the right
-- hash here: fast (there is nothing to slow down an attacker guessing an unguessable value) and
-- indexable. A password KDF would be cargo-cult.
CREATE TABLE auth_tokens (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('reset', 'verify')),
  token_hash TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,
  created_ip INET,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_auth_tokens_hash ON auth_tokens (token_hash);
CREATE INDEX idx_auth_tokens_user ON auth_tokens (user_id, kind, created_at DESC);
