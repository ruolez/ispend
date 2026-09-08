-- Per-category monthly budgets: one explicit row per (category, month); "copy last month" clones a month.
-- month is always the first day of the month. Parent/child overlap and transfer categories are rejected by the API.
CREATE TABLE budgets (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category_id INT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  month DATE NOT NULL CHECK (month = date_trunc('month', month)::date),
  amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, category_id, month)
);
CREATE INDEX idx_budgets_user_month ON budgets (user_id, month);
