import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from flask import g
from werkzeug.security import generate_password_hash

import config

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


def connect():
    return psycopg2.connect(**config.POSTGRES)


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, params=None, one=False, commit=True):
    with get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = None if cur.description is None else cur.fetchall()
    if commit:
        get_db().commit()
    if rows is None:
        return None
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=None, returning=False, commit=True):
    with get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone() if returning else None
        count = cur.rowcount
    if commit:
        get_db().commit()
    return row if returning else count


def execute_values(sql, rows, template=None, commit=True, page_size=500, fetch=False):
    """Bulk insert via psycopg2.extras.execute_values; sql must contain VALUES %s.
    With fetch=True the RETURNING rows come back as dicts."""
    if not rows:
        return [] if fetch else None
    with get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        out = psycopg2.extras.execute_values(cur, sql, rows, template=template, page_size=page_size, fetch=fetch)
    if commit:
        get_db().commit()
    return [dict(r) for r in out] if fetch else None


@contextmanager
def transaction():
    """Group several query()/execute(commit=False) calls into one commit."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_setting(key, default=None):
    row = query("SELECT value FROM settings WHERE key = %s", (key,), one=True)
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    execute(
        """INSERT INTO settings (key, value) VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (key, value),
    )


def user_setting(user_id, key, default=None):
    """Per-user setting stored in the global settings table under a 'u<id>:' prefix."""
    return get_setting(f"u{user_id}:{key}", default)


def set_user_setting(user_id, key, value):
    set_setting(f"u{user_id}:{key}", value)


def run_migrations():
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       filename TEXT PRIMARY KEY,
                       applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                   )"""
            )
            cur.execute("SELECT filename FROM schema_migrations")
            applied = {r[0] for r in cur.fetchall()}
            for fname in sorted(os.listdir(MIGRATIONS_DIR)):
                if not fname.endswith(".sql") or fname in applied:
                    continue
                with open(os.path.join(MIGRATIONS_DIR, fname)) as f:
                    cur.execute(f.read())
                cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (fname,))
        conn.commit()
        _seed_admin(conn)
        _seed_categories(conn)
    finally:
        conn.close()


def _seed_admin(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'admin')",
                ("admin", generate_password_hash(config.ADMIN_INITIAL_PASSWORD)),
            )
    conn.commit()


def _seed_categories(conn):
    """Every user gets the default taxonomy once; users who deleted it all keep it empty."""
    import seed_categories

    with conn.cursor() as cur:
        cur.execute(
            """SELECT u.id FROM users u
               WHERE NOT EXISTS (SELECT 1 FROM categories c WHERE c.user_id = u.id)
                 AND NOT COALESCE((u.preferences->>'categories_seeded')::boolean, FALSE)"""
        )
        user_ids = [r[0] for r in cur.fetchall()]
    for uid in user_ids:
        seed_categories.seed_for_user(conn, uid)
    conn.commit()
