-- Revenue history.
--
-- subscriptions is one row per user, overwritten in place by every webhook, so on its own it can
-- say what someone pays now but never what they paid last month. Two append-only tables fix that:
-- subscription_events (every change of status, plan or monthly value) and payments (every
-- invoice, with the amounts). Admin revenue metrics read only these, never Stripe.
--
-- user_id in both is deliberately NOT a foreign key: revenue history must survive a purge or an
-- erasure (bookkeeping), and a bare number means nothing once the users row is gone.

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS currency TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS unit_amount_cents INT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS quantity INT NOT NULL DEFAULT 1;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS billing_interval TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS interval_count INT NOT NULL DEFAULT 1;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS discount JSONB;
-- The monthly value this subscription contributes right now: 0 unless active or past_due.
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS mrr_cents INT NOT NULL DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS canceled_at TIMESTAMPTZ;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;
-- entitlement.evaluate() is the only place the access rules live; this is its cached answer so the
-- admin can filter and count by it in SQL. Refreshed on every write and hourly.
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS ent_state TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS ent_state_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_subscriptions_ent_state ON subscriptions (ent_state);

CREATE TABLE IF NOT EXISTS subscription_events (
    id BIGSERIAL PRIMARY KEY,
    user_id INT,
    stripe_subscription_id TEXT,
    stripe_event_id TEXT,
    source TEXT NOT NULL CHECK (source IN ('webhook', 'sync', 'reconcile', 'admin', 'signup', 'system')),
    kind TEXT NOT NULL,
    status_from TEXT,
    status_to TEXT,
    plan_from TEXT,
    plan_to TEXT,
    mrr_from_cents INT NOT NULL DEFAULT 0,
    mrr_to_cents INT NOT NULL DEFAULT 0,
    currency TEXT,
    detail JSONB,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sub_events_user ON subscription_events (user_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS idx_sub_events_occurred ON subscription_events (occurred_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sub_events_stripe ON subscription_events (stripe_event_id, kind)
    WHERE stripe_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    user_id INT,
    stripe_invoice_id TEXT NOT NULL UNIQUE,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    stripe_payment_intent_id TEXT,
    stripe_charge_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('paid', 'failed', 'refunded', 'partially_refunded', 'void')),
    billing_reason TEXT,
    currency TEXT NOT NULL,
    subtotal_cents INT NOT NULL DEFAULT 0,
    discount_cents INT NOT NULL DEFAULT 0,
    tax_cents INT NOT NULL DEFAULT 0,
    amount_paid_cents INT NOT NULL DEFAULT 0,
    amount_refunded_cents INT NOT NULL DEFAULT 0,
    plan TEXT,
    billing_interval TEXT,
    coupon TEXT,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    attempt_count INT,
    paid_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments (user_id, paid_at);
CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments (paid_at) WHERE amount_paid_cents > 0;
CREATE INDEX IF NOT EXISTS idx_payments_failed ON payments (failed_at) WHERE status = 'failed';
CREATE INDEX IF NOT EXISTS idx_payments_intent ON payments (stripe_payment_intent_id)
    WHERE stripe_payment_intent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payments_charge ON payments (stripe_charge_id) WHERE stripe_charge_id IS NOT NULL;

ALTER TABLE stripe_events ADD COLUMN IF NOT EXISTS processing_ms INT;

-- One starting point per existing subscription, so "what did this user pay at time T" has an
-- origin. Amounts are unknown here (they were never stored); the first reconcile with Stripe
-- replaces a paying user's baseline with their real history.
INSERT INTO subscription_events (user_id, source, kind, status_to, plan_to, occurred_at, detail)
SELECT s.user_id, 'system', 'baseline', s.status, s.plan, COALESCE(s.updated_at, s.created_at),
       jsonb_build_object('comped', s.comped_until IS NOT NULL AND s.comped_until > now())
  FROM subscriptions s
 WHERE NOT EXISTS (SELECT 1 FROM subscription_events e WHERE e.user_id = s.user_id);
