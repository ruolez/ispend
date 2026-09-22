CREATE TABLE IF NOT EXISTS import_layouts (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  layout_key TEXT NOT NULL,
  header JSONB,
  mapping JSONB NOT NULL,
  bank_profile TEXT,
  account_id INT REFERENCES accounts(id) ON DELETE SET NULL,
  sample_filename TEXT,
  times_used INT NOT NULL DEFAULT 1,
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, layout_key)
);
