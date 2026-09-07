-- Per-user AI usage lookups and trigram search on descriptions/merchants.
CREATE INDEX IF NOT EXISTS idx_ai_calls_user_created ON ai_calls (user_id, created_at DESC);
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_transactions_description_trgm ON transactions USING gin (description_raw gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_transactions_merchant_trgm ON transactions USING gin (merchant_name gin_trgm_ops);
