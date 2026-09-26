import os
import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extensions
import psycopg2.extras
from flask import g, has_request_context
from werkzeug.security import generate_password_hash

import config

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


def connect():
    return psycopg2.connect(**config.POSTGRES)


# Connections are reused across requests instead of opened per request (a TCP connect, a new
# Postgres backend and a password exchange each time — a screen switch makes 5-10 calls). Each
# worker process keeps up to POOL_IDLE idle connections; a busy moment simply opens more, and those
# beyond POOL_IDLE are closed when handed back. After a fork the child starts empty: its parent's
# sockets are never used (or closed — that would end the parent's sessions).
POOL_IDLE = int(os.environ.get("DB_POOL_IDLE", "8"))
_idle = []
_idle_pid = None
_idle_lock = threading.Lock()


def _reset_pool():
    global _idle, _idle_pid
    with _idle_lock:
        _idle, _idle_pid = [], None


def _take():
    global _idle, _idle_pid
    with _idle_lock:
        if _idle_pid != os.getpid():
            _idle, _idle_pid = [], os.getpid()
        while _idle:
            conn = _idle.pop()
            if not conn.closed:
                return conn
    return connect()


def _give_back(conn):
    """Return a connection for reuse — rolled back to a clean state — or close it if it is broken."""
    status = psycopg2.extensions.TRANSACTION_STATUS_UNKNOWN if conn.closed else conn.get_transaction_status()
    ok = status != psycopg2.extensions.TRANSACTION_STATUS_UNKNOWN
    if ok and status != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
        try:
            conn.rollback()
        except psycopg2.Error:
            ok = False
    with _idle_lock:
        if ok and _idle_pid == os.getpid() and len(_idle) < POOL_IDLE:
            _idle.append(conn)
            return
    try:
        conn.close()
    except psycopg2.Error:
        pass


def close_idle():
    """Close this process's idle connections (the gunicorn master after startup work: its children
    never use them)."""
    global _idle
    with _idle_lock:
        doomed, _idle = (_idle if _idle_pid == os.getpid() else []), []
    for conn in doomed:
        try:
            conn.close()
        except psycopg2.Error:
            pass


def get_db():
    if "db" not in g:
        g.db = _take()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        _give_back(db)


@contextmanager
def _timed():
    """Adds the statement's time to g.db_ms, which app.py reports in the Server-Timing header."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        if has_request_context():
            g.db_ms = g.get("db_ms", 0.0) + (time.perf_counter() - t0) * 1000


def _commit_now(commit):
    """Statements inside a transaction() block commit together at its end."""
    return commit and not g.get("in_tx")


def query(sql, params=None, one=False, commit=True):
    with _timed(), get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = None if cur.description is None else cur.fetchall()
    if _commit_now(commit):
        get_db().commit()
    if rows is None:
        return None
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=None, returning=False, commit=True):
    with _timed(), get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone() if returning else None
        count = cur.rowcount
    if _commit_now(commit):
        get_db().commit()
    return row if returning else count


def execute_values(sql, rows, template=None, commit=True, page_size=500, fetch=False):
    """Bulk insert via psycopg2.extras.execute_values; sql must contain VALUES %s.
    With fetch=True the RETURNING rows come back as dicts."""
    if not rows:
        return [] if fetch else None
    with _timed(), get_db().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        out = psycopg2.extras.execute_values(cur, sql, rows, template=template, page_size=page_size, fetch=fetch)
    if _commit_now(commit):
        get_db().commit()
    return [dict(r) for r in out] if fetch else None


@contextmanager
def transaction():
    """Group every query()/execute() in the block (callees included) into one commit; nests as a no-op."""
    conn = get_db()
    if g.get("in_tx"):
        yield conn
        return
    g.in_tx = True
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        g.in_tx = False


def _settings_memo():
    """Per-request memo of settings reads: entitlement, billing and the AI flags each read the same
    handful of keys several times per request. Only inside a request — background jobs run long
    and must see changes."""
    if not has_request_context():
        return None
    if "settings_memo" not in g:
        g.settings_memo = {}
    return g.settings_memo


def get_setting(key, default=None):
    memo = _settings_memo()
    if memo is not None and key in memo:
        value = memo[key]
    else:
        row = query("SELECT value FROM settings WHERE key = %s", (key,), one=True)
        value = row["value"] if row else None
        if memo is not None:
            memo[key] = value
    return value if value is not None else default


def set_setting(key, value):
    execute(
        """INSERT INTO settings (key, value) VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (key, value),
    )
    memo = _settings_memo()
    if memo is not None:
        memo[key] = value


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


def promote_admin(username):
    """Escape hatch: _seed_admin only fires when the users table is empty, so an instance that
    loses its last admin (manual SQL, a restore) has no way back in through the API.
        docker compose exec backend python -c "import db; db.promote_admin('admin')"
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET role = 'admin', status = 'active', locked_at = NULL, "
                        "deleted_at = NULL, updated_at = now() WHERE username = %s", (username,))
            if not cur.rowcount:
                raise SystemExit(f"No user named {username!r}")
        conn.commit()
        print(f"{username} is now an active admin")
    finally:
        conn.close()


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
