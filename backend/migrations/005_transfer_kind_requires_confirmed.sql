-- A transfer-kind category only makes a row a transfer once the category is confirmed (rule, memory,
-- manual, review). Suggestions from built-in hints, fuzzy memory or AI stay in spending/income until
-- someone accepts them, as the README promises. Accepting a suggestion (category_status -> confirmed)
-- now fires the trigger too.

CREATE OR REPLACE FUNCTION trg_transactions_transfer_kind() RETURNS trigger AS $$
DECLARE
  new_kind TEXT;
  old_kind TEXT;
BEGIN
  IF NEW.category_id IS NOT NULL AND NEW.category_status = 'confirmed' THEN
    SELECT kind INTO new_kind FROM categories WHERE id = NEW.category_id;
  END IF;
  IF new_kind = 'transfer' THEN
    NEW.is_transfer := TRUE;
    NEW.is_excluded := TRUE;
  ELSIF TG_OP = 'UPDATE'
        AND (OLD.category_id IS DISTINCT FROM NEW.category_id OR OLD.category_status IS DISTINCT FROM NEW.category_status)
        AND OLD.category_id IS NOT NULL AND OLD.category_status = 'confirmed' AND NEW.transfer_pair_id IS NULL
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
  BEFORE INSERT OR UPDATE OF category_id, category_status ON transactions
  FOR EACH ROW EXECUTE FUNCTION trg_transactions_transfer_kind();

-- Rows that were flagged only because of an unconfirmed transfer suggestion go back into the totals.
UPDATE transactions t SET is_transfer = FALSE, is_excluded = FALSE
FROM categories c
WHERE c.id = t.category_id AND c.kind = 'transfer' AND t.category_status = 'suggested'
  AND t.transfer_pair_id IS NULL AND (t.is_transfer OR t.is_excluded);
