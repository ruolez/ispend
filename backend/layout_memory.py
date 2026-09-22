"""Per-user memory of column mappings, keyed by the layout fingerprint of the file and the account."""
import json

import db


def lookup(user_id, layout_key, account_id=None):
    """The mapping saved for this account, else the one used most recently for the same layout."""
    return db.query(
        """SELECT id, mapping, bank_profile, account_id, times_used, last_used_at, sample_filename
           FROM import_layouts WHERE user_id = %s AND layout_key = %s
           ORDER BY (account_id IS NOT DISTINCT FROM %s) DESC, last_used_at DESC, id DESC LIMIT 1""",
        (user_id, layout_key, account_id), one=True,
    )


def learn(user_id, layout_key, mapping, header=None, bank_profile=None, account_id=None, filename=None,
          used=True, overwrite=True):
    """used=False saves a mapping that has not imported anything yet; overwrite=False only fills gaps."""
    if not layout_key or not mapping:
        return
    on_conflict = """DO UPDATE SET
             mapping = EXCLUDED.mapping,
             header = COALESCE(EXCLUDED.header, import_layouts.header),
             bank_profile = EXCLUDED.bank_profile,
             sample_filename = COALESCE(EXCLUDED.sample_filename, import_layouts.sample_filename),
             times_used = import_layouts.times_used + EXCLUDED.times_used,
             last_used_at = now(),
             updated_at = now()""" if overwrite else "DO NOTHING"
    db.execute(
        f"""INSERT INTO import_layouts (user_id, layout_key, header, mapping, bank_profile, account_id, sample_filename,
                                       times_used)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id, layout_key, account_id) {on_conflict}""",
        (user_id, layout_key, json.dumps(header) if header is not None else None, json.dumps(mapping, default=str),
         bank_profile, account_id, filename, 1 if used else 0),
    )
    if overwrite and account_id is not None:
        # the row saved while no account was chosen yet has done its job
        db.execute("DELETE FROM import_layouts WHERE user_id = %s AND layout_key = %s AND account_id IS NULL",
                   (user_id, layout_key))


def forget(user_id, layout_id, every_account=False):
    """every_account=True also drops what other accounts saved for the same columns, so detection starts over."""
    if every_account:
        return bool(db.execute(
            """DELETE FROM import_layouts WHERE user_id = %s
               AND layout_key = (SELECT layout_key FROM import_layouts WHERE user_id = %s AND id = %s)""",
            (user_id, user_id, layout_id)))
    return bool(db.execute("DELETE FROM import_layouts WHERE user_id = %s AND id = %s", (user_id, layout_id)))


def list_for(user_id):
    return db.query(
        """SELECT l.id, l.header, l.bank_profile, l.account_id, a.name AS account_name, l.sample_filename,
                  l.times_used, l.last_used_at, l.created_at
           FROM import_layouts l LEFT JOIN accounts a ON a.id = l.account_id
           WHERE l.user_id = %s ORDER BY l.last_used_at DESC, l.id DESC""",
        (user_id,),
    ) or []
