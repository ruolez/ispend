import os

SECRET_KEY = os.environ["SECRET_KEY"]
ADMIN_INITIAL_PASSWORD = os.environ.get("ADMIN_INITIAL_PASSWORD", "admin")

POSTGRES = {
    "host": os.environ.get("POSTGRES_HOST", "postgres"),
    "dbname": os.environ.get("POSTGRES_DB", "ispend"),
    "user": os.environ.get("POSTGRES_USER", "ispend"),
    "password": os.environ["POSTGRES_PASSWORD"],
}

STATEMENTS_DIR = os.environ.get("STATEMENTS_DIR", "/data/statements")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "26214400"))
# Whole-application restore archives are far larger than a statement; nginx has its own limit.
MAX_RESTORE_BYTES = int(os.environ.get("MAX_RESTORE_BYTES", str(4 * 1024 ** 3)))
# Off by default: Flask streams the artifact, which keeps it 0600 and readable only by the app
# user. Turning this on hands the download to nginx (no gunicorn thread held for the duration of
# a multi-GB transfer) but requires the file to be world-readable inside the statements volume,
# because the nginx and backend images share no group. Worth it only for very large installs.
USE_X_ACCEL = os.environ.get("USE_X_ACCEL", "0") == "1"
OCR_TIMEOUT_SECONDS = int(os.environ.get("OCR_TIMEOUT_SECONDS", "600"))
OCR_LANGS = os.environ.get("OCR_LANGS", "eng")

OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_TIMEOUT = int(os.environ.get("OPENROUTER_TIMEOUT", "90"))
AI_BATCH_SIZE = int(os.environ.get("AI_BATCH_SIZE", "40"))

# Set to 1 when the app is served over HTTPS (install.sh does this for SSL installs).
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Chicago")
