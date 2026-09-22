-- A column layout is remembered per account: the same export can need different settings
-- (sign flip) on a card than on a checking account.
ALTER TABLE import_layouts DROP CONSTRAINT IF EXISTS import_layouts_user_id_layout_key_key;
ALTER TABLE import_layouts
  ADD CONSTRAINT import_layouts_user_layout_account_key UNIQUE NULLS NOT DISTINCT (user_id, layout_key, account_id);

-- SET NULL could collide with a row that already has no account and block deleting the account.
ALTER TABLE import_layouts DROP CONSTRAINT IF EXISTS import_layouts_account_id_fkey;
ALTER TABLE import_layouts
  ADD CONSTRAINT import_layouts_account_id_fkey FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE;
