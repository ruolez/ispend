-- The latest balance and last transaction date per account (accounts list, dashboard) look up an
-- account's newest row; only (account_id, fingerprint, occurrence) led by account_id before.
CREATE INDEX IF NOT EXISTS idx_txn_account_date ON transactions (account_id, txn_date DESC, id DESC);
-- Last import per account (accounts list).
CREATE INDEX IF NOT EXISTS idx_statements_account ON statements (account_id);
