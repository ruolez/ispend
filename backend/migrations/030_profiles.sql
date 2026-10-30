-- Profiles: a user keeps several independent sets of books (e.g. Personal, Business). Every
-- finance row belongs to exactly one profile; user_id stays on the rows as the owner (privacy,
-- admin counts, purge and the statement file path all work per user).
--
-- Cross-profile references are impossible: accounts, categories and statements are keyed by
-- (id, profile_id) and everything that points at them carries its own profile_id in the key.
-- Those foreign keys are DEFERRABLE INITIALLY IMMEDIATE so that moving an account between
-- profiles can update both sides inside one transaction.

CREATE TABLE profiles (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
  kind TEXT NOT NULL DEFAULT 'personal' CHECK (kind IN ('personal','business')),
  color TEXT NOT NULL DEFAULT 'c1',
  sort_order INT NOT NULL DEFAULT 0,
  preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT profiles_id_user_key UNIQUE (id, user_id)
);
CREATE UNIQUE INDEX uq_profiles_user_name ON profiles (user_id, lower(name));
CREATE INDEX idx_profiles_user ON profiles (user_id, sort_order, id);

-- The profile a user last worked in. Not a foreign key: users is restored before profiles.
ALTER TABLE users ADD COLUMN last_profile_id INT;

-- backfill: backup/fixups.py replays the preference copy on archives older than this migration.
-- One Personal profile for every customer, and for any other account that owns finance rows
-- (admin accounts of old installs may still hold some).
INSERT INTO profiles (user_id, name, kind, color, preferences)
SELECT u.id, 'Personal', 'personal', 'c1',
       jsonb_strip_nulls(jsonb_build_object(
         'default_account_id', u.preferences->'default_account_id',
         'saved_views', u.preferences->'saved_views',
         'review_skips', u.preferences->'review_skips',
         'onboarding', u.preferences->'onboarding',
         'categories_seeded', u.preferences->'categories_seeded'))
FROM users u
WHERE u.role = 'user'
   OR u.id IN (SELECT user_id FROM accounts UNION SELECT user_id FROM categories
               UNION SELECT user_id FROM rules UNION SELECT user_id FROM statements
               UNION SELECT user_id FROM transactions UNION SELECT user_id FROM merchant_memory
               UNION SELECT user_id FROM recurring_dismissals UNION SELECT user_id FROM insights
               UNION SELECT user_id FROM budgets UNION SELECT user_id FROM tags
               UNION SELECT user_id FROM import_layouts);
-- end backfill

-- profile_id on every top-level finance table, filled from the owner's profile.
DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['accounts','categories','rules','statements','transactions','merchant_memory',
                           'recurring_dismissals','insights','budgets','tags','import_layouts'] LOOP
    EXECUTE format('ALTER TABLE %I ADD COLUMN profile_id INT', t);
    EXECUTE format('UPDATE %I x SET profile_id = p.id FROM profiles p WHERE p.user_id = x.user_id', t);
    EXECUTE format('ALTER TABLE %I ALTER COLUMN profile_id SET NOT NULL', t);
    -- The profile must belong to the row's owner.
    EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (profile_id, user_id) '
                   'REFERENCES profiles(id, user_id) ON DELETE CASCADE', t, t || '_profile_fkey');
  END LOOP;
END
$$;

-- Keys the composite foreign keys point at.
ALTER TABLE accounts ADD CONSTRAINT accounts_id_profile_key UNIQUE (id, profile_id);
ALTER TABLE categories ADD CONSTRAINT categories_id_profile_key UNIQUE (id, profile_id);
ALTER TABLE statements ADD CONSTRAINT statements_id_profile_key UNIQUE (id, profile_id);

-- Same-profile references, replacing the single-column foreign keys with the same actions.
ALTER TABLE categories DROP CONSTRAINT categories_parent_id_fkey;
ALTER TABLE categories ADD CONSTRAINT fk_category_parent FOREIGN KEY (parent_id, profile_id)
  REFERENCES categories(id, profile_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE rules DROP CONSTRAINT rules_account_id_fkey;
ALTER TABLE rules ADD CONSTRAINT fk_rule_account FOREIGN KEY (account_id, profile_id)
  REFERENCES accounts(id, profile_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE rules DROP CONSTRAINT rules_category_id_fkey;
ALTER TABLE rules ADD CONSTRAINT fk_rule_category FOREIGN KEY (category_id, profile_id)
  REFERENCES categories(id, profile_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE statements DROP CONSTRAINT statements_account_id_fkey;
ALTER TABLE statements ADD CONSTRAINT fk_stmt_account FOREIGN KEY (account_id, profile_id)
  REFERENCES accounts(id, profile_id) ON DELETE SET NULL (account_id) DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE transactions DROP CONSTRAINT transactions_account_id_fkey;
ALTER TABLE transactions ADD CONSTRAINT fk_txn_account FOREIGN KEY (account_id, profile_id)
  REFERENCES accounts(id, profile_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE transactions DROP CONSTRAINT transactions_category_id_fkey;
ALTER TABLE transactions ADD CONSTRAINT fk_txn_category FOREIGN KEY (category_id, profile_id)
  REFERENCES categories(id, profile_id) ON DELETE SET NULL (category_id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE transactions DROP CONSTRAINT transactions_statement_id_fkey;
ALTER TABLE transactions ADD CONSTRAINT fk_txn_statement FOREIGN KEY (statement_id, profile_id)
  REFERENCES statements(id, profile_id) ON DELETE SET NULL (statement_id) DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE budgets DROP CONSTRAINT budgets_category_id_fkey;
ALTER TABLE budgets ADD CONSTRAINT fk_budget_category FOREIGN KEY (category_id, profile_id)
  REFERENCES categories(id, profile_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE merchant_memory DROP CONSTRAINT merchant_memory_category_id_fkey;
ALTER TABLE merchant_memory ADD CONSTRAINT fk_mm_category FOREIGN KEY (category_id, profile_id)
  REFERENCES categories(id, profile_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE import_layouts DROP CONSTRAINT import_layouts_account_id_fkey;
ALTER TABLE import_layouts ADD CONSTRAINT fk_layout_account FOREIGN KEY (account_id, profile_id)
  REFERENCES accounts(id, profile_id) ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- Per-profile keys and indexes. The per-user ones they replace are dropped by the next migration,
-- once no query relies on them.
ALTER TABLE accounts ADD CONSTRAINT accounts_profile_name_key UNIQUE (profile_id, name);
CREATE UNIQUE INDEX uq_categories_profile_slug ON categories (profile_id, slug);
CREATE UNIQUE INDEX uq_categories_profile_parent_name ON categories (profile_id, COALESCE(parent_id, 0), lower(name));
CREATE INDEX idx_categories_profile_parent ON categories (profile_id, parent_id, sort_order);
CREATE INDEX idx_rules_profile_priority ON rules (profile_id, priority, id);
CREATE INDEX idx_statements_profile_created ON statements (profile_id, created_at DESC);
CREATE INDEX idx_statements_profile_sha ON statements (profile_id, file_sha256);
CREATE INDEX idx_txn_profile_date ON transactions (profile_id, txn_date DESC, id DESC);
CREATE INDEX idx_txn_profile_account_date ON transactions (profile_id, account_id, txn_date);
CREATE INDEX idx_txn_profile_category ON transactions (profile_id, category_id);
CREATE INDEX idx_txn_profile_merchant ON transactions (profile_id, merchant_key);
CREATE INDEX idx_txn_profile_uncategorized ON transactions (profile_id, txn_date DESC) WHERE category_id IS NULL;
CREATE INDEX idx_txn_profile_suggested ON transactions (profile_id) WHERE category_status = 'suggested';
CREATE INDEX idx_txn_profile_desc_lower ON transactions (profile_id, lower(description_clean));
ALTER TABLE merchant_memory ADD CONSTRAINT merchant_memory_profile_key_direction_key
  UNIQUE (profile_id, merchant_key, direction);
ALTER TABLE recurring_dismissals ADD CONSTRAINT recurring_dismissals_profile_key UNIQUE (profile_id, merchant_key);
ALTER TABLE insights ADD CONSTRAINT insights_profile_period_key UNIQUE (profile_id, period_start, period_end);
ALTER TABLE budgets ADD CONSTRAINT budgets_profile_category_month_key
  UNIQUE NULLS NOT DISTINCT (profile_id, category_id, month);
CREATE INDEX idx_budgets_profile_month ON budgets (profile_id, month);
CREATE UNIQUE INDEX uq_tags_profile_name ON tags (profile_id, lower(name));
ALTER TABLE import_layouts ADD CONSTRAINT import_layouts_profile_layout_account_key
  UNIQUE NULLS NOT DISTINCT (profile_id, layout_key, account_id);

-- Transitional: an INSERT that does not name a profile yet lands in the owner's first profile.
-- Removed by the next migration, when every insert names its profile.
CREATE FUNCTION trg_fill_profile_id() RETURNS trigger AS $$
BEGIN
  IF NEW.profile_id IS NULL THEN
    SELECT id INTO NEW.profile_id FROM profiles WHERE user_id = NEW.user_id ORDER BY sort_order, id LIMIT 1;
    IF NEW.profile_id IS NULL THEN
      INSERT INTO profiles (user_id, name) VALUES (NEW.user_id, 'Personal') RETURNING id INTO NEW.profile_id;
    END IF;
  END IF;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['accounts','categories','rules','statements','transactions','merchant_memory',
                           'recurring_dismissals','insights','budgets','tags','import_layouts'] LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION trg_fill_profile_id()',
                   t || '_fill_profile', t);
  END LOOP;
END
$$;

-- Moving accounts between profiles maps each row onto the target profile's categories and must
-- keep is_transfer / is_excluded exactly as they were; the move sets this flag with SET LOCAL.
CREATE OR REPLACE FUNCTION trg_transactions_transfer_kind() RETURNS trigger AS $$
DECLARE
  new_kind TEXT;
  old_kind TEXT;
BEGIN
  IF current_setting('ispend.keep_transfer_flags', true) = 'on' THEN
    RETURN NEW;
  END IF;
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
