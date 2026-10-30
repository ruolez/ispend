"""Profiles: independent sets of books owned by one person (e.g. Personal, Business)."""
import os

import config
import db
import seed_categories

DEFAULT_NAME = "Personal"
KINDS = ("personal", "business")
COLORS = tuple(f"c{i}" for i in range(1, 13))
NAME_MAX = 40
FIELDS = ("id", "name", "kind", "color", "sort_order")
# An import still running in the background writes into its statement's profile.
BUSY_STATUSES = ("uploaded", "parsing", "committing")


class ProfileError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def create(conn, user_id, name=DEFAULT_NAME, kind="personal", color="c1", taxonomy=None):
    """Insert a profile with the default categories for its kind and return its id. Does not commit."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO profiles (user_id, name, kind, color, sort_order)
               VALUES (%s, %s, %s, %s, (SELECT COALESCE(MAX(sort_order) + 1, 0) FROM profiles WHERE user_id = %s))
               RETURNING id""",
            (user_id, name, kind, color, user_id),
        )
        pid = cur.fetchone()[0]
    if taxonomy is not False:
        seed_categories.seed_for_profile(conn, user_id, pid, taxonomy or seed_categories.TAXONOMIES[kind])
    return pid


def copy_setup(conn, user_id, src, dst):
    """Give profile dst a copy of src's categories, tags, rules that apply to every account, and
    merchant memory. Accounts and transactions are never copied. Does not commit."""
    with conn.cursor() as cur:
        cur.execute("""SELECT id, parent_id, name, slug, kind, color, icon, is_system, sort_order FROM categories
                        WHERE profile_id = %s ORDER BY parent_id NULLS FIRST, id""", (src,))
        cats = {}
        for cid, parent_id, name, slug, kind, color, icon, is_system, sort_order in cur.fetchall():
            if parent_id is not None and parent_id not in cats:
                continue
            cur.execute("""INSERT INTO categories (user_id, profile_id, parent_id, name, slug, kind, color, icon, is_system, sort_order)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (user_id, dst, cats.get(parent_id), name, slug, kind, color, icon, is_system, sort_order))
            cats[cid] = cur.fetchone()[0]
        cur.execute("""INSERT INTO tags (user_id, profile_id, name, color, sort_order)
                       SELECT %s, %s, name, color, sort_order FROM tags WHERE profile_id = %s""", (user_id, dst, src))
        cur.execute("""SELECT name, priority, is_active, match_type, match_field, pattern, case_sensitive, amount_min,
                              amount_max, category_id, set_transfer, set_excluded
                         FROM rules WHERE profile_id = %s AND account_id IS NULL ORDER BY priority, id""", (src,))
        for r in cur.fetchall():
            if r[9] is not None and r[9] not in cats:
                continue
            cur.execute("""INSERT INTO rules (user_id, profile_id, name, priority, is_active, match_type, match_field, pattern,
                                              case_sensitive, amount_min, amount_max, category_id, set_transfer, set_excluded)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (user_id, dst, *r[:9], cats.get(r[9]), r[10], r[11]))
        cur.execute("""SELECT merchant_key, direction, display_name, category_id, is_transfer FROM merchant_memory
                        WHERE profile_id = %s""", (src,))
        for key, direction, display, category_id, is_transfer in cur.fetchall():
            if category_id not in cats:
                continue
            cur.execute("""INSERT INTO merchant_memory (user_id, profile_id, merchant_key, direction, display_name, category_id,
                                                        is_transfer)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (user_id, dst, key, direction, display, cats[category_id], is_transfer))
        cur.execute("""UPDATE profiles SET preferences = preferences || '{"categories_seeded": true}'::jsonb
                        WHERE id = %s""", (dst,))
    return {"categories": len(cats)}


def clean_fields(data, existing=None):
    """Validated name / kind / color; ProfileError carries the message."""
    out = {}
    if existing is None or "name" in data:
        name = " ".join(str(data.get("name") or "").split())
        if not name:
            raise ProfileError("Give the profile a name")
        if len(name) > NAME_MAX:
            raise ProfileError(f"A profile name can be at most {NAME_MAX} characters")
        out["name"] = name
    if existing is None or "kind" in data:
        kind = data.get("kind") or "personal"
        if kind not in KINDS:
            raise ProfileError("A profile is personal or business")
        out["kind"] = kind
    if existing is None or "color" in data:
        color = data.get("color") or "c1"
        if color not in COLORS:
            raise ProfileError("Invalid color slot")
        out["color"] = color
    return out


def name_taken(user_id, name, exclude_id=None):
    row = db.query("SELECT id FROM profiles WHERE user_id = %s AND lower(name) = lower(%s)", (user_id, name), one=True)
    return bool(row) and row["id"] != exclude_id


def list_for(user_id):
    return db.query(
        """SELECT p.id, p.name, p.kind, p.color, p.sort_order, p.created_at,
                  (SELECT COUNT(*) FROM accounts a WHERE a.profile_id = p.id) AS accounts,
                  (SELECT COUNT(*) FROM transactions t WHERE t.profile_id = p.id) AS transactions
             FROM profiles p WHERE p.user_id = %s ORDER BY p.sort_order, p.id""", (user_id,)) or []


def owned(user_id, profile_id):
    return db.query(f"SELECT {', '.join(FIELDS)} FROM profiles WHERE id = %s AND user_id = %s",
                    (profile_id, user_id), one=True)


def delete(user_id, profile_id):
    """Delete a profile with everything in it; its uploaded files go when no other statement uses
    them. Refused for the person's last profile and while one of its imports is running."""
    if not owned(user_id, profile_id):
        raise ProfileError("Profile not found", 404)
    others = db.query("SELECT id FROM profiles WHERE user_id = %s AND id <> %s ORDER BY sort_order, id LIMIT 1",
                      (user_id, profile_id), one=True)
    if not others:
        raise ProfileError("This is your only profile. Create another one before deleting it.", 409)
    if db.query("SELECT 1 FROM statements WHERE profile_id = %s AND status = ANY(%s) LIMIT 1",
                (profile_id, list(BUSY_STATUSES)), one=True):
        raise ProfileError("An import is still running in this profile. Try again when it has finished.", 409)
    files = {r["path"] for r in db.query(
        """SELECT stored_path AS path FROM statements WHERE profile_id = %s
           UNION SELECT ocr_path FROM statements WHERE profile_id = %s AND ocr_path IS NOT NULL""",
        (profile_id, profile_id)) or []}
    counts = db.query("""SELECT (SELECT COUNT(*) FROM accounts WHERE profile_id = %(p)s) AS accounts,
                                (SELECT COUNT(*) FROM transactions WHERE profile_id = %(p)s) AS transactions,
                                (SELECT COUNT(*) FROM statements WHERE profile_id = %(p)s) AS statements""",
                      {"p": profile_id}, one=True)
    with db.transaction():
        db.execute("DELETE FROM profiles WHERE id = %s AND user_id = %s", (profile_id, user_id), commit=False)
        db.execute("UPDATE users SET last_profile_id = NULL WHERE id = %s AND last_profile_id = %s",
                   (user_id, profile_id), commit=False)
    for rel in files:
        if rel and not db.query("SELECT 1 FROM statements WHERE stored_path = %s OR ocr_path = %s LIMIT 1", (rel, rel), one=True):
            try:
                os.remove(os.path.join(config.STATEMENTS_DIR, rel))
            except OSError:
                pass
    return {**{k: int(v or 0) for k, v in counts.items()}, "next_profile_id": others["id"]}


def ensure_all(conn):
    """Every customer has at least one profile (new installs, restores, accounts made by hand)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT u.id FROM users u
                        WHERE u.role = 'user' AND NOT EXISTS (SELECT 1 FROM profiles p WHERE p.user_id = u.id)""")
        user_ids = [r[0] for r in cur.fetchall()]
    for uid in user_ids:
        create(conn, uid)
    conn.commit()
