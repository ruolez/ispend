-- Only self-service sign-ups are asked to confirm their address before they can sign in. The flag
-- is set at signup, and only while "Require email verification" is on, so it never applies
-- retroactively to admin-created or pre-existing accounts. The gate is user_state.needs_verification().
ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_required BOOLEAN NOT NULL DEFAULT false;
