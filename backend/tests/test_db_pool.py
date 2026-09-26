"""db.py's per-process connection reuse: requests take an idle connection instead of opening one each.

The real db module is loaded under another name (other test modules install a stub as "db"), with
connect() returning fake connections."""
import importlib.util
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2.extensions as ext  # noqa: E402
from flask import Flask  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_real", os.path.join(os.path.dirname(__file__), "..", "db.py"))
dbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dbr)


class FakeConn:
    made = 0

    def __init__(self, *a, **k):
        FakeConn.made += 1
        self.closed = 0
        self.status = ext.TRANSACTION_STATUS_IDLE
        self.rollbacks = 0

    def get_transaction_status(self):
        return self.status

    def rollback(self):
        self.rollbacks += 1
        self.status = ext.TRANSACTION_STATUS_IDLE

    def close(self):
        self.closed = 1


class PoolTest(unittest.TestCase):
    def setUp(self):
        FakeConn.made = 0
        self.app = Flask(__name__)
        self.direct = mock.patch.object(dbr, "connect", side_effect=FakeConn)
        self.direct.start()
        dbr._reset_pool()

    def tearDown(self):
        self.direct.stop()
        dbr._reset_pool()

    def request(self, fn=None):
        with self.app.app_context():
            conn = dbr.get_db()
            if fn:
                fn(conn)
            dbr.close_db()
            return conn

    def test_consecutive_requests_share_one_connection(self):
        a, b = self.request(), self.request()
        self.assertEqual((a is b, FakeConn.made, a.closed), (True, 1, 0))

    def test_an_open_transaction_is_rolled_back_before_reuse(self):
        def leave_open(conn):
            conn.status = ext.TRANSACTION_STATUS_INTRANS
        a = self.request(leave_open)
        self.assertEqual((a.rollbacks, self.request() is a), (1, True))

    def test_a_broken_connection_is_discarded(self):
        def break_it(conn):
            conn.status = ext.TRANSACTION_STATUS_UNKNOWN
        a = self.request(break_it)
        b = self.request()
        self.assertEqual((a.closed, b is a, FakeConn.made), (1, False, 2))

    def test_busy_requests_open_more_and_only_pool_idle_are_kept(self):
        conns = [dbr._take() for _ in range(dbr.POOL_IDLE + 1)]
        for c in conns:
            dbr._give_back(c)
        self.assertEqual((FakeConn.made, sum(c.closed for c in conns)), (dbr.POOL_IDLE + 1, 1))

    def test_a_forked_worker_never_reuses_its_parents_connections(self):
        parent = self.request()
        with mock.patch.object(dbr.os, "getpid", return_value=os.getpid() + 1):
            child = self.request()
        self.assertEqual((child is parent, parent.closed), (False, 0))


class SettingsMemoTest(unittest.TestCase):
    """get_setting is read many times per request (entitlement, billing, AI flags); one query per key."""

    def setUp(self):
        self.app = Flask(__name__)

    def test_repeat_reads_in_one_request_hit_the_database_once_and_writes_update_the_memo(self):
        with self.app.test_request_context(), \
                mock.patch.object(dbr, "query", return_value={"value": "7"}) as q, \
                mock.patch.object(dbr, "execute", return_value=1):
            first = [dbr.get_setting("billing_trial_days"), dbr.get_setting("billing_trial_days")]
            dbr.set_setting("billing_trial_days", "9")
            after = dbr.get_setting("billing_trial_days")
        self.assertEqual((first, after, q.call_count), (["7", "7"], "9", 1))

    def test_a_missing_setting_is_memoised_too_and_defaults_still_apply(self):
        with self.app.test_request_context(), mock.patch.object(dbr, "query", return_value=None) as q:
            got = [dbr.get_setting("nope", "d1"), dbr.get_setting("nope", "d2")]
        self.assertEqual((got, q.call_count), (["d1", "d2"], 1))

    def test_outside_a_request_nothing_is_memoised(self):
        with self.app.app_context(), mock.patch.object(dbr, "query", return_value={"value": "1"}) as q:
            dbr.get_setting("k"), dbr.get_setting("k")
        self.assertEqual(q.call_count, 2)


if __name__ == "__main__":
    unittest.main()
