"""Build the archive.

Uses COPY ... TO STDOUT rather than pg_dump: the runtime image ships no postgresql-client, and
Debian's client 15 would refuse to dump a v16 server anyway. COPY text format is exactly what
pg_dump's data section contains, so NUMERIC round-trips as exact decimal text (the split
trigger compares sums with <>), TEXT[] and JSONB go through their own parsers, and NULL stays
distinct from an empty string.
"""

import datetime
import logging
import os

import config
import db

from . import archive, tables

log = logging.getLogger(__name__)


def _live_migrations(cur):
    cur.execute("SELECT filename FROM schema_migrations ORDER BY filename")
    return [r[0] for r in cur.fetchall()]


def live_columns(cur):
    """{table: {column: {nullable, has_default}}} for every table we care about."""
    cur.execute(
        """SELECT table_name, column_name, is_nullable = 'YES' AS nullable,
                  (column_default IS NOT NULL OR is_identity = 'YES'
                   OR is_generated = 'ALWAYS') AS has_default
             FROM information_schema.columns
            WHERE table_schema = current_schema()
            ORDER BY table_name, ordinal_position""")
    out = {}
    for table, column, nullable, has_default in cur.fetchall():
        out.setdefault(table, {})[column] = {"nullable": nullable, "has_default": has_default}
    return out


def _exportable_columns(cur, table):
    """Generated columns cannot be written back, so they are never exported."""
    cur.execute(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = %s
              AND is_generated <> 'ALWAYS'
            ORDER BY ordinal_position""", (table,))
    return [r[0] for r in cur.fetchall()]


def _statement_files(cur):
    """Derived from the DB, not the filesystem. Files are content-addressed per user, so one
    blob can back several statements rows; the archive stores it once and the restored rows
    carry the reference count."""
    cur.execute("""
        SELECT DISTINCT rel FROM (
            SELECT stored_path AS rel FROM statements WHERE stored_path IS NOT NULL
            UNION
            SELECT ocr_path FROM statements WHERE ocr_path IS NOT NULL) s
        ORDER BY rel""")
    return [r[0] for r in cur.fetchall()]


def export_archive(out_path, progress=None, include_files=True):
    """Write a complete archive to out_path. Returns the manifest."""
    conn = db.get_db()
    manifest_tables, warnings = [], []
    files_index = []
    total_file_bytes = 0

    with conn.cursor() as cur, archive.ArchiveWriter(out_path) as arc:
        migrations = _live_migrations(cur)
        cur.execute("SHOW server_version")
        server_version = cur.fetchone()[0]

        for i, table in enumerate(tables.TABLE_ORDER):
            if progress:
                progress.phase("database", 0.05 + 0.45 * (i / len(tables.TABLE_ORDER)),
                               f"Exporting {table}")
            columns = _exportable_columns(cur, table)
            collist = ", ".join(f'"{c}"' for c in columns)
            sql = (f"COPY (SELECT {collist} FROM {table} ORDER BY {tables.order_by(table)}) "
                   "TO STDOUT")
            with arc.table_writer(table) as writer:
                cur.copy_expert(sql, writer)
                manifest_tables.append({
                    "name": table, "columns": columns,
                    "rows": writer.rows, "bytes": writer.bytes, "sha256": writer.sha256,
                })

        if include_files:
            rels = _statement_files(cur)
            for i, rel in enumerate(rels):
                if progress and not i % 5:
                    progress.phase("files", 0.5 + 0.45 * (i / max(len(rels), 1)),
                                   f"Copying files ({i}/{len(rels)})")
                path = os.path.join(config.STATEMENTS_DIR, rel)
                if not os.path.isfile(path):
                    # Already handled gracefully at runtime by the statement download endpoint.
                    warnings.append({"kind": "file_missing", "path": rel})
                    continue
                try:
                    with open(path, "rb") as fh:
                        sha, size = arc.add_file(archive.FILES_PREFIX + rel, fh)
                except OSError as e:
                    warnings.append({"kind": "file_unreadable", "path": rel, "detail": str(e)})
                    continue
                files_index.append({"path": rel, "sha256": sha, "bytes": size})
                total_file_bytes += size

        arc.add_json(archive.FILES_INDEX, files_index)
        manifest = {
            "format": archive.FORMAT,
            "format_version": archive.FORMAT_VERSION,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "app": {"migrations": migrations, "migration_head": migrations[-1] if migrations else None},
            "source": {"hostname": os.uname().nodename, "postgres_version": server_version,
                       "app_timezone": config.APP_TIMEZONE},
            "contents": {"secrets_included": True, "encrypted": False, "env_included": False},
            "tables": manifest_tables,
            "files": {"count": len(files_index), "bytes": total_file_bytes},
            "warnings": warnings,
        }
        # Written last: a build that dies leaves an archive with no manifest, which the reader
        # refuses, rather than one that looks complete but is truncated.
        arc.add_json(archive.MANIFEST, manifest)
    return manifest
