"""Job progress on its own autocommit connection.

The restore loads every table inside one db.transaction(); an UPDATE on that connection would
be invisible to anything polling until the whole thing commits, which is exactly when progress
stops being useful.
"""

import json
import logging

import db

log = logging.getLogger(__name__)


class Progress:
    def __init__(self, job_id):
        self.job_id = job_id
        self._conn = db.connect()
        self._conn.autocommit = True

    def _set(self, sql, params):
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
        except Exception:  # progress must never take the job down with it
            log.warning("could not write backup job progress", exc_info=True)

    def phase(self, phase, fraction=None, message=None):
        self._set(
            """UPDATE backup_jobs
                  SET status = 'running', phase = %s, message = COALESCE(%s, message),
                      progress = COALESCE(%s, progress), updated_at = now()
                WHERE id = %s""",
            (phase, message, fraction, self.job_id))

    def fraction(self, value):
        self._set("UPDATE backup_jobs SET progress = %s, updated_at = now() WHERE id = %s",
                  (max(0.0, min(1.0, value)), self.job_id))

    def done(self, *, filename=None, file_path=None, size_bytes=None, manifest=None,
             stats=None, warnings=None):
        self._set(
            """UPDATE backup_jobs
                  SET status = 'done', phase = 'done', progress = 1, updated_at = now(),
                      finished_at = now(), filename = COALESCE(%s, filename),
                      file_path = COALESCE(%s, file_path), size_bytes = COALESCE(%s, size_bytes),
                      manifest = COALESCE(%s::jsonb, manifest), stats = COALESCE(%s::jsonb, stats),
                      warnings = COALESCE(%s::jsonb, warnings)
                WHERE id = %s""",
            (filename, file_path, size_bytes,
             json.dumps(manifest, default=str) if manifest is not None else None,
             json.dumps(stats, default=str) if stats is not None else None,
             json.dumps(warnings, default=str) if warnings is not None else None,
             self.job_id))

    def failed(self, message):
        self._set(
            """UPDATE backup_jobs SET status = 'error', error_message = %s, updated_at = now(),
                      finished_at = now() WHERE id = %s""",
            (str(message)[:2000], self.job_id))

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
