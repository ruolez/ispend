-- Webhook idempotency. Stripe's event id is the primary key, and the row is inserted in the SAME
-- transaction as the state change: a crash mid-processing leaves the event unrecorded, so the
-- retry (Stripe keeps trying for three days) does the work rather than being swallowed.
CREATE TABLE stripe_events (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  stripe_created BIGINT NOT NULL,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'processed' CHECK (status IN ('processed', 'ignored', 'failed')),
  error TEXT,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_stripe_events_received ON stripe_events (received_at DESC);
