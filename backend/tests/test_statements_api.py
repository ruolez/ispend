import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

TMP = tempfile.mkdtemp()
FAKE = _stubs.install(TMP)

from flask import Flask, session  # noqa: E402

import importer  # noqa: E402
import statements_api  # noqa: E402
from importer import pipeline  # noqa: E402


def make_app():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(statements_api.bp)
    return app


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        FAKE.calls.clear()
        FAKE.query_results.clear()
        FAKE.execute_results.clear()
        self.spawned = []
        self._spawn = statements_api.jobs.spawn
        statements_api.jobs.spawn = lambda fn, *a, **k: self.spawned.append((fn.__name__, a))

    def tearDown(self):
        statements_api.jobs.spawn = self._spawn

    def _upload(self, filename, data, account_id=None):
        FAKE.query_results.append(None)  # duplicate lookup
        FAKE.execute_results.append({"id": 42, "original_filename": filename, "file_kind": "csv"})
        with self.app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 1
            form = {"file": (io.BytesIO(data), filename)}
            if account_id:
                form["account_id"] = str(account_id)
            return c.post("/api/statements", data=form, content_type="multipart/form-data")

    def test_csv_upload_stores_file_and_spawns_parse(self):
        res = self._upload("chase.csv", b"Date,Description,Amount\n08/01/2024,Coffee,-4.50\n")
        body = res.get_json()
        self.assertEqual((res.status_code, body["id"], body["status"], body["duplicate_of"]), (201, 42, "parsing", None))
        self.assertEqual(self.spawned, [("parse_statement", (42,))])
        stored = [c for c in FAKE.calls if c[0] == "execute" and "INSERT INTO statements" in c[1]]
        self.assertEqual(len(stored), 1)
        user_dir = os.path.join(TMP, "1")
        self.assertTrue(os.path.isdir(user_dir) and any(f.endswith(".csv") for f in os.listdir(user_dir)))

    def test_unknown_kind_rejected(self):
        with self.app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 1
            res = c.post("/api/statements", data={"file": (io.BytesIO(b"\x00\x01\x02\xff"), "blob.bin")},
                         content_type="multipart/form-data")
        self.assertEqual((res.status_code, "Unsupported" in res.get_json()["error"]), (400, True))

    def test_missing_file(self):
        with self.app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 1
            res = c.post("/api/statements", data={}, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)

    def test_requires_login(self):
        with self.app.test_client() as c:
            res = c.post("/api/statements", data={}, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 401)


class CommitStateTest(unittest.TestCase):
    def setUp(self):
        FAKE.calls.clear()
        FAKE.query_results.clear()
        FAKE.execute_results.clear()

    def test_commit_refuses_when_not_previewed(self):
        FAKE.query_results.append({"id": 5, "status": "committed", "user_id": 1})
        FAKE.query_results.append({"id": 3, "user_id": 1, "currency": "USD"})
        FAKE.execute_results.append(0)  # lock UPDATE affected no rows
        with self.assertRaises(pipeline.ImportError_) as ctx:
            importer.commit_statement(5, 3)
        self.assertEqual(ctx.exception.status, 409)

    def test_commit_requires_account(self):
        FAKE.query_results.append({"id": 5, "status": "previewed", "user_id": 1})
        FAKE.query_results.append(None)
        with self.assertRaises(pipeline.ImportError_) as ctx:
            importer.commit_statement(5, 99)
        self.assertEqual(ctx.exception.status, 400)

    def test_queries_are_user_scoped(self):
        app = make_app()
        FAKE.query_results.append(None)
        with app.test_client() as c:
            with c.session_transaction() as s:
                s["user_id"] = 7
            res = c.get("/api/statements/123")
        self.assertEqual(res.status_code, 404)
        q = [c for c in FAKE.calls if c[0] == "query"][0]
        self.assertIn("s.user_id = %s", q[1])
        self.assertEqual(q[2], (123, 7))


class RecoverTest(unittest.TestCase):
    def test_recover_marks_parsing_error_and_committing_previewed(self):
        FAKE.calls.clear()
        FAKE.execute_results.extend([2, 1])
        importer.recover_interrupted()
        sqls = [c[1] for c in FAKE.calls if c[0] == "execute"]
        self.assertEqual(len(sqls), 2)
        self.assertIn("status = 'error'", sqls[0])
        self.assertIn("WHERE status = 'parsing'", sqls[0])
        self.assertIn("status = 'previewed'", sqls[1])


if __name__ == "__main__":
    unittest.main()


class PhrasesForTest(unittest.TestCase):
    """Cardholder phrases saved at staging are what commit and dupe recomputation use."""
    DESCS = [f"Shop {i} Eugene Braverman RAW{i}" for i in range(20)]

    def test_saved_phrases_are_reused(self):
        st = {"stats": {"stripped_phrases": ["SOMEONE ELSE"]}}
        self.assertEqual(pipeline._phrases_for(st, self.DESCS), ["SOMEONE ELSE"])

    def test_recomputed_when_absent(self):
        self.assertEqual(pipeline._phrases_for({"stats": {}}, self.DESCS), ["EUGENE BRAVERMAN"])
        self.assertEqual(pipeline._phrases_for(None, self.DESCS), ["EUGENE BRAVERMAN"])
