-- Split transactions: the parent row keeps its amount and a primary category; lines attribute the
-- amount to several categories for the category reports. Lines are validated by the API and backed by
-- a deferred trigger (>= 2 lines, same sign, sum equal to the parent amount).
CREATE TABLE transaction_splits (
  id SERIAL PRIMARY KEY,
  transaction_id INT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  category_id INT REFERENCES categories(id) ON DELETE SET NULL,
  amount NUMERIC(12,2) NOT NULL CHECK (amount <> 0),
  note TEXT,
  sort_order INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_transaction_splits_txn ON transaction_splits (transaction_id, sort_order);
CREATE INDEX idx_transaction_splits_cat ON transaction_splits (category_id);

CREATE OR REPLACE FUNCTION check_transaction_splits() RETURNS trigger AS $$
DECLARE
  tid INT;
  parent NUMERIC;
  n INT;
  total NUMERIC;
  wrong_sign INT;
BEGIN
  IF TG_TABLE_NAME = 'transactions' THEN
    tid := NEW.id;
  ELSE
    tid := COALESCE(NEW.transaction_id, OLD.transaction_id);
  END IF;
  SELECT amount INTO parent FROM transactions WHERE id = tid;
  IF parent IS NULL THEN
    RETURN NULL;  -- parent gone (cascade)
  END IF;
  SELECT COUNT(*), COALESCE(SUM(amount), 0), COUNT(*) FILTER (WHERE sign(amount) <> sign(parent))
    INTO n, total, wrong_sign
    FROM transaction_splits WHERE transaction_id = tid;
  IF n = 0 THEN
    RETURN NULL;
  END IF;
  IF n < 2 THEN
    RAISE EXCEPTION 'A split needs at least two lines';
  END IF;
  IF wrong_sign > 0 THEN
    RAISE EXCEPTION 'Split lines must have the same sign as the transaction';
  END IF;
  IF total <> parent THEN
    RAISE EXCEPTION 'Split lines must add up to the transaction amount';
  END IF;
  RETURN NULL;
END
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER transaction_splits_sum
  AFTER INSERT OR UPDATE OR DELETE ON transaction_splits
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_transaction_splits();

CREATE CONSTRAINT TRIGGER transaction_splits_parent
  AFTER UPDATE OF amount ON transactions
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_transaction_splits();
