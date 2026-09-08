-- Free-form labels across categories ("Trip 2026", "Reimbursable"). Many tags per transaction.
CREATE TABLE tags (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  color TEXT NOT NULL DEFAULT 'c1',
  sort_order INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_tags_user_name ON tags (user_id, lower(name));

CREATE TABLE transaction_tags (
  transaction_id INT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  tag_id INT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (transaction_id, tag_id)
);
CREATE INDEX idx_transaction_tags_tag ON transaction_tags (tag_id, transaction_id);

-- History timeline kinds for tag edits (and for the split feature that follows).
ALTER TABLE transaction_events DROP CONSTRAINT transaction_events_kind_check;
ALTER TABLE transaction_events ADD CONSTRAINT transaction_events_kind_check
  CHECK (kind IN ('imported','rule','merchant','builtin','ai','manual','transfer','note','excluded','tag','split'));
