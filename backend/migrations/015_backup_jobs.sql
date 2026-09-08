-- Tracks backup and restore runs so the admin UI can poll progress and re-download an artifact
-- after a page reload.
--
-- This table is INFRASTRUCTURE: it is never exported into a backup and never truncated by a
-- restore. created_by is deliberately NOT a foreign key to users(id) -- a restore truncates
-- users, and an FK here would let TRUNCATE ... CASCADE destroy the row of the very restore job
-- that is running.
CREATE TABLE backup_jobs (
  id SERIAL PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('backup', 'restore', 'pre_restore')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'error')),
  phase TEXT,
  progress NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 1),
  message TEXT,
  error_message TEXT,
  filename TEXT,
  file_path TEXT,
  size_bytes BIGINT,
  manifest JSONB,
  stats JSONB NOT NULL DEFAULT '{}'::jsonb,
  warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Handed to the browser once when a restore starts: the admin's own session dies mid-restore
  -- (their users row is replaced), so polling has to survive being signed out.
  token TEXT,
  created_by INT,
  created_by_username TEXT,
  parent_job_id INT REFERENCES backup_jobs(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX idx_backup_jobs_created ON backup_jobs (created_at DESC);
CREATE INDEX idx_backup_jobs_active ON backup_jobs (id) WHERE status IN ('queued', 'running');
