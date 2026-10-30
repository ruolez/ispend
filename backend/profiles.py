"""Profiles: independent sets of books owned by one person (e.g. Personal, Business)."""
import seed_categories

DEFAULT_NAME = "Personal"


def create(conn, user_id, name=DEFAULT_NAME, kind="personal", color="c1", taxonomy=None):
    """Insert a profile with its default categories and return its id. Does not commit."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO profiles (user_id, name, kind, color, sort_order)
               VALUES (%s, %s, %s, %s, (SELECT COALESCE(MAX(sort_order) + 1, 0) FROM profiles WHERE user_id = %s))
               RETURNING id""",
            (user_id, name, kind, color, user_id),
        )
        pid = cur.fetchone()[0]
    seed_categories.seed_for_profile(conn, user_id, pid, taxonomy)
    return pid


def ensure_all(conn):
    """Every customer has at least one profile (new installs, restores, accounts made by hand)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT u.id FROM users u
                        WHERE u.role = 'user' AND NOT EXISTS (SELECT 1 FROM profiles p WHERE p.user_id = u.id)""")
        user_ids = [r[0] for r in cur.fetchall()]
    for uid in user_ids:
        create(conn, uid)
    conn.commit()
