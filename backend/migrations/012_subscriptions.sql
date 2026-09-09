-- One row per user.
--
-- A separate table rather than columns on users: webhooks arrive keyed by stripe_customer_id,
-- which wants its own unique index on what is otherwise the hottest table in the app; auth.login
-- and auth.me both SELECT * FROM users and should not drag twenty billing columns through the
-- hot path; and a bad billing migration cannot corrupt the account table. The cost is nothing --
-- the entitlement loader replaces the single lookup refresh_session_user already ran with one
-- LEFT JOIN on indexed primary keys.
--
-- `status` mirrors Stripe verbatim and is deliberately NOT constrained by a CHECK: a status
-- Stripe introduces after this ships must still be storable, or the webhook 500s and Stripe
-- retries it for three days. entitlement.evaluate() decides what an unknown one means.
CREATE TABLE subscriptions (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'trialing',
  plan TEXT,
  price_id TEXT,
  stripe_customer_id TEXT,
  stripe_subscription_id TEXT,
  trial_end TIMESTAMPTZ,
  current_period_end TIMESTAMPTZ,
  cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
  lapsed_at TIMESTAMPTZ,
  grace_until TIMESTAMPTZ,
  -- A comp overrides everything while it lasts; 'infinity' means free forever.
  comped_until TIMESTAMPTZ,
  -- Stripe does not order webhook deliveries. An event older than this is dropped.
  last_event_at BIGINT NOT NULL DEFAULT 0,
  synced_at TIMESTAMPTZ,
  -- Send-once stamps; the lapse ones are cleared when the account recovers.
  trial_ending_email_at TIMESTAMPTZ,
  trial_ended_email_at TIMESTAMPTZ,
  payment_failed_email_at TIMESTAMPTZ,
  grace_ending_email_at TIMESTAMPTZ,
  read_only_email_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_subscriptions_stripe_customer
  ON subscriptions (stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;
CREATE UNIQUE INDEX uq_subscriptions_stripe_subscription
  ON subscriptions (stripe_subscription_id) WHERE stripe_subscription_id IS NOT NULL;
CREATE INDEX idx_subscriptions_trial_end ON subscriptions (trial_end) WHERE trial_end IS NOT NULL;
CREATE INDEX idx_subscriptions_grace_until ON subscriptions (grace_until) WHERE grace_until IS NOT NULL;

-- Everyone who already had an account predates billing entirely and must never be locked out by
-- an upgrade. Comped forever; an operator can revoke individually later.
INSERT INTO subscriptions (user_id, status, comped_until)
SELECT id, 'comped', 'infinity'::timestamptz FROM users
ON CONFLICT (user_id) DO NOTHING;
