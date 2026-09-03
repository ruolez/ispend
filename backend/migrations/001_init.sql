-- iSpend initial schema. Every domain table is scoped by user_id.
-- Money: NUMERIC(12,2); transactions.amount negative = money out, positive = money in.

CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Global, admin-managed key/value settings (OpenRouter key/model, AI switches).
CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  institution TEXT,
  account_type TEXT NOT NULL DEFAULT 'checking'
    CHECK (account_type IN ('checking','savings','credit_card','line_of_credit','loan','investment','cash','other')),
  currency CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD','CAD')),
  last4 TEXT,
  color TEXT NOT NULL DEFAULT 'c1',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, name)
);

CREATE TABLE categories (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  parent_id INT REFERENCES categories(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'expense' CHECK (kind IN ('expense','income','transfer')),
  -- color is a palette slot key (c1..c12); the frontend resolves it per theme
  color TEXT NOT NULL DEFAULT 'c1',
  icon TEXT,
  is_system BOOLEAN NOT NULL DEFAULT FALSE,
  sort_order INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_categories_user_slug ON categories (user_id, slug);
CREATE UNIQUE INDEX uq_categories_user_parent_name ON categories (user_id, COALESCE(parent_id, 0), lower(name));
CREATE INDEX idx_categories_user_parent ON categories (user_id, parent_id, sort_order);

CREATE TABLE rules (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT '',
  priority INT NOT NULL DEFAULT 100,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  match_type TEXT NOT NULL CHECK (match_type IN ('contains','starts_with','equals','regex')),
  match_field TEXT NOT NULL DEFAULT 'description_clean'
    CHECK (match_field IN ('description_clean','description_raw','merchant_key')),
  pattern TEXT NOT NULL,
  case_sensitive BOOLEAN NOT NULL DEFAULT FALSE,
  amount_min NUMERIC(12,2),
  amount_max NUMERIC(12,2),
  account_id INT REFERENCES accounts(id) ON DELETE CASCADE,
  category_id INT REFERENCES categories(id) ON DELETE CASCADE,
  set_transfer BOOLEAN NOT NULL DEFAULT FALSE,
  set_excluded BOOLEAN NOT NULL DEFAULT FALSE,
  hit_count INT NOT NULL DEFAULT 0,
  last_hit_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (category_id IS NOT NULL OR set_transfer OR set_excluded),
  CHECK (amount_min IS NULL OR amount_max IS NULL OR amount_min <= amount_max)
);
CREATE INDEX idx_rules_user_priority ON rules (user_id, priority, id);

CREATE TABLE statements (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id INT REFERENCES accounts(id) ON DELETE SET NULL,
  original_filename TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  ocr_path TEXT,
  file_sha256 TEXT NOT NULL,
  file_size INT NOT NULL,
  file_kind TEXT NOT NULL CHECK (file_kind IN ('csv','xlsx','xls','pdf')),
  status TEXT NOT NULL DEFAULT 'uploaded'
    CHECK (status IN ('uploaded','parsing','previewed','committing','committed','error','discarded')),
  bank_profile TEXT,
  profile_confidence NUMERIC(4,3),
  mapping JSONB,
  period_start DATE,
  period_end DATE,
  ocr_applied BOOLEAN NOT NULL DEFAULT FALSE,
  stats JSONB NOT NULL DEFAULT '{}'::jsonb,
  warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  committed_at TIMESTAMPTZ
);
CREATE INDEX idx_statements_user_created ON statements (user_id, created_at DESC);
CREATE INDEX idx_statements_user_sha ON statements (user_id, file_sha256);
CREATE INDEX idx_statements_in_progress ON statements (updated_at) WHERE status IN ('parsing','committing');

CREATE TABLE transactions (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  statement_id INT REFERENCES statements(id) ON DELETE SET NULL,
  txn_date DATE NOT NULL,
  posted_date DATE,
  amount NUMERIC(12,2) NOT NULL,
  currency CHAR(3) NOT NULL,
  balance NUMERIC(12,2),
  description_raw TEXT NOT NULL,
  description_clean TEXT NOT NULL,
  merchant_key TEXT NOT NULL,
  merchant_name TEXT NOT NULL,
  category_id INT REFERENCES categories(id) ON DELETE SET NULL,
  category_status TEXT NOT NULL DEFAULT 'none' CHECK (category_status IN ('none','suggested','confirmed')),
  category_source TEXT CHECK (category_source IN ('rule','merchant','builtin','ai','manual')),
  category_rule_id INT REFERENCES rules(id) ON DELETE SET NULL,
  category_confidence NUMERIC(4,3),
  ai_rationale TEXT,
  is_transfer BOOLEAN NOT NULL DEFAULT FALSE,
  transfer_pair_id INT REFERENCES transactions(id) ON DELETE SET NULL,
  is_excluded BOOLEAN NOT NULL DEFAULT FALSE,
  notes TEXT,
  fingerprint TEXT NOT NULL,
  occurrence INT NOT NULL DEFAULT 1,
  raw JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (account_id, fingerprint, occurrence)
);
CREATE INDEX idx_txn_user_date ON transactions (user_id, txn_date DESC, id DESC);
CREATE INDEX idx_txn_user_account_date ON transactions (user_id, account_id, txn_date);
CREATE INDEX idx_txn_user_category ON transactions (user_id, category_id);
CREATE INDEX idx_txn_user_merchant ON transactions (user_id, merchant_key);
CREATE INDEX idx_txn_statement ON transactions (statement_id);
CREATE INDEX idx_txn_uncategorized ON transactions (user_id, txn_date DESC) WHERE category_id IS NULL;
CREATE INDEX idx_txn_suggested ON transactions (user_id) WHERE category_status = 'suggested';
CREATE INDEX idx_txn_desc_lower ON transactions (user_id, lower(description_clean));

-- Preview staging for a statement; deleted on commit/discard.
CREATE TABLE import_rows (
  id SERIAL PRIMARY KEY,
  statement_id INT NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
  row_index INT NOT NULL,
  txn_date DATE,
  posted_date DATE,
  description TEXT,
  amount NUMERIC(12,2),
  balance NUMERIC(12,2),
  raw JSONB NOT NULL,
  merchant_name TEXT,
  fingerprint TEXT,
  occurrence INT NOT NULL DEFAULT 1,
  duplicate_of INT REFERENCES transactions(id) ON DELETE SET NULL,
  in_file_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
  is_valid BOOLEAN NOT NULL DEFAULT TRUE,
  include BOOLEAN NOT NULL DEFAULT TRUE,
  category_id INT REFERENCES categories(id) ON DELETE SET NULL,
  problems TEXT[] NOT NULL DEFAULT '{}',
  UNIQUE (statement_id, row_index)
);

CREATE TABLE transaction_events (
  id SERIAL PRIMARY KEY,
  transaction_id INT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('imported','rule','merchant','builtin','ai','manual','transfer','note','excluded')),
  detail JSONB,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_txn_events_txn ON transaction_events (transaction_id, created_at);

CREATE TABLE merchant_memory (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  merchant_key TEXT NOT NULL,
  display_name TEXT,
  category_id INT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  is_transfer BOOLEAN NOT NULL DEFAULT FALSE,
  times_used INT NOT NULL DEFAULT 1,
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, merchant_key)
);

CREATE TABLE recurring_dismissals (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  merchant_key TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, merchant_key)
);

CREATE TABLE anomaly_dismissals (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  transaction_id INT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, transaction_id)
);

CREATE TABLE ai_calls (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id) ON DELETE SET NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('categorize','insights','test')),
  model TEXT NOT NULL,
  item_count INT NOT NULL DEFAULT 0,
  prompt_tokens INT,
  completion_tokens INT,
  status TEXT NOT NULL CHECK (status IN ('ok','error')),
  error_message TEXT,
  duration_ms INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ai_calls_created ON ai_calls (created_at DESC);

CREATE TABLE insights (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  model TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, period_start, period_end)
);

CREATE TABLE audit_log (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id),
  action TEXT NOT NULL,
  detail JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_log_created_at ON audit_log (created_at DESC);
