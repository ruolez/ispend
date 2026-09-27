-- Admin analytics storage.
--
-- metric_daily: the few numbers that cannot be recomputed later (how many people were on a trial
-- on a given day), recorded hourly for the current day by ops_tick. Backed up.
-- metric_cache: five-minute cache of computed admin pages. Operational only: never backed up, no
-- foreign keys, emptied by a restore.
CREATE TABLE IF NOT EXISTS metric_daily (
    day DATE NOT NULL,
    metric TEXT NOT NULL,
    dim TEXT NOT NULL DEFAULT '',
    value NUMERIC NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, metric, dim)
);

CREATE TABLE IF NOT EXISTS metric_cache (
    key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The old overview cache lived in settings, which is backed up and restored.
DELETE FROM settings WHERE key LIKE 'admin:stats:overview:%';
