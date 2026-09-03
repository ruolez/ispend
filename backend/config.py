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
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
OCR_TIMEOUT_SECONDS = int(os.environ.get("OCR_TIMEOUT_SECONDS", 600))
OCR_LANGS = os.environ.get("OCR_LANGS", "eng")

OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_TIMEOUT = int(os.environ.get("OPENROUTER_TIMEOUT", 90))
AI_BATCH_SIZE = int(os.environ.get("AI_BATCH_SIZE", 40))

APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Chicago")
