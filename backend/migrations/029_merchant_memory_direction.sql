-- Merchant memory is kept per direction: the same source can pay you (money in) and charge you
-- (money out), and each side keeps its own category. direction = 'in' when amount > 0.
ALTER TABLE merchant_memory ADD COLUMN direction TEXT NOT NULL DEFAULT 'out' CHECK (direction IN ('in','out'));
ALTER TABLE merchant_memory DROP CONSTRAINT IF EXISTS merchant_memory_user_id_merchant_key_key;
ALTER TABLE merchant_memory
  ADD CONSTRAINT merchant_memory_user_key_direction_key UNIQUE (user_id, merchant_key, direction);

-- backfill: backup/fixups.py replays everything below on archives older than this migration.
-- What the user settled on per merchant and direction: categories they picked themselves count
-- first (rows filed by memory or rules only echo an earlier choice), then the remembered category,
-- then how often and how recently it was used.
CREATE TEMP TABLE mm_choice ON COMMIT DROP AS
SELECT DISTINCT ON (h.user_id, h.merchant_key, h.direction)
       h.user_id, h.merchant_key, h.direction, h.category_id, h.n, h.last_at, h.is_transfer
FROM (SELECT t.user_id, t.merchant_key, CASE WHEN t.amount > 0 THEN 'in' ELSE 'out' END AS direction,
             t.category_id, COUNT(*) AS n,
             COUNT(*) FILTER (WHERE t.category_source IS DISTINCT FROM 'merchant'
                                AND t.category_source IS DISTINCT FROM 'rule') AS chosen,
             MAX(t.updated_at) AS last_at, BOOL_OR(t.is_transfer) AS is_transfer
      FROM transactions t
      JOIN merchant_memory m ON m.user_id = t.user_id AND m.merchant_key = t.merchant_key
      WHERE t.category_id IS NOT NULL AND t.category_status = 'confirmed'
      GROUP BY 1, 2, 3, 4) h
JOIN merchant_memory m ON m.user_id = h.user_id AND m.merchant_key = h.merchant_key
ORDER BY h.user_id, h.merchant_key, h.direction, h.chosen DESC, (h.category_id = m.category_id) DESC,
         h.n DESC, h.last_at DESC;

-- The remembered row goes where its category was chosen, else where the category's kind points.
UPDATE merchant_memory m
SET direction = COALESCE(
      (SELECT c.direction FROM mm_choice c
       WHERE c.user_id = m.user_id AND c.merchant_key = m.merchant_key AND c.category_id = m.category_id
       ORDER BY (c.direction = k.direction) DESC LIMIT 1),
      k.direction)
FROM (SELECT id, CASE WHEN kind = 'income' THEN 'in' ELSE 'out' END AS direction FROM categories) k
WHERE k.id = m.category_id;

UPDATE merchant_memory m SET category_id = c.category_id, is_transfer = c.is_transfer
FROM mm_choice c
WHERE c.user_id = m.user_id AND c.merchant_key = m.merchant_key AND c.direction = m.direction
  AND c.category_id <> m.category_id;

INSERT INTO merchant_memory (user_id, merchant_key, direction, display_name, category_id, is_transfer,
                             times_used, last_used_at)
SELECT c.user_id, c.merchant_key, c.direction, m.display_name, c.category_id, c.is_transfer, c.n, c.last_at
FROM mm_choice c
JOIN merchant_memory m ON m.user_id = c.user_id AND m.merchant_key = c.merchant_key
ON CONFLICT (user_id, merchant_key, direction) DO NOTHING;
