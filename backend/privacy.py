"""A person's data rights: a copy of everything iSpend holds about them, and erasure.

Export: one zip, built in the background, with a JSON file per kind of record, the transactions
as a spreadsheet-friendly CSV too, and the statement files they uploaded. It is for the person —
an admin can start one on their behalf, but the download link goes to the person's own email and
only works in their own session.

Erase: cancel any Stripe subscription first (never delete someone who would go on being charged),
remove their names from the activity log, delete the account (the database cascades to everything
they own), delete their files, and keep an anonymous record that it happened. Invoices and the
revenue ledger keep only the bare account number, for bookkeeping.

Every backed-up table is listed below as exported or not, with the reason; a test fails when a new
table is added without deciding.
"""

import csv
import hashlib
import io
import json
import logging
import os
import secrets
import shutil
import zipfile
from datetime import datetime, timedelta, timezone

import config
import db

log = logging.getLogger(__name__)

EXPORT_TTL = timedelta(days=7)
EXPORT_DIRNAME = "_exports"

# table -> SQL returning that person's rows (%(u)s is their id). Order is the order in the zip.
EXPORTED = {
    "users": "SELECT id, username, email, email_verified_at, role, status, created_at, last_login_at, last_seen_at, preferences FROM users WHERE id = %(u)s",
    "accounts": "SELECT * FROM accounts WHERE user_id = %(u)s ORDER BY id",
    "categories": "SELECT * FROM categories WHERE user_id = %(u)s ORDER BY id",
    "tags": "SELECT * FROM tags WHERE user_id = %(u)s ORDER BY id",
    "rules": "SELECT * FROM rules WHERE user_id = %(u)s ORDER BY id",
    "budgets": "SELECT * FROM budgets WHERE user_id = %(u)s ORDER BY id",
    "statements": """SELECT id, account_id, original_filename, file_kind, status, bank_profile, period_start, period_end,
                            created_at, committed_at, stats FROM statements WHERE user_id = %(u)s ORDER BY id""",
    "transactions": """SELECT id, account_id, statement_id, txn_date, posted_date, amount, currency, balance, description_raw,
                              description_clean, merchant_name, category_id, category_status, category_source, is_transfer,
                              is_excluded, notes, created_at FROM transactions WHERE user_id = %(u)s ORDER BY txn_date, id""",
    "transaction_splits": """SELECT s.* FROM transaction_splits s JOIN transactions t ON t.id = s.transaction_id
                               WHERE t.user_id = %(u)s ORDER BY s.id""",
    "transaction_tags": """SELECT tt.* FROM transaction_tags tt JOIN transactions t ON t.id = tt.transaction_id
                             WHERE t.user_id = %(u)s""",
    "transaction_events": """SELECT e.* FROM transaction_events e JOIN transactions t ON t.id = e.transaction_id
                               WHERE t.user_id = %(u)s ORDER BY e.id""",
    "merchant_memory": "SELECT * FROM merchant_memory WHERE user_id = %(u)s ORDER BY id",
    "import_layouts": "SELECT * FROM import_layouts WHERE user_id = %(u)s ORDER BY id",
    "recurring_dismissals": "SELECT * FROM recurring_dismissals WHERE user_id = %(u)s",
    "anomaly_dismissals": "SELECT * FROM anomaly_dismissals WHERE user_id = %(u)s",
    "insights": "SELECT * FROM insights WHERE user_id = %(u)s ORDER BY id",
    "ai_calls": "SELECT id, purpose, model, item_count, prompt_tokens, completion_tokens, status, created_at FROM ai_calls WHERE user_id = %(u)s ORDER BY id",
    "subscriptions": """SELECT status, plan, trial_end, current_period_end, cancel_at_period_end, comped_until, currency,
                               mrr_cents, started_at, canceled_at, ended_at, created_at FROM subscriptions WHERE user_id = %(u)s""",
    "subscription_events": "SELECT kind, status_to, plan_to, mrr_to_cents, currency, occurred_at FROM subscription_events WHERE user_id = %(u)s ORDER BY occurred_at",
    "payments": """SELECT status, currency, subtotal_cents, discount_cents, tax_cents, amount_paid_cents, amount_refunded_cents,
                          plan, period_start, period_end, paid_at, failed_at FROM payments WHERE user_id = %(u)s ORDER BY created_at""",
    "login_events": "SELECT kind, ok, reason, ip::text AS ip, browser, os, device, country, created_at FROM login_events WHERE user_id = %(u)s ORDER BY id",
    "email_log": "SELECT template, subject, status, created_at, sent_at FROM email_log WHERE user_id = %(u)s ORDER BY id",
    "user_activity_days": "SELECT day, kinds FROM user_activity_days WHERE user_id = %(u)s ORDER BY day",
    "signup_attribution": "SELECT * FROM signup_attribution WHERE user_id = %(u)s",
    "audit_log": "SELECT action, detail, created_at FROM audit_log WHERE user_id = %(u)s ORDER BY id",
    "admin_notes": "SELECT body, created_at FROM admin_notes WHERE user_id = %(u)s ORDER BY id",
    "user_admin_tags": """SELECT t.name FROM user_admin_tags ut JOIN admin_tags t ON t.id = ut.tag_id WHERE ut.user_id = %(u)s""",
}

NOT_EXPORTED = {
    "settings": "per-user settings are exported separately with secrets masked",
    "stripe_events": "Stripe delivery bookkeeping; the payments and subscription history cover it",
    "auth_tokens": "one-time secrets",
    "import_rows": "a preview that disappears when the statement is imported",
    "data_exports": "this archive's own bookkeeping",
    "erasures": "anonymous records of other erasures",
    "metric_daily": "totals across everyone, nothing about one person",
    "admin_tags": "the tag names are exported with user_admin_tags",
    "backup_jobs": "not backed up",
    "schema_migrations": "not personal data",
    "app_errors": "server diagnostics, pruned after 30 days",
    "metric_cache": "computed totals across everyone",
}

SECRET_SETTING = ("key", "secret", "password", "token")


class EraseError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def exports_dir(uid):
    return os.path.join(config.STATEMENTS_DIR, EXPORT_DIRNAME, str(uid))


def _json(rows):
    def default(v):
        if isinstance(v, (datetime,)):
            return v.isoformat()
        return str(v)
    return json.dumps(rows, indent=2, default=default, ensure_ascii=False)


def _settings(uid):
    rows = db.query("SELECT key, value FROM settings WHERE key LIKE %s", (f"u{uid}:%",)) or []
    out = {}
    for r in rows:
        key = r["key"].split(":", 1)[1]
        out[key] = "(hidden)" if any(s in key for s in SECRET_SETTING) and r["value"] else r["value"]
    return out


README = """This is everything iSpend holds about your account, as of {when}.

- Each .json file is one kind of record (accounts, categories, rules, transactions, ...).
- transactions.csv is the same transactions in a form any spreadsheet opens.
- statements/ holds the files you uploaded, named by their iSpend statement number.
- settings.json is your settings; saved keys and passwords are shown as (hidden).

Amounts are in the currency of each account; negative means money out.
"""


def request_export(uid, requested_by):
    row = db.execute(
        """INSERT INTO data_exports (user_id, requested_by, token) VALUES (%s, %s, %s) RETURNING id""",
        (uid, requested_by, secrets.token_urlsafe(24)), returning=True)
    import jobs
    jobs.spawn(build_export, row["id"])
    return row["id"]


def build_export(export_id):
    job = db.query("SELECT * FROM data_exports WHERE id = %s", (export_id,), one=True)
    if not job:
        return
    uid = job["user_id"]
    db.execute("UPDATE data_exports SET status = 'running' WHERE id = %s", (export_id,))
    folder = exports_dir(uid)
    path = os.path.join(folder, f"{job['token']}.zip")
    try:
        os.makedirs(folder, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", README.format(when=datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")))
            for table, sql in EXPORTED.items():
                z.writestr(f"{table}.json", _json(db.query(sql, {"u": uid}) or []))
            z.writestr("settings.json", _json(_settings(uid)))
            txns = db.query(EXPORTED["transactions"], {"u": uid}) or []
            buf = io.StringIO()
            if txns:
                w = csv.DictWriter(buf, fieldnames=list(txns[0].keys()))
                w.writeheader()
                for t in txns:
                    w.writerow(t)
            z.writestr("transactions.csv", buf.getvalue())
            for st in db.query("SELECT id, stored_path, file_kind FROM statements WHERE user_id = %s", (uid,)) or []:
                src = os.path.join(config.STATEMENTS_DIR, st["stored_path"] or "")
                if st.get("stored_path") and os.path.isfile(src):
                    z.write(src, f"statements/statement-{st['id']}.{st['file_kind']}")
        os.chmod(path, 0o600)
        db.execute(
            """UPDATE data_exports SET status = 'done', file_path = %s, size_bytes = %s, finished_at = now(),
                      expires_at = now() + make_interval(days => %s) WHERE id = %s""",
            (path, os.path.getsize(path), EXPORT_TTL.days, export_id))
        _notify_ready(uid, export_id)
    except Exception as e:
        log.exception("data export %s failed", export_id)
        db.execute("UPDATE data_exports SET status = 'error', error_message = %s, finished_at = now() WHERE id = %s",
                   (str(e)[:300], export_id))
        try:
            os.remove(path)
        except OSError:
            pass


def _notify_ready(uid, export_id):
    import mailer
    user = db.query("SELECT username, email FROM users WHERE id = %s", (uid,), one=True)
    if user and user.get("email"):
        base = (config.APP_BASE_URL or "").rstrip("/")
        mailer.send_template("export_ready", user["email"], user_id=uid, username=user["username"],
                             link=f"{base}/settings.html#data")


def prune_exports():
    """Remove archives past their seven days; called hourly."""
    rows = db.query("""SELECT id, file_path FROM data_exports
                        WHERE status = 'done' AND expires_at < now()""") or []
    for r in rows:
        try:
            if r.get("file_path"):
                os.remove(r["file_path"])
        except OSError:
            pass
        db.execute("UPDATE data_exports SET status = 'expired', file_path = NULL WHERE id = %s", (r["id"],))
    return len(rows)


def export_json(row):
    return {"id": row["id"], "status": row["status"], "size_bytes": row.get("size_bytes"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
            "downloaded_at": row["downloaded_at"].isoformat() if row.get("downloaded_at") else None,
            "by_admin": bool(row.get("requested_by")) and row.get("requested_by") != row.get("user_id")}


# ---------- erasure ----------

SCRUB_KEYS = ("username", "email", "name", "filename", "reason", "to", "merchant_key")


def _cancel_stripe(uid):
    """True when a live subscription was cancelled. Raises EraseError when Stripe refuses: the
    person must never be deleted while their card is still being charged."""
    sub = db.query("SELECT status, stripe_subscription_id, stripe_customer_id FROM subscriptions WHERE user_id = %s",
                   (uid,), one=True) or {}
    if not sub.get("stripe_subscription_id") or sub.get("status") in ("canceled", "incomplete_expired"):
        return False
    import billing
    try:
        stripe = billing.client()
        stripe.Subscription.cancel(sub["stripe_subscription_id"])
    except Exception as e:
        log.exception("could not cancel subscription before erasure")
        raise EraseError(f"Stripe would not cancel the subscription, so nothing was deleted: {e}", 502) from e
    try:
        stripe.Customer.modify(sub["stripe_customer_id"], email="", name="Erased customer",
                               metadata={"ispend_erased": "1"})
    except Exception:
        log.warning("could not scrub the Stripe customer record", exc_info=True)
    return True


def erase(uid, requested_by, admin_id=None, reason=None):
    """Irreversible. Returns the erasure record."""
    user = db.query("SELECT id, username, email, role, status FROM users WHERE id = %s", (uid,), one=True)
    if not user:
        raise EraseError("User not found", 404)
    if user["role"] == "admin":
        others = db.query("""SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND status = 'active' AND id <> %s""",
                          (uid,), one=True)
        if not others["n"]:
            raise EraseError("There must always be at least one active administrator")
    busy = db.query("""SELECT 1 FROM statements WHERE user_id = %s AND status IN ('parsing','committing')
                        AND updated_at > now() - interval '10 minutes' LIMIT 1""", (uid,), one=True)
    if busy:
        raise EraseError("An import is still running for this account. Try again in a few minutes.")
    canceled = _cancel_stripe(uid)
    counts = db.query(
        """SELECT (SELECT COUNT(*) FROM transactions WHERE user_id = %(u)s) AS transactions,
                  (SELECT COUNT(*) FROM statements WHERE user_id = %(u)s) AS statements,
                  (SELECT COUNT(*) FROM accounts WHERE user_id = %(u)s) AS accounts""", {"u": uid}, one=True) or {}
    folder = os.path.join(config.STATEMENTS_DIR, str(uid))
    files, size = 0, 0
    for base in (folder, exports_dir(uid)):
        for root, _dirs, names in os.walk(base):
            for n in names:
                files += 1
                try:
                    size += os.path.getsize(os.path.join(root, n))
                except OSError:
                    pass
    email_hash = hashlib.sha256(user["email"].strip().lower().encode()).hexdigest() if user.get("email") else None
    with db.transaction():
        db.execute(
            "UPDATE audit_log SET detail = detail - %s::text[] WHERE (user_id = %s OR target_user_id = %s) AND detail IS NOT NULL",
            (list(SCRUB_KEYS), uid, uid), commit=False)
        db.execute("DELETE FROM users WHERE id = %s", (uid,), commit=False)
        db.execute("DELETE FROM settings WHERE key LIKE %s", (f"u{uid}:%",), commit=False)
        row = db.execute(
            """INSERT INTO erasures (erased_user_id, email_sha256, requested_by, admin_id, reason, stripe_canceled,
                                     counts, files_removed, bytes_removed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
            (uid, email_hash, requested_by, admin_id, (reason or "")[:500] or None, canceled,
             json.dumps({k: int(v or 0) for k, v in counts.items()}), files, size), returning=True, commit=False)
    shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(exports_dir(uid), ignore_errors=True)
    if user.get("email"):
        import mailer
        mailer.send_async("account_deleted", user["email"], username=user["username"])
    return {"id": row["id"], "created_at": row["created_at"].isoformat(), "stripe_canceled": canceled,
            "counts": counts, "files_removed": files}
