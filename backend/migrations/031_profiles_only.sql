-- Profiles, second step: every query and insert names its profile now, so the per-user keys and
-- the transitional insert trigger from 030 go away, and the preferences that moved to the profile
-- leave users.preferences.

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['accounts','categories','rules','statements','transactions','merchant_memory',
                           'recurring_dismissals','insights','budgets','tags','import_layouts'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', t || '_fill_profile', t);
  END LOOP;
END
$$;
DROP FUNCTION IF EXISTS trg_fill_profile_id();

ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_user_id_name_key;
DROP INDEX IF EXISTS uq_categories_user_slug;
DROP INDEX IF EXISTS uq_categories_user_parent_name;
DROP INDEX IF EXISTS idx_categories_user_parent;
DROP INDEX IF EXISTS idx_rules_user_priority;
DROP INDEX IF EXISTS idx_statements_user_created;
DROP INDEX IF EXISTS idx_statements_user_sha;
DROP INDEX IF EXISTS idx_txn_user_date;
DROP INDEX IF EXISTS idx_txn_user_account_date;
DROP INDEX IF EXISTS idx_txn_user_category;
DROP INDEX IF EXISTS idx_txn_user_merchant;
DROP INDEX IF EXISTS idx_txn_uncategorized;
DROP INDEX IF EXISTS idx_txn_suggested;
DROP INDEX IF EXISTS idx_txn_desc_lower;
ALTER TABLE merchant_memory DROP CONSTRAINT IF EXISTS merchant_memory_user_key_direction_key;
ALTER TABLE insights DROP CONSTRAINT IF EXISTS insights_user_id_period_start_period_end_key;
ALTER TABLE budgets DROP CONSTRAINT IF EXISTS budgets_user_id_category_id_month_key;
DROP INDEX IF EXISTS idx_budgets_user_month;
DROP INDEX IF EXISTS uq_tags_user_name;
ALTER TABLE import_layouts DROP CONSTRAINT IF EXISTS import_layouts_user_layout_account_key;

ALTER TABLE recurring_dismissals DROP CONSTRAINT recurring_dismissals_pkey;
ALTER TABLE recurring_dismissals DROP CONSTRAINT recurring_dismissals_profile_key;
ALTER TABLE recurring_dismissals ADD PRIMARY KEY (profile_id, merchant_key);

-- Per-person lookups that stay: the admin console counts a person's transactions and statements,
-- and deleting a person cascades by user_id.
CREATE INDEX IF NOT EXISTS idx_txn_user ON transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_statements_user ON statements (user_id);

-- backfill: backup/fixups.py replays this on archives older than this migration.
UPDATE users SET preferences = preferences - 'default_account_id' - 'saved_views' - 'review_skips' - 'onboarding'
                                           - 'categories_seeded'
 WHERE preferences ?| ARRAY['default_account_id', 'saved_views', 'review_skips', 'onboarding', 'categories_seeded'];
