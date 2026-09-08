"""Migrations that changed DATA, replayed after loading an older archive.

A migration only ever runs once per database. When the target's schema is newer than the
archive, the DDL is already in place but the backfill those migrations performed never touched
the rows we are loading now -- so it has to be repeated here.

Every file in backend/migrations must be listed in FIXUPS or NO_FIXUP_NEEDED; a unit test
enforces that, so the next data-changing migration cannot be forgotten.
"""

import logging

log = logging.getLogger(__name__)


def _fix_002(cur):
    # 002 forced is_transfer/is_excluded for transfer-kind categories.
    cur.execute("""
        UPDATE transactions t SET is_transfer = TRUE, is_excluded = TRUE
          FROM categories c
         WHERE c.id = t.category_id AND c.kind = 'transfer'
           AND NOT (t.is_transfer AND t.is_excluded)""")


def _fix_005(cur):
    # 005 narrowed 002 to confirmed rows only, and cleared the flags it had set on suggestions.
    cur.execute("""
        UPDATE transactions t SET is_transfer = FALSE, is_excluded = FALSE
          FROM categories c
         WHERE c.id = t.category_id AND c.kind = 'transfer'
           AND t.category_status <> 'confirmed'
           AND (t.is_transfer OR t.is_excluded)""")


FIXUPS = {
    "002_transfer_kind_trigger.sql": _fix_002,
    "005_transfer_kind_requires_confirmed.sql": _fix_005,
}

# Schema-only, or data changes that a fresh load reproduces on its own.
NO_FIXUP_NEEDED = {
    "001_init.sql",
    "003_audit_log_user_fk.sql",
    "004_import_rows_source.sql",
    "006_indexes_and_trgm.sql",
    "007_budgets.sql",
    "008_tags.sql",
    "009_transaction_splits.sql",
    "010_user_status.sql",
    "015_backup_jobs.sql",
}


def run(cur, missing_migrations):
    applied = []
    for name in sorted(missing_migrations):
        fn = FIXUPS.get(name)
        if fn is None:
            if name not in NO_FIXUP_NEEDED:
                log.warning("no backup fixup registered for migration %s", name)
            continue
        fn(cur)
        applied.append(name)
    return applied
