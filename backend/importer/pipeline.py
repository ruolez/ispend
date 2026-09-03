"""Import orchestration: upload -> parse (background) -> preview rows -> commit. Only module here touching db."""
import json
import logging
import os
from datetime import date
from decimal import Decimal

import config
import db
import dedupe
import merchant
from importer import sniff
from importer.models import Mapping
from util import record_events

log = logging.getLogger(__name__)


class ImportError_(Exception):
    """Raised for user-facing import failures (state conflicts, bad input)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def abs_path(rel):
    return os.path.join(config.STATEMENTS_DIR, rel)


def store_upload(user_id, file_storage, account_id=None):
    data = file_storage.read()
    if not data:
        raise ImportError_("The uploaded file is empty")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ImportError_("File is too large (limit %d MB)" % (config.MAX_UPLOAD_BYTES // (1024 * 1024)), 413)
    filename = os.path.basename(file_storage.filename or "statement")
    try:
        kind = sniff.file_kind(filename, data[:4096])
    except ValueError as e:
        raise ImportError_(str(e))
    sha = sniff.sha256_bytes(data)
    rel = f"{user_id}/{sha}.{kind}"
    path = abs_path(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    dup = db.query(
        """SELECT id FROM statements WHERE user_id = %s AND file_sha256 = %s AND status <> 'discarded'
           ORDER BY id DESC LIMIT 1""",
        (user_id, sha), one=True,
    )
    row = db.execute(
        """INSERT INTO statements (user_id, account_id, original_filename, stored_path, file_sha256, file_size, file_kind)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (user_id, account_id, filename, rel, sha, len(data), kind), returning=True,
    )
    return {**row, "duplicate_of": dup["id"] if dup else None}


def _load(statement_id):
    return db.query("SELECT * FROM statements WHERE id = %s", (statement_id,), one=True)


def _set_error(statement_id, message):
    db.execute("UPDATE statements SET status = 'error', error_message = %s, updated_at = now() WHERE id = %s",
               (message[:1000], statement_id))


def parse_statement(statement_id, mapping=None, profile_key=None):
    st = _load(statement_id)
    if not st:
        return
    db.execute("UPDATE statements SET status = 'parsing', error_message = NULL, updated_at = now() WHERE id = %s",
               (statement_id,))
    try:
        result, ocr_path = _parse_file(st, mapping, profile_key)
        _stage(st, result, ocr_path)
    except Exception as e:
        log.exception("parse failed for statement %s", statement_id)
        _set_error(statement_id, f"{e}")


def _parse_file(st, mapping, profile_key):
    from importer import csv_parser, excel_parser

    path = abs_path(st["stored_path"])
    kind = st["file_kind"]
    if isinstance(mapping, dict):
        mapping = Mapping.from_dict(mapping)
    if kind == "csv":
        with open(path, "rb") as f:
            text = sniff.decode_text(f.read())
        return csv_parser.parse_csv(text, mapping=mapping, profile_key=profile_key), None
    if kind in ("xlsx", "xls"):
        rows = excel_parser.load_excel_rows(path, kind)
        if not rows:
            raise ImportError_("The workbook has no data rows")
        return csv_parser.parse_rows(rows, mapping=mapping, profile_key=profile_key), None
    if kind == "pdf":
        from importer import pdf_ocr, pdf_text

        ocr_rel = None
        source = path
        if st.get("ocr_path") and os.path.exists(abs_path(st["ocr_path"])):
            source, ocr_rel = abs_path(st["ocr_path"]), st["ocr_path"]
        elif pdf_ocr.needs_ocr(path):
            ocr_rel = st["stored_path"][:-4] + ".ocr.pdf"
            pdf_ocr.run_ocr(path, abs_path(ocr_rel), langs=config.OCR_LANGS, timeout=config.OCR_TIMEOUT_SECONDS)
            source = abs_path(ocr_rel)
        flip = bool(mapping.flip_sign) if mapping else False
        result = pdf_text.parse_pdf(source, profile_key=profile_key, flip_sign=flip)
        result.ocr_applied = ocr_rel is not None
        return result, ocr_rel
    raise ImportError_("Unsupported file kind")


def _categorize_rows(user_id, account_id, staged):
    """Fill predicted category ids at preview time; never fatal."""
    try:
        import categorizer
        ctx = categorizer.load_context(user_id)
    except Exception:
        log.info("categorizer unavailable at preview", exc_info=True)
        return
    for s in staged:
        r = s["row"]
        if not r.is_valid:
            continue
        try:
            d = categorizer.categorize({
                "description_clean": s["merchant"].clean, "description_raw": r.description,
                "merchant_key": s["merchant"].key, "amount": r.amount, "account_id": account_id,
            }, ctx)
            s["category_id"] = d.category_id
        except Exception:
            log.debug("categorize failed at preview", exc_info=True)


def _build_staged(rows, account_id):
    staged = []
    for r in rows:
        m = merchant.normalize(r.description)
        fp = dedupe.fingerprint(account_id or 0, r.txn_date, r.amount, m.clean) if r.is_valid else None
        staged.append({"row": r, "merchant": m, "fingerprint": fp, "occurrence": 1, "category_id": None})
    dedupe.assign_occurrences(staged)
    return staged


def _stage(st, result, ocr_rel):
    uid, account_id, sid = st["user_id"], st["account_id"], st["id"]
    staged = _build_staged(result.rows, account_id)
    existing = dedupe.find_existing(account_id, [(s["fingerprint"], s["occurrence"]) for s in staged if s["fingerprint"]]) \
        if account_id else {}
    if account_id:
        _categorize_rows(uid, account_id, staged)
    values = []
    for s in staged:
        r = s["row"]
        dup = existing.get((s["fingerprint"], s["occurrence"]))
        values.append((
            sid, r.row_index, r.txn_date, r.posted_date, r.description, r.amount, r.balance, json.dumps(r.raw, default=str),
            s["merchant"].name, s["fingerprint"], s["occurrence"], dup, s["occurrence"] > 1, r.is_valid,
            r.is_valid and dup is None, s["category_id"], r.problems,
        ))
    stats = _preview_stats(staged, existing)
    stats["header"] = result.header
    stats["sample"] = result.sample
    with db.transaction():
        db.execute("DELETE FROM import_rows WHERE statement_id = %s", (sid,), commit=False)
        db.execute_values(
            """INSERT INTO import_rows (statement_id, row_index, txn_date, posted_date, description, amount, balance, raw,
                                        merchant_name, fingerprint, occurrence, duplicate_of, in_file_duplicate, is_valid,
                                        include, category_id, problems) VALUES %s""",
            values, commit=False,
        )
        db.execute(
            """UPDATE statements SET status = 'previewed', bank_profile = %s, profile_confidence = %s, mapping = %s,
                   period_start = %s, period_end = %s, ocr_applied = %s, ocr_path = COALESCE(%s, ocr_path),
                   stats = %s, warnings = %s, error_message = NULL, updated_at = now()
               WHERE id = %s""",
            (result.profile, result.profile_confidence, json.dumps(result.mapping.to_dict()) if result.mapping else None,
             result.period[0] if result.period else None, result.period[1] if result.period else None,
             bool(result.ocr_applied), ocr_rel, json.dumps(stats, default=str), json.dumps(result.warnings), sid),
            commit=False,
        )


def _preview_stats(staged, existing):
    valid = [s for s in staged if s["row"].is_valid]
    dup_existing = sum(1 for s in valid if (s["fingerprint"], s["occurrence"]) in existing)
    return {
        "rows_total": len(staged),
        "rows_valid": len(valid),
        "rows_invalid": len(staged) - len(valid),
        "dupes_existing": dup_existing,
        "dupes_in_file": sum(1 for s in valid if s["occurrence"] > 1),
    }


def reparse_with_mapping(statement_id, mapping, profile_key=None):
    st = _load(statement_id)
    if not st:
        raise ImportError_("Statement not found", 404)
    if st["status"] not in ("previewed", "error", "uploaded"):
        raise ImportError_("Statement is not editable in its current state", 409)
    parse_statement(statement_id, mapping=mapping, profile_key=profile_key or st.get("bank_profile"))
    return _load(statement_id)


def recompute_dupes(statement_id, account_id):
    st = _load(statement_id)
    if not st or st["status"] != "previewed":
        return
    rows = db.query("SELECT id, txn_date, description, amount, is_valid FROM import_rows WHERE statement_id = %s ORDER BY row_index",
                    (statement_id,))
    staged = []
    for r in rows:
        m = merchant.normalize(r["description"] or "")
        fp = dedupe.fingerprint(account_id or 0, r["txn_date"], r["amount"], m.clean) if r["is_valid"] else None
        staged.append({"id": r["id"], "fingerprint": fp, "occurrence": 1, "merchant": m, "category_id": None,
                       "row": _RowShim(r)})
    dedupe.assign_occurrences(staged)
    existing = dedupe.find_existing(account_id, [(s["fingerprint"], s["occurrence"]) for s in staged if s["fingerprint"]]) \
        if account_id else {}
    if account_id:
        _categorize_rows(st["user_id"], account_id, staged)
    with db.transaction():
        for s in staged:
            dup = existing.get((s["fingerprint"], s["occurrence"]))
            db.execute(
                """UPDATE import_rows SET fingerprint = %s, occurrence = %s, duplicate_of = %s, in_file_duplicate = %s,
                       include = %s, category_id = %s WHERE id = %s""",
                (s["fingerprint"], s["occurrence"], dup, s["occurrence"] > 1, s["row"].is_valid and dup is None,
                 s["category_id"], s["id"]), commit=False,
            )
        stats = dict(st["stats"] or {})
        stats.update(_preview_stats(staged, existing))
        db.execute("UPDATE statements SET account_id = %s, stats = %s, updated_at = now() WHERE id = %s",
                   (account_id, json.dumps(stats, default=str), statement_id), commit=False)


class _RowShim:
    def __init__(self, r):
        self.txn_date = r["txn_date"]
        self.amount = r["amount"]
        self.description = r["description"] or ""
        self.is_valid = bool(r["is_valid"])


def commit_statement(statement_id, account_id):
    st = _load(statement_id)
    if not st:
        raise ImportError_("Statement not found", 404)
    uid = st["user_id"]
    account = db.query("SELECT * FROM accounts WHERE id = %s AND user_id = %s", (account_id, uid), one=True)
    if not account:
        raise ImportError_("Choose an account to import into")
    locked = db.execute(
        "UPDATE statements SET status = 'committing', account_id = %s, updated_at = now() WHERE id = %s AND status = 'previewed'",
        (account_id, statement_id),
    )
    if not locked:
        raise ImportError_("Statement is not ready to import (status: %s)" % st["status"], 409)
    try:
        return _commit_locked(st, account)
    except Exception as e:
        log.exception("commit failed for statement %s", statement_id)
        db.execute("UPDATE statements SET status = 'previewed', error_message = %s, updated_at = now() WHERE id = %s",
                   (str(e)[:1000], statement_id))
        raise


def _commit_locked(st, account):
    sid, uid, account_id = st["id"], st["user_id"], account["id"]
    all_rows = db.query("SELECT * FROM import_rows WHERE statement_id = %s ORDER BY row_index", (sid,))
    excluded_by_user = sum(1 for r in all_rows if r["is_valid"] and r["duplicate_of"] is None and not r["include"])
    skipped_invalid = sum(1 for r in all_rows if not r["is_valid"])
    candidates = [r for r in all_rows if r["is_valid"] and r["include"]]
    staged = []
    for r in candidates:
        m = merchant.normalize(r["description"] or "")
        staged.append({"row": r, "merchant": m,
                       "fingerprint": dedupe.fingerprint(account_id, r["txn_date"], r["amount"], m.clean), "occurrence": 1})
    dedupe.assign_occurrences(staged)
    existing = dedupe.find_existing(account_id, [(s["fingerprint"], s["occurrence"]) for s in staged])
    ctx = None
    try:
        import categorizer
        ctx = categorizer.load_context(uid)
    except Exception:
        log.warning("categorizer unavailable during commit", exc_info=True)
        categorizer = None
    counts = {"rule": 0, "merchant": 0, "builtin": 0, "suggested": 0, "uncategorized": 0}
    rule_hits = {}
    events, inserted_ids, uncategorized_ids = [], [], []
    skipped_dupes = 0
    with db.transaction():
        for s in staged:
            r, m = s["row"], s["merchant"]
            if (s["fingerprint"], s["occurrence"]) in existing:
                skipped_dupes += 1
                continue
            d = None
            if ctx is not None:
                try:
                    d = categorizer.categorize({
                        "description_clean": m.clean, "description_raw": r["description"] or "",
                        "merchant_key": m.key, "amount": r["amount"], "account_id": account_id,
                    }, ctx)
                except Exception:
                    log.debug("categorize failed at commit", exc_info=True)
            category_id = getattr(d, "category_id", None)
            status = getattr(d, "status", "none") if category_id else "none"
            source = getattr(d, "source", None) if category_id else None
            rule_id = getattr(d, "rule_id", None)
            confidence = getattr(d, "confidence", None) if category_id else None
            is_transfer = bool(getattr(d, "is_transfer", False))
            is_excluded = bool(getattr(d, "is_excluded", False)) or is_transfer
            row = db.execute(
                """INSERT INTO transactions (user_id, account_id, statement_id, txn_date, posted_date, amount, currency, balance,
                       description_raw, description_clean, merchant_key, merchant_name, category_id, category_status,
                       category_source, category_rule_id, category_confidence, is_transfer, is_excluded, fingerprint,
                       occurrence, raw)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (account_id, fingerprint, occurrence) DO NOTHING RETURNING id""",
                (uid, account_id, sid, r["txn_date"], r["posted_date"], r["amount"], account["currency"], r["balance"],
                 r["description"] or "", m.clean, m.key, m.name, category_id, status, source, rule_id, confidence,
                 is_transfer, is_excluded, s["fingerprint"], s["occurrence"], json.dumps(r["raw"], default=str)),
                returning=True, commit=False,
            )
            if not row:
                skipped_dupes += 1
                continue
            tid = row["id"]
            inserted_ids.append(tid)
            events.append((tid, "imported", {"statement_id": sid, "filename": st["original_filename"]}, uid))
            if category_id and source in ("rule", "merchant", "builtin"):
                events.append((tid, source, {"category_id": category_id, "rule_id": rule_id, "confidence": confidence,
                                             "status": status}, uid))
                if status == "confirmed":
                    counts[source] += 1
                else:
                    counts["suggested"] += 1
                if source == "rule" and rule_id:
                    rule_hits[rule_id] = rule_hits.get(rule_id, 0) + 1
            else:
                counts["uncategorized"] += 1
                uncategorized_ids.append(tid)
            if is_transfer:
                events.append((tid, "transfer", {"by": source or "import"}, uid))
        record_events(events, commit=False)
        for rid, n in rule_hits.items():
            db.execute("UPDATE rules SET hit_count = hit_count + %s, last_hit_at = now() WHERE id = %s", (n, rid), commit=False)
        db.execute("DELETE FROM import_rows WHERE statement_id = %s", (sid,), commit=False)
        stats = dict(st["stats"] or {})
        stats.pop("header", None)
        stats.pop("sample", None)
        result = {
            "imported": len(inserted_ids), "skipped_duplicates": skipped_dupes, "skipped_invalid": skipped_invalid,
            "excluded_by_user": excluded_by_user, "categorized": counts,
            "ai_queued": bool(ctx is not None and getattr(ctx, "ai_enabled", False) and uncategorized_ids),
        }
        stats.update(result)
        db.execute(
            """UPDATE statements SET status = 'committed', committed_at = now(), stats = %s, error_message = NULL,
                   updated_at = now() WHERE id = %s""",
            (json.dumps(stats, default=str), sid), commit=False,
        )
    if result["ai_queued"]:
        try:
            import ai_categorizer
            import jobs
            jobs.spawn(ai_categorizer.suggest_for_ids, uid, uncategorized_ids)
        except Exception:
            log.warning("could not queue AI suggestions", exc_info=True)
            result["ai_queued"] = False
    return result


def discard_statement(st, delete_transactions=False):
    """Remove a statement (and optionally its transactions) plus its files when unreferenced."""
    sid = st["id"]
    deleted = 0
    with db.transaction():
        if delete_transactions:
            deleted = db.execute("DELETE FROM transactions WHERE statement_id = %s", (sid,), commit=False)
        db.execute("DELETE FROM import_rows WHERE statement_id = %s", (sid,), commit=False)
        db.execute("DELETE FROM statements WHERE id = %s", (sid,), commit=False)
    for rel in (st.get("stored_path"), st.get("ocr_path")):
        if not rel:
            continue
        still = db.query("SELECT 1 FROM statements WHERE stored_path = %s OR ocr_path = %s LIMIT 1", (rel, rel), one=True)
        if not still:
            try:
                os.remove(abs_path(rel))
            except OSError:
                pass
    return deleted


def recover_interrupted():
    """At startup: background threads died with the old process, so reset their statements."""
    try:
        n1 = db.execute(
            """UPDATE statements SET status = 'error', updated_at = now(),
                   error_message = 'Parsing was interrupted by a server restart. Use Retry to parse again.'
               WHERE status = 'parsing'"""
        )
        n2 = db.execute("UPDATE statements SET status = 'previewed', updated_at = now() WHERE status = 'committing'")
        if n1 or n2:
            log.info("recovered interrupted statements: %s parsing, %s committing", n1, n2)
    except Exception:
        log.warning("recover_interrupted failed", exc_info=True)
    finally:
        try:
            db.close_db()
        except Exception:
            pass
