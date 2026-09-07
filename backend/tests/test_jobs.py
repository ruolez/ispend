import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask, g  # noqa: E402

import jobs  # noqa: E402


class SpawnTest(unittest.TestCase):
    def test_job_runs_in_an_app_context_and_closes_its_connection(self):
        app = Flask(__name__)
        seen = {}
        done = threading.Event()

        def work(x):
            g.marker = x
            seen["x"] = x
            seen["has_ctx"] = bool(g)
            done.set()

        with app.app_context(), mock.patch.object(jobs.db, "close_db") as close_db:
            future = jobs.spawn(work, 42)
            future.result(timeout=5)
        self.assertTrue(done.wait(5))
        self.assertEqual((seen["x"], seen["has_ctx"]), (42, True))
        close_db.assert_called_once()

    def test_failures_are_logged_not_raised(self):
        app = Flask(__name__)

        def boom():
            raise RuntimeError("nope")

        with app.app_context(), mock.patch.object(jobs.db, "close_db"), self.assertLogs(jobs.log, level="ERROR") as logs:
            jobs.spawn(boom).result(timeout=5)
        self.assertTrue(any("background job boom failed" in m for m in logs.output))

    def test_pool_is_bounded(self):
        self.assertEqual(jobs._POOL._max_workers, 2)


if __name__ == "__main__":
    unittest.main()
