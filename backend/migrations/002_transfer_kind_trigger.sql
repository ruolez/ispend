-- A transaction filed under a transfer-kind category (e.g. Transfers > Credit Card Payment)
-- is a transfer no matter which code path set the category. Keep is_transfer/is_excluded in
-- sync at the row level so reports, the review queue and cash-flow totals all agree.

CREATE OR REPLACE FUNCTION trg_transactions_transfer_kind() RETURNS trigger AS $$
DECLARE
  new_kind TEXT;
  old_kind TEXT;
BEGIN
  IF NEW.category_id IS NOT NULL THEN
    SELECT kind INTO new_kind FROM categories WHERE id = NEW.category_id;
  END IF;
  IF new_kind = 'transfer' THEN
    NEW.is_transfer := TRUE;
    NEW.is_excluded := TRUE;
  ELSIF TG_OP = 'UPDATE' AND OLD.category_id IS DISTINCT FROM NEW.category_id
        AND OLD.category_id IS NOT NULL AND NEW.transfer_pair_id IS NULL
        AND NEW.is_transfer = OLD.is_transfer AND NEW.is_excluded = OLD.is_excluded THEN
    SELECT kind INTO old_kind FROM categories WHERE id = OLD.category_id;
    IF old_kind = 'transfer' THEN
      NEW.is_transfer := FALSE;
      NEW.is_excluded := FALSE;
    END IF;
  END IF;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS transactions_transfer_kind ON transactions;
CREATE TRIGGER transactions_transfer_kind
  BEFORE INSERT OR UPDATE OF category_id ON transactions
  FOR EACH ROW EXECUTE FUNCTION trg_transactions_transfer_kind();

-- Backfill rows already filed under transfer categories.
UPDATE transactions t SET is_transfer = TRUE, is_excluded = TRUE
FROM categories c
WHERE c.id = t.category_id AND c.kind = 'transfer' AND (NOT t.is_transfer OR NOT t.is_excluded);
