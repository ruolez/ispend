import json

from flask import Blueprint, jsonify, request, send_file, session

import db
import importer
import jobs
from auth import login_required
from util import api_error, audit, parse_int_list, row_json, rows_json

bp = Blueprint("statements", __name__, url_prefix="/api/statements")

ROW_LIMIT_DEFAULT = 500
ROW_LIMIT_MAX = 2000


def _get(statement_id):
    return db.query(
        """SELECT s.*, a.name AS account_name, u.username
           FROM statements s LEFT JOIN accounts a ON a.id = s.account_id LEFT JOIN users u ON u.id = s.user_id
           WHERE s.id = %s AND s.user_id = %s""",
        (statement_id, session["user_id"]), one=True,
    )


def _statement_json(st):
    out = row_json(st)
    stats = dict(st.get("stats") or {})
    out["header"] = stats.pop("header", None)
    out["sample"] = stats.pop("sample", None)
    out["stats"] = stats
    out["warnings"] = st.get("warnings") or []
    out.pop("stored_path", None)
    out.pop("ocr_path", None)
    return out


def _detail(st):
    out = _statement_json(st)
    if st["status"] in ("previewed", "committing"):
        offset = max(int(request.args.get("offset") or 0), 0)
        limit = min(int(request.args.get("limit") or ROW_LIMIT_DEFAULT), ROW_LIMIT_MAX)
        rows = db.query(
            """SELECT r.id, r.row_index, r.txn_date, r.posted_date, r.description, r.amount, r.balance, r.merchant_name,
                      r.occurrence, r.duplicate_of, r.in_file_duplicate, r.is_valid, r.include, r.category_id, r.problems,
                      t.txn_date AS dup_date, t.description_raw AS dup_description, t.amount AS dup_amount
               FROM import_rows r LEFT JOIN transactions t ON t.id = r.duplicate_of
               WHERE r.statement_id = %s ORDER BY r.row_index OFFSET %s LIMIT %s""",
            (st["id"], offset, limit),
        )
        items = []
        for r in rows_json(rows):
            dup = None
            if r["duplicate_of"]:
                dup = {"id": r["duplicate_of"], "txn_date": r.pop("dup_date"), "description": r.pop("dup_description"),
                       "amount": r.pop("dup_amount")}
            else:
                for k in ("dup_date", "dup_description", "dup_amount"):
                    r.pop(k, None)
            r["duplicate_txn"] = dup
            items.append(r)
        summary = db.query(
            """SELECT COUNT(*) AS rows_total,
                      COUNT(*) FILTER (WHERE is_valid) AS valid,
                      COUNT(*) FILTER (WHERE NOT is_valid) AS invalid,
                      COUNT(*) FILTER (WHERE duplicate_of IS NOT NULL) AS dupes_existing,
                      COUNT(*) FILTER (WHERE in_file_duplicate) AS dupes_in_file,
                      COUNT(*) FILTER (WHERE include AND is_valid AND duplicate_of IS NULL) AS included,
                      COUNT(*) FILTER (WHERE include AND is_valid AND duplicate_of IS NULL AND amount < 0) AS charges_n,
                      COALESCE(SUM(amount) FILTER (WHERE include AND is_valid AND duplicate_of IS NULL AND amount < 0), 0) AS charges_sum,
                      COUNT(*) FILTER (WHERE include AND is_valid AND duplicate_of IS NULL AND amount >= 0) AS payments_n,
                      COALESCE(SUM(amount) FILTER (WHERE include AND is_valid AND duplicate_of IS NULL AND amount >= 0), 0) AS payments_sum,
                      COUNT(*) FILTER (WHERE include AND is_valid AND duplicate_of IS NULL AND category_id IS NULL) AS uncategorized,
                      MIN(txn_date) AS date_min, MAX(txn_date) AS date_max
               FROM import_rows WHERE statement_id = %s""",
            (st["id"],), one=True,
        )
        s = row_json(summary)
        out["summary"] = {
            "rows_total": s["rows_total"], "valid": s["valid"], "invalid": s["invalid"],
            "dupes_existing": s["dupes_existing"], "dupes_in_file": s["dupes_in_file"], "included": s["included"],
            "charges": {"n": s["charges_n"], "sum": s["charges_sum"]},
            "payments": {"n": s["payments_n"], "sum": s["payments_sum"]},
            "uncategorized": s["uncategorized"], "date_min": s["date_min"], "date_max": s["date_max"],
        }
        out["rows"] = items
        out["rows_offset"] = offset
        out["rows_limit"] = limit
        import bank_profiles
        out["profile_options"] = [{"key": p.key, "label": p.label, "country": p.country} for p in bank_profiles.all_profiles()]
    return out


@bp.get("")
@login_required
def list_statements():
    sql = """SELECT s.id, s.original_filename, s.file_kind, s.file_size, s.status, s.bank_profile, s.profile_confidence,
                    s.account_id, a.name AS account_name, s.period_start, s.period_end, s.ocr_applied, s.stats, s.warnings,
                    s.error_message, s.created_at, s.updated_at, s.committed_at, u.username,
                    (SELECT COUNT(*) FROM transactions t WHERE t.statement_id = s.id) AS txn_count
             FROM statements s LEFT JOIN accounts a ON a.id = s.account_id LEFT JOIN users u ON u.id = s.user_id
             WHERE s.user_id = %s AND s.status <> 'discarded'"""
    params = [session["user_id"]]
    status = request.args.get("status")
    if status:
        sql += " AND s.status = ANY(%s)"
        params.append(status.split(","))
    if request.args.get("account_id"):
        sql += " AND s.account_id = %s"
        params.append(int(request.args["account_id"]))
    limit = min(int(request.args.get("limit") or 200), 1000)
    sql += " ORDER BY s.created_at DESC LIMIT %s"
    params.append(limit)
    rows = db.query(sql, params)
    return jsonify([_statement_json(r) for r in rows])


@bp.post("")
@login_required
def upload():
    f = request.files.get("file")
    if f is None:
        return api_error("No file uploaded (field name 'file')")
    account_id = request.form.get("account_id") or None
    if account_id:
        acct = db.query("SELECT id FROM accounts WHERE id = %s AND user_id = %s", (int(account_id), session["user_id"]), one=True)
        if not acct:
            return api_error("Account not found", 404)
        account_id = acct["id"]
    try:
        st = importer.store_upload(session["user_id"], f, account_id=account_id)
    except importer.ImportError_ as e:
        return api_error(str(e), e.status)
    db.execute("UPDATE statements SET status = 'parsing', updated_at = now() WHERE id = %s", (st["id"],))
    jobs.spawn(importer.parse_statement, st["id"])
    audit("statement.upload", {"id": st["id"], "filename": st["original_filename"], "kind": st["file_kind"]})
    return jsonify({"id": st["id"], "status": "parsing", "file_kind": st["file_kind"],
                    "original_filename": st["original_filename"], "duplicate_of": st["duplicate_of"]}), 201


@bp.get("/<int:statement_id>")
@login_required
def get_statement(statement_id):
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    return jsonify(_detail(st))


@bp.put("/<int:statement_id>/mapping")
@login_required
def put_mapping(statement_id):
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    data = request.get_json(silent=True) or {}
    mapping = data.get("mapping")
    if not isinstance(mapping, dict):
        return api_error("mapping object is required")
    profile_key = data.get("bank_profile") if "bank_profile" in data else st.get("bank_profile")
    try:
        importer.reparse_with_mapping(statement_id, mapping, profile_key=profile_key or None)
    except importer.ImportError_ as e:
        return api_error(str(e), e.status)
    st = _get(statement_id)
    if st["status"] == "error":
        return api_error(st.get("error_message") or "Could not parse with this mapping")
    return jsonify(_detail(st))


@bp.put("/<int:statement_id>/account")
@login_required
def put_account(statement_id):
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    data = request.get_json(silent=True) or {}
    account_id = data.get("account_id")
    if account_id:
        acct = db.query("SELECT id FROM accounts WHERE id = %s AND user_id = %s", (account_id, session["user_id"]), one=True)
        if not acct:
            return api_error("Account not found", 404)
    if st["status"] == "previewed":
        importer.recompute_dupes(statement_id, account_id)
    else:
        db.execute("UPDATE statements SET account_id = %s, updated_at = now() WHERE id = %s", (account_id, statement_id))
    return jsonify(_detail(_get(statement_id)))


@bp.put("/<int:statement_id>/rows")
@login_required
def put_rows(statement_id):
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    if st["status"] != "previewed":
        return api_error("Rows can only be changed while previewing", 409)
    data = request.get_json(silent=True) or {}
    include = bool(data.get("include", True))
    if data.get("all"):
        sql = "UPDATE import_rows SET include = %s WHERE statement_id = %s AND is_valid"
        params = [include, statement_id]
        only = data.get("only")
        if only == "dupes":
            sql += " AND duplicate_of IS NOT NULL"
        elif only == "in_file":
            sql += " AND in_file_duplicate"
        elif only == "clean":
            sql += " AND duplicate_of IS NULL"
        n = db.execute(sql, params)
    else:
        ids = parse_int_list(data.get("row_ids"))
        if not ids:
            return api_error("row_ids or all is required")
        n = db.execute("UPDATE import_rows SET include = %s WHERE statement_id = %s AND id = ANY(%s) AND is_valid",
                       (include, statement_id, ids))
    return jsonify({"updated": n, **{"summary": _detail(_get(statement_id)).get("summary")}})


@bp.post("/<int:statement_id>/commit")
@login_required
def commit(statement_id):
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    data = request.get_json(silent=True) or {}
    account_id = data.get("account_id") or st.get("account_id")
    if not account_id:
        return api_error("Choose an account to import into")
    try:
        result = importer.commit_statement(statement_id, int(account_id))
    except importer.ImportError_ as e:
        return api_error(str(e), e.status)
    audit("statement.commit", {"id": statement_id, **result})
    return jsonify(result)


@bp.post("/<int:statement_id>/reparse")
@login_required
def reparse(statement_id):
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    if st["status"] not in ("error", "previewed", "uploaded"):
        return api_error("Statement cannot be re-parsed in its current state", 409)
    db.execute("UPDATE statements SET status = 'parsing', error_message = NULL, updated_at = now() WHERE id = %s", (statement_id,))
    data = request.get_json(silent=True) or {}
    mapping = st.get("mapping") if data.get("keep_mapping") else None
    profile_key = data.get("bank_profile") or (st.get("bank_profile") if data.get("keep_mapping") else None)
    jobs.spawn(importer.parse_statement, statement_id, mapping, profile_key)
    return jsonify({"id": statement_id, "status": "parsing"})


@bp.delete("/<int:statement_id>")
@login_required
def delete_statement(statement_id):
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    with_txns = request.args.get("with_transactions") == "true"
    if st["status"] == "committed":
        n = db.query("SELECT COUNT(*) AS n FROM transactions WHERE statement_id = %s", (statement_id,), one=True)["n"]
        if n and not with_txns:
            return api_error(f"This statement imported {n} transactions. Confirm rollback to delete them too.", 409)
    if st["status"] in ("parsing", "committing"):
        return api_error("Statement is being processed; try again shortly", 409)
    deleted = importer.discard_statement(st, delete_transactions=with_txns)
    audit("statement.delete", {"id": statement_id, "deleted_transactions": deleted})
    return jsonify({"ok": True, "deleted_transactions": deleted})


@bp.post("/<int:statement_id>/flip-signs")
@login_required
def flip_signs(statement_id):
    """Negate every transaction imported from this statement (charges <-> payments)."""
    import signs
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    if st["status"] != "committed":
        return api_error("Only imported statements can be flipped", 409)
    ids = [r["id"] for r in db.query(
        "SELECT id FROM transactions WHERE statement_id = %s AND user_id = %s", (statement_id, session["user_id"])) or []]
    n = signs.flip_signs(session["user_id"], ids, reason=f"statement {statement_id}")
    mapping = dict(st.get("mapping") or {})
    mapping["flip_sign"] = not bool(mapping.get("flip_sign"))
    db.execute("UPDATE statements SET mapping = %s, updated_at = now() WHERE id = %s",
               (json.dumps(mapping), statement_id))
    audit("statement.flip_signs", {"id": statement_id, "flipped": n})
    return jsonify({"flipped": n})


@bp.get("/<int:statement_id>/file")
@login_required
def download(statement_id):
    st = _get(statement_id)
    if not st:
        return api_error("Statement not found", 404)
    path = importer.abs_path(st["stored_path"])
    try:
        return send_file(path, as_attachment=True, download_name=st["original_filename"])
    except FileNotFoundError:
        return api_error("Original file is no longer available", 404)
