import logging

import psycopg2
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

import config
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_BYTES,
    )

    db.run_migrations()

    import accounts_api
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
    import transactions_api

    for module in (
        auth, settings_api, accounts_api, categories_api, statements_api,
        transactions_api, review_api, rules_api, merchants_api, reports_api, ai_api, budgets_api,
    ):
        app.register_blueprint(module.bp)

    app.before_request(auth.refresh_session_user)
    app.teardown_appcontext(db.close_db)

    with app.app_context():
        import importer
        importer.recover_interrupted()

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
