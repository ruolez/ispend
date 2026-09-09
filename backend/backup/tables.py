"""What the archive covers, and the order it has to be loaded in.

TABLE_ORDER is written by hand rather than derived from information_schema, because the order
encodes foreign-key dependencies that no introspection would get right on its own. The
integration test asserts set(TABLE_ORDER) | EXCLUDED equals the live table set, so adding a
table without updating this file fails loudly instead of silently dropping data from backups.
"""

# schema_migrations is the target's own record of what ran there; restoring the source's rows
# would either conflict or erase the target's knowledge of its schema. backup_jobs tracks the
# restore that is running. Neither is ever exported or truncated.
EXCLUDED = {"schema_migrations", "backup_jobs"}

TABLE_ORDER = [
    # level 0 - no dependencies
    "users",
    "settings",
    # level 1 - depend on users only
    "subscriptions",
    "stripe_events",
    "auth_tokens",
    "accounts",
    "categories",            # self-FK parent_id -> two-pass
    "tags",
    "recurring_dismissals",
    "ai_calls",              # user_id nullable (ON DELETE SET NULL)
    "insights",
    "audit_log",             # user_id nullable
    # level 2 - depend on users + accounts/categories
    "rules",
    "statements",
    "budgets",
    "merchant_memory",
    # level 3
    "transactions",          # self-FK transfer_pair_id -> two-pass
    # level 4 - children of transactions
    "import_rows",
    "transaction_events",
    "anomaly_dismissals",
    "transaction_tags",
    "transaction_splits",
]

# Columns that point at the same table: inserted NULL first, wired up in a second pass.
SELF_FK = {"categories": "parent_id", "transactions": "transfer_pair_id"}

# Ordering key for the COPY, so an archive of unchanged data is byte-identical run to run.
PK = {
    "settings": "key",
    "subscriptions": "user_id",
    "stripe_events": "id",
    "recurring_dismissals": "user_id, merchant_key",
    "anomaly_dismissals": "user_id, transaction_id",
    "transaction_tags": "transaction_id, tag_id",
}
DEFAULT_PK = "id"

# Tables with a SERIAL id whose sequence must be advanced after an id-preserving restore.
NO_SEQUENCE = {"settings", "subscriptions", "stripe_events",
               "recurring_dismissals", "anomaly_dismissals", "transaction_tags"}

# The one trigger that rewrites values on insert. Disabled during a restore (table ownership is
# enough; no superuser needed) so is_transfer/is_excluded come back exactly as they were saved
# rather than being recomputed.
REWRITING_TRIGGERS = [("transactions", "transactions_transfer_kind")]


def order_by(table):
    return PK.get(table, DEFAULT_PK)


def has_sequence(table):
    return table not in NO_SEQUENCE


def truncate_order():
    """Reverse dependency order. Listed explicitly and WITHOUT CASCADE, so a future table that
    gains an FK into this set errors out instead of being silently wiped."""
    return list(reversed(TABLE_ORDER))
