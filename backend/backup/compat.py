"""Can this archive be loaded into this server?

Schema comes from the migrations on the target, data comes from the archive, so an admin can
restore a months-old archive into a freshly updated server. That only works if we check what
changed in between and refuse the cases we cannot honour.
"""

from .archive import ArchiveError
from .tables import EXCLUDED, TABLE_ORDER


class Plan:
    def __init__(self, columns, missing_migrations, warnings):
        self.columns = columns                      # {table: [column, ...]} as stored in the archive
        self.missing_migrations = missing_migrations  # ran on the target but not on the source
        self.warnings = warnings


def check(manifest, live_migrations, live_columns):
    """live_columns: {table: {column: {"nullable": bool, "has_default": bool}}}.
    Raises ArchiveError with a message meant for the admin; otherwise returns a Plan."""
    archive_migrations = list(manifest.get("app", {}).get("migrations") or [])
    live = list(live_migrations)
    warnings = []

    # An archive from a newer build may carry columns or tables this server has never heard of.
    ahead = [m for m in archive_migrations if m not in live]
    if ahead:
        raise ArchiveError(
            f"This archive was made by a newer version of iSpend ({ahead[0]}). "
            "Update this server first, then restore.")

    missing = [m for m in live if m not in archive_migrations]

    columns = {}
    for entry in manifest.get("tables", []):
        table = entry["name"]
        if table in EXCLUDED:
            continue
        if table not in TABLE_ORDER:
            raise ArchiveError(f"The archive contains a table this version no longer has ({table}).")
        if table not in live_columns:
            raise ArchiveError(f"The archive contains a table this server does not have ({table}).")
        archive_cols = list(entry["columns"])
        unknown = [c for c in archive_cols if c not in live_columns[table]]
        if unknown:
            raise ArchiveError(
                f"The archive's {table} table has a column this version no longer has "
                f"({unknown[0]}). Restore it into a matching version instead.")
        # A column added since the archive was made must be able to fill itself in.
        for name, meta in live_columns[table].items():
            if name in archive_cols:
                continue
            if not (meta["nullable"] or meta["has_default"]):
                raise ArchiveError(
                    f"This version requires {table}.{name}, which the archive does not carry "
                    "and cannot be filled in automatically.")
        columns[table] = archive_cols

    for table in TABLE_ORDER:
        if table not in columns:
            warnings.append({"kind": "table_absent", "table": table,
                             "detail": f"The archive has no {table}; it will be left empty."})
    if missing:
        warnings.append({"kind": "older_archive", "detail":
                         f"The archive predates {len(missing)} migration(s); "
                         "values added by them are filled in after loading."})
    return Plan(columns, missing, warnings)
