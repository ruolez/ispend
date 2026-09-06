-- Remember how a preview row was categorized (rule / merchant memory / builtin) so the import
-- preview can show what the system already knows.
ALTER TABLE import_rows ADD COLUMN IF NOT EXISTS category_source TEXT;
ALTER TABLE import_rows ADD COLUMN IF NOT EXISTS category_rule_id INT;
