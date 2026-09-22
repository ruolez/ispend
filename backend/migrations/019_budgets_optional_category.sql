-- A budget with no category is the month's global limit on all spending: one such row per (user, month).
-- NULLS NOT DISTINCT keeps ON CONFLICT (user_id, category_id, month) working for that row.
ALTER TABLE budgets ALTER COLUMN category_id DROP NOT NULL;
ALTER TABLE budgets DROP CONSTRAINT IF EXISTS budgets_user_id_category_id_month_key;
ALTER TABLE budgets
  ADD CONSTRAINT budgets_user_id_category_id_month_key UNIQUE NULLS NOT DISTINCT (user_id, category_id, month);
