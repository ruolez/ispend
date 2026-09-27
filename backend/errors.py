"""A durable record of server errors, for the admin's System page.

Unhandled request errors, failed background jobs and failed timer runs used to exist only in the
container log. Each one now also lands in app_errors, grouped by a fingerprint (the exception type
and the innermost frame of our own code) so a bug that fires a thousand times reads as one line
with a count.

Written on its own short-lived connection: the request's connection may be sitting in an aborted
transaction, which is often exactly why we are here. Never raises.
"""

import hashlib
import logging
import os
import threading
import time
import traceback

import db

log = logging.getLogger(__name__)

SOURCES = ("request", "job", "tick")
MAX_MESSAGE = 500
MAX_TRACE = 8000
# The same fingerprint is stored at most once per this many seconds per worker; a hot loop must
# not turn the error log into the error.
RATE_SECONDS = 60
_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
_last = {}
_lock = threading.Lock()


def _own_frame(tb):
    """file:line of the innermost frame inside this codebase, else the innermost frame at all."""
    frames = traceback.extract_tb(tb) if tb else []
    ours = [f for f in frames if f.filename.startswith(_APP_ROOT) and "/site-packages/" not in f.filename]
    frame = (ours or frames or [None])[-1]
    if frame is None:
        return "unknown"
    return f"{os.path.relpath(frame.filename, _APP_ROOT)}:{frame.lineno}"


def fingerprint(exc):
    where = _own_frame(exc.__traceback__)
    return hashlib.sha1(f"{type(exc).__name__}@{where}".encode(), usedforsecurity=False).hexdigest()[:16], where


def _should_store(fp, now=None):
    now = now or time.monotonic()
    with _lock:
        if now - _last.get(fp, -RATE_SECONDS - 1) < RATE_SECONDS:
            return False
        _last[fp] = now
        return True


def record(source, exc, location=None, status=None, user_id=None):
    """Store one error. location: the route rule or job name that was running."""
    try:
        fp, where = fingerprint(exc)
        if not _should_store(fp):
            return False
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-MAX_TRACE:]
        conn = db.connect()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO app_errors (source, fingerprint, error_type, message, location, status,
                                               user_id, traceback)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (source if source in SOURCES else "request", fp, type(exc).__name__,
                     str(exc)[:MAX_MESSAGE], (location or where)[:200], status, user_id, trace))
        finally:
            conn.close()
        return True
    except Exception:
        log.warning("could not record an application error", exc_info=True)
        return False
