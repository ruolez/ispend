"""Load an archive over this installation.

The whole database load runs inside ONE transaction, so a failure anywhere leaves the existing
data untouched.

Trigger strategy: exactly one trigger is disabled by name (transactions_transfer_kind, which
rewrites is_transfer/is_excluded on insert). That needs table ownership, not superuser. Every
foreign key stays enforced and the deferred transaction_splits_sum triggers fire at COMMIT, so
the load is its own integrity proof. `fast` swaps in session_replication_role = replica for
large datasets; it needs superuser and is validated afterwards by generated anti-joins.
"""

import hashlib
import logging
import os
import secrets

import config
import db

from . import archive, compat, export, fixups, tables

log = logging.getLogger(__name__)

SENTINEL = ".restore-in-progress"


def sentinel_path():
    return os.path.join(config.STATEMENTS_DIR, "_backups", SENTINEL)


def restore_in_progress():
    try:
        return os.path.exists(sentinel_path())
    except OSError:
        return False


def _is_superuser(cur):
    cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    row = cur.fetchone()
    return bool(row and row[0])


def _validate_splits(cur):
    cur.execute("""
        SELECT s.transaction_id
          FROM transaction_splits s JOIN transactions t ON t.id = s.transaction_id
         GROUP BY s.transaction_id, t.amount
        HAVING COUNT(*) < 2 OR SUM(s.amount) <> t.amount
            OR COUNT(*) FILTER (WHERE sign(s.amount) <> sign(t.amount)) > 0
         LIMIT 1""")
    bad = cur.fetchone()
    if bad:
        raise archive.ArchiveError(
            f"The archive's split lines for transaction {bad[0]} do not add up to its amount.")


def _validate_foreign_keys(cur):
    """Only needed after the replica-mode fast path, which skips FK triggers entirely.
    ALTER TABLE ... VALIDATE CONSTRAINT is a no-op on an already-valid constraint, so the
    references have to be re-proved with an anti-join per constraint."""
    cur.execute("""
        SELECT c.conname,
               child.relname, ARRAY(SELECT a.attname FROM unnest(c.conkey) k
                                      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k),
               parent.relname, ARRAY(SELECT a.attname FROM unnest(c.confkey) k
                                       JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k)
          FROM pg_constraint c
          JOIN pg_class child ON child.oid = c.conrelid
          JOIN pg_class parent ON parent.oid = c.confrelid
         WHERE c.contype = 'f' AND c.connamespace = current_schema()::regnamespace""")
    for name, child, child_cols, parent, parent_cols in cur.fetchall():
        on = " AND ".join(f'p."{p}" = c."{k}"' for k, p in zip(child_cols, parent_cols, strict=True))
        notnull = " AND ".join(f'c."{k}" IS NOT NULL' for k in child_cols)
        cur.execute(f'SELECT 1 FROM {child} c LEFT JOIN {parent} p ON {on} '
                    f'WHERE {notnull} AND p."{parent_cols[0]}" IS NULL LIMIT 1')
        if cur.fetchone():
            raise archive.ArchiveError(
                f"The archive is inconsistent: {child} has rows violating {name}.")


def _restore_sequences(cur):
    """Without this the next INSERT collides with a restored id. pg_get_serial_sequence avoids
    hardcoding <table>_id_seq."""
    for table in tables.TABLE_ORDER:
        if not tables.has_sequence(table):
            continue
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence(%s, 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)", (table,))


# No seed-category guard here on purpose. seed_categories.seed_for_profile already stamps
# profiles.preferences.categories_seeded when it seeds, so a user who deleted their taxonomy is already
# skipped by db._seed_categories. Stamping category-less users during a restore would instead
# deny the default taxonomy to an account that had simply never been seeded yet, and would make
# the restored rows differ from the archive.


def _rotate_session_epoch(cur):
    """SECRET_KEY is unchanged, so a cookie carrying user_id 1 would otherwise bind to whoever
    is id 1 in the restored data. Rotating the global epoch kills every cookie everywhere --
    including any copied off the old server."""
    cur.execute(
        """INSERT INTO settings (key, value) VALUES ('session_epoch', %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (secrets.token_urlsafe(16),))


def _load_table(cur, arc, table, columns, sha, mode, fill_profile=False):
    collist = ", ".join(f'"{c}"' for c in columns)
    self_fk = tables.SELF_FK.get(table)
    two_pass = mode != "replica" and self_fk is not None and self_fk in columns
    with arc.table_reader(table, sha) as reader:
        if not (two_pass or fill_profile):
            cur.copy_expert(f"COPY {table} ({collist}) FROM STDIN", reader)
            return
        # Staged: a self-FK cannot point at a row that is not inserted yet, and an archive older
        # than profiles has no profile_id, which comes from the owner's profile instead.
        cur.execute(f"CREATE TEMP TABLE _stg (LIKE {table}) ON COMMIT DROP")
        if fill_profile:
            cur.execute("ALTER TABLE _stg ALTER COLUMN profile_id DROP NOT NULL")
        cur.copy_expert(f"COPY _stg ({collist}) FROM STDIN", reader)
        names = list(columns)
        values = ["NULL" if two_pass and c == self_fk else f'"{c}"' for c in columns]
        if fill_profile:
            names.append("profile_id")
            values.append("(SELECT p.id FROM profiles p WHERE p.user_id = _stg.user_id ORDER BY p.sort_order, p.id LIMIT 1) AS profile_id")
        quoted = ", ".join(f'"{c}"' for c in names)
        cur.execute(f"INSERT INTO {table} ({quoted}) SELECT {', '.join(values)} FROM _stg ORDER BY {tables.order_by(table)}")
        if two_pass:
            cur.execute(f'UPDATE {table} t SET "{self_fk}" = s."{self_fk}" FROM _stg s '
                        f'WHERE s.id = t.id AND s."{self_fk}" IS NOT NULL')
        cur.execute("DROP TABLE _stg")


def _synthesize_profiles(cur):
    """An archive from before profiles: every account gets one Personal profile carrying its
    per-book preferences. Accounts left with an unused one are pruned by fixups._fix_030."""
    cur.execute("""
        INSERT INTO profiles (user_id, name, kind, color, preferences)
        SELECT u.id, 'Personal', 'personal', 'c1',
               jsonb_strip_nulls(jsonb_build_object(
                 'default_account_id', u.preferences->'default_account_id',
                 'saved_views', u.preferences->'saved_views',
                 'review_skips', u.preferences->'review_skips',
                 'onboarding', u.preferences->'onboarding',
                 'categories_seeded', u.preferences->'categories_seeded'))
          FROM users u ORDER BY u.id""")


def restore_files(arc, delete_extra=False):
    """Rehydrate /data/statements. Mismatches are warnings, never failures: a missing file is
    already handled gracefully by the statement download endpoint."""
    warnings = []
    root = config.STATEMENTS_DIR
    wanted = set()
    for entry in arc.files_index():
        rel = entry["path"]
        wanted.add(rel)
        arcname = archive.FILES_PREFIX + rel
        dest = os.path.join(root, rel)
        if not arc.has_file(arcname):
            warnings.append({"kind": "file_missing", "path": rel})
            continue
        if os.path.isfile(dest) and _sha256_file(dest) == entry["sha256"]:
            continue  # already correct: re-running a restore is a no-op
        os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
        tmp = dest + ".part"
        digest = hashlib.sha256()
        with arc.open_file(arcname) as src, open(tmp, "wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            os.unlink(tmp)
            warnings.append({"kind": "file_corrupt", "path": rel})
            continue
        os.chmod(tmp, 0o640)
        os.replace(tmp, dest)

    extra = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                if not (entry.is_dir(follow_symlinks=False) and entry.name.isdigit()):
                    continue  # skips _backups, which is not a user directory
                with os.scandir(entry.path) as inner:
                    for f in inner:
                        rel = f"{entry.name}/{f.name}"
                        if f.is_file(follow_symlinks=False) and rel not in wanted:
                            extra.append(rel)
    except OSError as e:
        warnings.append({"kind": "scan_failed", "detail": str(e)})

    if extra:
        if delete_extra:
            for rel in extra:
                try:
                    os.unlink(os.path.join(root, rel))
                except OSError:
                    pass
            warnings.append({"kind": "extra_files_deleted", "count": len(extra)})
        else:
            warnings.append({"kind": "extra_files_kept", "count": len(extra),
                             "detail": "Files on disk that the archive does not reference were left alone."})
    return warnings


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect(path):
    """Read the manifest and say what a restore would do. Mutates nothing."""
    with archive.ArchiveReader(path) as arc:
        conn = db.get_db()
        with conn.cursor() as cur:
            live_migrations = export._live_migrations(cur)
            cols = export.live_columns(cur)
            problems = []
            try:
                plan = compat.check(arc.manifest, live_migrations, cols)
                warnings = plan.warnings
                missing = plan.missing_migrations
            except archive.ArchiveError as e:
                problems.append(str(e))
                warnings, missing = [], []
            will_replace = {}
            for entry in arc.manifest.get("tables", []):
                table = entry["name"]
                if table not in tables.TABLE_ORDER:
                    continue
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                will_replace[table] = {"live": cur.fetchone()[0], "archive": entry["rows"]}
        return {
            "manifest": arc.manifest,
            "compatible": not problems,
            "problems": problems,
            "warnings": warnings,
            "missing_migrations": missing,
            "will_replace": will_replace,
            "files": arc.manifest.get("files", {}),
        }


def restore_archive(path, progress=None, delete_extra_files=False, fast=False):
    """Replace this installation's data with the archive's. Returns a warnings list."""
    os.makedirs(os.path.dirname(sentinel_path()), mode=0o700, exist_ok=True)
    with open(sentinel_path(), "w") as fh:
        fh.write("1")
    try:
        with archive.ArchiveReader(path) as arc:
            conn = db.get_db()
            with conn.cursor() as cur:
                plan = compat.check(arc.manifest, export._live_migrations(cur),
                                    export.live_columns(cur))
                mode = "replica" if (fast and _is_superuser(cur)) else "owner"
                shas = {t["name"]: t["sha256"] for t in arc.manifest["tables"]}

                if progress:
                    progress.phase("database", 0.05, f"Loading tables ({mode} mode)")
                with db.transaction():
                    if mode == "replica":
                        cur.execute("SET session_replication_role = replica")
                    else:
                        for table, trigger in tables.REWRITING_TRIGGERS:
                            cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")

                    cur.execute("TRUNCATE " + ", ".join(tables.truncate_order()) + " RESTART IDENTITY")

                    loaded = []
                    live = export.live_columns(cur)
                    for i, table in enumerate(tables.TABLE_ORDER):
                        if table in tables.SYNTHESIZED and table not in plan.columns:
                            _synthesize_profiles(cur)
                            continue
                        if table not in plan.columns or not arc.has_table(table):
                            continue
                        if progress:
                            progress.phase("database", 0.05 + 0.5 * (i / len(tables.TABLE_ORDER)),
                                           f"Loading {table}")
                        fill = [c for c in tables.FILLED_ON_LOAD
                                if c in live.get(table, {}) and c not in plan.columns[table]]
                        _load_table(cur, arc, table, plan.columns[table], shas.get(table), mode, fill_profile=bool(fill))
                        loaded.append(table)

                    _restore_sequences(cur)
                    fixups.run(cur, plan.missing_migrations)
                    _rotate_session_epoch(cur)
                    # Computed from the data that was just replaced.
                    cur.execute("DELETE FROM metric_cache")

                    if mode == "replica":
                        cur.execute("SET session_replication_role = origin")
                        _validate_foreign_keys(cur)
                    else:
                        for table, trigger in tables.REWRITING_TRIGGERS:
                            cur.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
                    # Raise a readable error before COMMIT fires the deferred constraint trigger.
                    _validate_splits(cur)
                # COMMIT happened here: the deferred split triggers have now passed too.

            if progress:
                progress.phase("files", 0.6, "Restoring uploaded statements")
            warnings = list(plan.warnings) + restore_files(arc, delete_extra=delete_extra_files)
            return warnings
    finally:
        try:
            os.unlink(sentinel_path())
        except OSError:
            pass


def clear_stale_sentinel():
    """Called at startup: a process killed mid-restore would otherwise leave the app in
    maintenance mode forever."""
    try:
        if os.path.exists(sentinel_path()):
            os.unlink(sentinel_path())
            log.warning("cleared a stale restore sentinel left by an interrupted run")
    except OSError:
        pass
