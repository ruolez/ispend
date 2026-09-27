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


def _fix_021(cur):
    # 021 filled target_user_id / by_admin on the activity log rows that existed before it.
    cur.execute("""
        UPDATE audit_log a SET target_user_id = (a.detail->>'id')::int, by_admin = true
         WHERE a.target_user_id IS NULL
           AND a.action ~ '^(user\\.(create|update|password_reset|lock|unlock|restore|delete)|billing\\.(comp|extend_trial|extend_grace|cancel))$'
           AND a.detail->>'id' ~ '^\\d{1,9}$'
           AND EXISTS (SELECT 1 FROM users u WHERE u.id = (a.detail->>'id')::int)""")
    cur.execute("""
        UPDATE audit_log SET by_admin = true
         WHERE NOT by_admin
           AND (action LIKE 'admin.%'
                OR action IN ('user.create', 'user.purge', 'settings.admin_update',
                              'billing.config_update', 'billing.sync_stale'))""")


def _fix_022(cur):
    # 022 gave every existing subscription a starting point in the revenue ledger.
    cur.execute("""
        INSERT INTO subscription_events (user_id, source, kind, status_to, plan_to, occurred_at, detail)
        SELECT s.user_id, 'system', 'baseline', s.status, s.plan, COALESCE(s.updated_at, s.created_at),
               jsonb_build_object('comped', s.comped_until IS NOT NULL AND s.comped_until > now())
          FROM subscriptions s
         WHERE NOT EXISTS (SELECT 1 FROM subscription_events e WHERE e.user_id = s.user_id)""")


def _fix_023(cur):
    # 023 backfilled the activation stamps, the activity days and the AI purpose from older rows.
    cur.execute("""
        UPDATE users u SET first_upload_at = COALESCE(u.first_upload_at, s.fu),
                           first_commit_at = COALESCE(u.first_commit_at, s.fc)
          FROM (SELECT user_id, MIN(created_at) AS fu, MIN(committed_at) AS fc FROM statements GROUP BY user_id) s
         WHERE s.user_id = u.id""")
    cur.execute("UPDATE users SET last_seen_at = last_login_at WHERE last_seen_at IS NULL")
    cur.execute("""
        INSERT INTO user_activity_days (user_id, day, kinds)
        SELECT user_id, d, bit_or(k)::smallint FROM (
            SELECT user_id, committed_at::date AS d, 2 AS k FROM statements WHERE committed_at IS NOT NULL
            UNION ALL
            SELECT user_id, created_at::date, 32 FROM statements
        ) s GROUP BY 1, 2
        ON CONFLICT (user_id, day) DO NOTHING""")
    cur.execute("UPDATE ai_calls SET purpose = 'extract' WHERE purpose = 'categorize' AND item_count = 0")


FIXUPS = {
    "002_transfer_kind_trigger.sql": _fix_002,
    "005_transfer_kind_requires_confirmed.sql": _fix_005,
    "021_admin_security.sql": _fix_021,
    "022_billing_ledger.sql": _fix_022,
    "023_activity_and_ops.sql": _fix_023,
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
    "011_billing_identity.sql",
    "012_subscriptions.sql",
    "013_stripe_events.sql",
    "014_auth_tokens.sql",
    "015_backup_jobs.sql",
    "016_import_layouts.sql",
    "017_import_layouts_per_account.sql",
    "018_user_verification_required.sql",
    "019_budgets_optional_category.sql",
    "020_perf_indexes.sql",
    "024_metrics.sql",
    "025_admin_crm.sql",
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
