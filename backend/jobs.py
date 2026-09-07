"""Background work inside the gunicorn worker.

A fresh app context gives the thread its own `g`, so db.get_db() opens a
dedicated connection that is closed in `finally`. Threads are non-daemon so a
worker recycle (graceful timeout) waits for a running parse/OCR to finish.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from flask import current_app

import db

log = logging.getLogger(__name__)

# Two parses/OCR runs at a time per worker; further uploads queue instead of each taking a thread.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")


def spawn(fn, *args, **kwargs):
    app = current_app._get_current_object()

    def runner():
        with app.app_context():
            try:
                fn(*args, **kwargs)
            except Exception:
                log.exception("background job %s failed", fn.__name__)
            finally:
                db.close_db()

    return _POOL.submit(runner)
