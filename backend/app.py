import logging

import psycopg2
import psycopg2.errors
from flask import Flask, abort, jsonify, request
from werkzeug.exceptions import HTTPException

import billing_tick
import config
import db
import entitlement
from backup import restore as backup_restore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
        MAX_CONTENT_LENGTH=max(config.MAX_UPLOAD_BYTES, config.MAX_RESTORE_BYTES),
    )

    db.run_migrations()

    import accounts_api
    import admin_api
    import admin_backup
    import admin_billing
    import billing_api
    import ai_api
    import auth
    import categories_api
    import budgets_api
    import merchants_api
    import reports_api
    import review_api
    import rules_api
    import settings_api
    import statements_api
    import tags_api
    import transactions_api

    for module in (
        auth, settings_api, accounts_api, categories_api, statements_api,
        transactions_api, review_api, rules_api, merchants_api, reports_api, ai_api, budgets_api, tags_api,
        admin_api, admin_backup, admin_billing, billing_api,
    ):
        app.register_blueprint(module.bp)

    app.before_request(auth.refresh_session_user)

    @app.before_request
    def _guards():
        # MAX_CONTENT_LENGTH is app-wide and had to be raised for restore uploads, so every other
        # path keeps its cheap pre-read 413 here. (store_upload enforces the real limit itself.)
        if not request.path.startswith("/api/admin/backup/"):
            length = request.content_length
            if length is not None and length > config.MAX_UPLOAD_BYTES:
                abort(413)
        # A restore truncates and reloads every table; other requests would see a half-empty
        # database or block on its locks. A sentinel file is used because a DB flag would itself
        # be truncated and a process global would not cross the gunicorn workers.
        if (request.path.startswith("/api/")
                and not request.path.startswith("/api/admin/backup/restore/")
                and request.path != "/api/health"
                and backup_restore.restore_in_progress()):
            return jsonify({"error": "A restore is in progress. Try again in a few minutes."}), 503
        # gunicorn --preload runs create_app() in the master, so a thread started there dies in
        # the fork. Starting it from the first request puts it in the worker.
        billing_tick.start(app)
        # Blocks every non-GET outside an explicit allowlist. Deny-by-default, because a
        # decorator is opt-in and "we forgot to decorate the new endpoint" is what leaks the
        # product.
        return entitlement.enforce_write_access()

    app.teardown_appcontext(db.close_db)

    with app.app_context():
        import importer
        importer.recover_interrupted()
        backup_restore.clear_stale_sentinel()
        db.execute("""UPDATE backup_jobs SET status = 'error', finished_at = now(),
                             error_message = 'Interrupted by a server restart'
                       WHERE status IN ('queued','running')""")

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "File is too large (limit %d MB)" % (config.MAX_UPLOAD_BYTES // (1024 * 1024))}), 413

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(psycopg2.errors.RaiseException)
    def db_rule(e):
        return jsonify({"error": (e.diag.message_primary or "Rejected by a database rule").strip()}), 400

    @app.errorhandler(ValueError)
    @app.errorhandler(psycopg2.DataError)
    def bad_value(e):
        app.logger.info("Rejected request value: %s", str(e).strip().splitlines()[0] if str(e) else type(e).__name__)
        return jsonify({"error": "Invalid value in request"}), 400

    @app.errorhandler(Exception)
    def unhandled(e):
        if isinstance(e, HTTPException):
            return jsonify({"error": e.description or e.name}), e.code
        app.logger.exception("Unhandled error")
        return jsonify({"error": "Something went wrong on the server. The details were logged."}), 500

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    return app
