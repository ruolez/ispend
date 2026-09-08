import io
import json
import os
import sys
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import admin_backup  # noqa: E402
import util  # noqa: E402
from backup import archive, compat, fixups, tables  # noqa: E402

ROUTES = [
    ("get", "/api/admin/backup"), ("post", "/api/admin/backup"),
    ("get", "/api/admin/backup/1"), ("delete", "/api/admin/backup/1"),
    ("get", "/api/admin/backup/1/download"), ("post", "/api/admin/backup/upload"),
    ("post", "/api/admin/backup/restore"),
]


class AdminBackupApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(admin_backup.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, uid=1, role="admin", raw=None):
        c = self.app.test_client()
        if uid is not None:
            with c.session_transaction() as s:
                s["user_id"] = uid
                s["role"] = role
                s["username"] = "admin"
        kwargs = {}
        if raw is not None:
            kwargs = {"data": raw, "content_type": "application/octet-stream"}
        elif body is not None:
            kwargs = {"data": json.dumps(body), "content_type": "application/json"}
        with mock.patch.object(FAKE, "query", side_effect=self.q), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(admin_backup, "db", FAKE), \
                mock.patch.object(admin_backup.jobs, "spawn") as spawn:
            self.spawn = spawn
            return getattr(c, method)(path, **kwargs)

    def test_every_route_is_admin_only(self):
        for method, path in ROUTES:
            with self.subTest(route=f"{method} {path}"):
                self.x.calls.clear()
                self.assertEqual(self._call(method, path, uid=2, role="user").status_code, 403)
                self.assertEqual(self._call(method, path, uid=None).status_code, 401)
                self.assertEqual([c for c in self.x.calls if "INSERT INTO backup_jobs" in c[0]], [])

    def test_starting_a_backup_queues_one_job(self):
        self.q.routes.append(("status IN ('queued','running')", None))
        self.x.routes.append(("INSERT INTO backup_jobs", {"id": 4}))
        with mock.patch.object(admin_backup, "_free_space_ok", return_value=(True, None)):
            res = self._call("post", "/api/admin/backup", {})
        self.assertEqual((res.status_code, res.get_json()["id"]), (202, 4))
        self.assertEqual(self.spawn.call_count, 1)

    def test_a_second_backup_is_refused_while_one_runs(self):
        self.q.routes.append(("status IN ('queued','running')", {"id": 3}))
        res = self._call("post", "/api/admin/backup", {})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.x.sql("INSERT INTO backup_jobs"), [])

    def test_backup_is_refused_without_headroom(self):
        self.q.routes.append(("status IN ('queued','running')", None))
        with mock.patch.object(admin_backup, "_free_space_ok", return_value=(False, (5_000_000, 100))):
            res = self._call("post", "/api/admin/backup", {})
        self.assertEqual(res.status_code, 507)
        self.assertIn("free", res.get_json()["error"])

    def test_download_404s_when_the_artifact_is_gone(self):
        self.q.routes.append(("FROM backup_jobs", {"id": 1, "status": "done", "filename": "x.zip",
                                                   "file_path": "/nope/x.zip", "kind": "backup"}))
        self.assertEqual(self._call("get", "/api/admin/backup/1/download").status_code, 404)

    def test_download_hands_off_to_nginx_when_x_accel_is_on(self):
        row = {"id": 1, "status": "done", "filename": "x.zip", "kind": "backup",
               "file_path": "/tmp/x.zip"}
        self.q.routes.append(("FROM backup_jobs", row))
        with open("/tmp/x.zip", "wb") as f:
            f.write(b"zip")
        with mock.patch.object(admin_backup.config, "USE_X_ACCEL", True):
            res = self._call("get", "/api/admin/backup/1/download")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["X-Accel-Redirect"], "/protected/_backups/x.zip")
        self.assertIn('attachment; filename="x.zip"', res.headers["Content-Disposition"])
        self.assertEqual(res.get_data(), b"{}\n")  # nginx supplies the body

    def test_restore_requires_the_typed_phrase(self):
        for confirm in (None, "", "restore", "yes", "RESTORE "):
            with self.subTest(confirm=confirm):
                self.x.calls.clear()
                res = self._call("post", "/api/admin/backup/restore",
                                 {"upload_id": "x", "confirm": confirm})
                self.assertEqual((res.status_code, res.get_json()),
                                 (400, {"error": "Type RESTORE to confirm"}))
                self.assertEqual([c for c in self.x.calls if "backup_jobs" in c[0]], [])

    def test_restore_is_refused_while_a_statement_is_parsing(self):
        upload_id = "1e2d3c4b-0000-4000-8000-000000000000"
        path = admin_backup._staged_path(upload_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x")
        try:
            self.q.routes.append(("status IN ('queued','running')", None))
            self.q.routes.append(("FROM statements", {"?column?": 1}))
            res = self._call("post", "/api/admin/backup/restore",
                             {"upload_id": upload_id, "confirm": "RESTORE"})
            self.assertEqual(res.status_code, 409)
            self.assertIn("imported", res.get_json()["error"])
            self.assertEqual(self.x.sql("INSERT INTO backup_jobs"), [])
        finally:
            os.unlink(path)

    def test_restore_rejects_an_unknown_upload(self):
        res = self._call("post", "/api/admin/backup/restore",
                         {"upload_id": "../../etc/passwd", "confirm": "RESTORE"})
        self.assertEqual(res.status_code, 404)

    def test_restore_status_accepts_the_token_without_a_session(self):
        """The admin's own session dies mid-restore, so the token is the only way they can see
        how it ended."""
        row = {"id": 9, "kind": "restore", "status": "running", "token": "good-token"}
        self.q.routes.append(("FROM backup_jobs", row))
        self.assertEqual(self._call("get", "/api/admin/backup/restore/9?token=good-token",
                                    uid=None).status_code, 200)
        self.assertEqual(self._call("get", "/api/admin/backup/restore/9?token=wrong",
                                    uid=None).status_code, 404)
        self.assertEqual(self._call("get", "/api/admin/backup/restore/9", uid=None).status_code, 404)


class ArchiveTest(unittest.TestCase):
    def _build(self):
        buf = io.BytesIO()
        with archive.ArchiveWriter(buf) as arc:
            with arc.table_writer("users") as w:
                w.write(b"1\tamy\n2\tbob\n")
                self.sha, self.rows = w.sha256, w.rows
            arc.add_file("files/3/deadbeef.pdf", io.BytesIO(b"%PDF-1.4"))
            arc.add_json(archive.FILES_INDEX, [{"path": "3/deadbeef.pdf", "sha256": "x", "bytes": 8}])
            arc.add_json(archive.MANIFEST, {
                "format": archive.FORMAT, "format_version": 1,
                "app": {"migrations": ["001_init.sql"]},
                "tables": [{"name": "users", "columns": ["id", "username"], "rows": 2,
                            "sha256": self.sha}],
            })
        return buf

    def test_rows_are_counted_and_hashed(self):
        self._build()
        self.assertEqual(self.rows, 2)

    def test_manifest_is_the_last_member_and_uploads_are_not_recompressed(self):
        buf = self._build()
        zf = zipfile.ZipFile(buf)
        self.assertEqual(zf.namelist()[-1], archive.MANIFEST)
        self.assertEqual(zf.getinfo("files/3/deadbeef.pdf").compress_type, zipfile.ZIP_STORED)
        self.assertEqual(zf.getinfo("db/users.copy").compress_type, zipfile.ZIP_DEFLATED)

    def test_a_flipped_byte_is_detected(self):
        buf = self._build()
        with archive.ArchiveReader(buf) as arc, self.assertRaises(archive.ArchiveError):
            with arc.table_reader("users", "0" * 64) as reader:
                reader.read()

    def test_an_archive_without_a_manifest_is_refused(self):
        """A build that crashed leaves exactly this, and it must not look restorable."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("db/users.copy", "1\tamy\n")
        with self.assertRaises(archive.ArchiveError) as ctx:
            archive.ArchiveReader(buf)
        self.assertIn("manifest.json is missing", str(ctx.exception))

    def test_a_non_zip_is_refused(self):
        with self.assertRaises(archive.ArchiveError):
            archive.ArchiveReader(io.BytesIO(b"not a zip at all"))


class CompatTest(unittest.TestCase):
    LIVE = {"users": {"id": {"nullable": False, "has_default": True},
                      "username": {"nullable": False, "has_default": False},
                      "status": {"nullable": False, "has_default": True}}}

    def _manifest(self, migrations, columns):
        return {"app": {"migrations": migrations},
                "tables": [{"name": "users", "columns": columns, "rows": 1}]}

    def test_equal_histories_are_accepted(self):
        plan = compat.check(self._manifest(["001_init.sql"], ["id", "username", "status"]),
                            ["001_init.sql"], self.LIVE)
        self.assertEqual(plan.missing_migrations, [])

    def test_a_newer_archive_is_refused_by_name(self):
        with self.assertRaises(archive.ArchiveError) as ctx:
            compat.check(self._manifest(["001_init.sql", "099_future.sql"], ["id", "username", "status"]),
                         ["001_init.sql"], self.LIVE)
        self.assertIn("099_future.sql", str(ctx.exception))

    def test_an_older_archive_is_accepted_when_the_new_column_can_fill_itself_in(self):
        plan = compat.check(self._manifest(["001_init.sql"], ["id", "username"]),
                            ["001_init.sql", "010_user_status.sql"], self.LIVE)
        self.assertEqual(plan.missing_migrations, ["010_user_status.sql"])
        self.assertEqual(plan.columns["users"], ["id", "username"])

    def test_a_required_column_the_archive_lacks_is_refused(self):
        live = {"users": {**self.LIVE["users"],
                          "email": {"nullable": False, "has_default": False}}}
        with self.assertRaises(archive.ArchiveError) as ctx:
            compat.check(self._manifest(["001_init.sql"], ["id", "username", "status"]),
                         ["001_init.sql", "011.sql"], live)
        self.assertIn("users.email", str(ctx.exception))

    def test_a_column_this_version_dropped_is_refused(self):
        with self.assertRaises(archive.ArchiveError) as ctx:
            compat.check(self._manifest(["001_init.sql"], ["id", "username", "is_active"]),
                         ["001_init.sql"], self.LIVE)
        self.assertIn("is_active", str(ctx.exception))


class TablesTest(unittest.TestCase):
    def test_dependencies_come_before_dependents(self):
        depends = {
            "accounts": ["users"], "categories": ["users"], "tags": ["users"],
            "recurring_dismissals": ["users"], "ai_calls": ["users"], "insights": ["users"],
            "audit_log": ["users"], "rules": ["users", "accounts", "categories"],
            "statements": ["users", "accounts"], "budgets": ["users", "categories"],
            "merchant_memory": ["users", "categories"],
            "transactions": ["users", "accounts", "statements", "categories", "rules"],
            "import_rows": ["statements", "transactions", "categories"],
            "transaction_events": ["transactions", "users"],
            "anomaly_dismissals": ["users", "transactions"],
            "transaction_tags": ["transactions", "tags"],
            "transaction_splits": ["transactions", "categories"],
        }
        position = {t: i for i, t in enumerate(tables.TABLE_ORDER)}
        for table, parents in depends.items():
            for parent in parents:
                self.assertLess(position[parent], position[table], f"{parent} must load before {table}")

    def test_no_duplicates_and_self_fks_are_known_tables(self):
        self.assertEqual(len(tables.TABLE_ORDER), len(set(tables.TABLE_ORDER)))
        self.assertTrue(set(tables.SELF_FK) <= set(tables.TABLE_ORDER))
        self.assertTrue(set(tables.NO_SEQUENCE) <= set(tables.TABLE_ORDER))

    def test_truncate_runs_children_first(self):
        self.assertEqual(tables.truncate_order()[0], tables.TABLE_ORDER[-1])

    def test_every_migration_is_classified(self):
        here = os.path.join(os.path.dirname(__file__), "..", "migrations")
        migrations = {f for f in os.listdir(here) if f.endswith(".sql")}
        self.assertEqual(migrations - set(fixups.FIXUPS) - fixups.NO_FIXUP_NEEDED, set())


if __name__ == "__main__":
    unittest.main()
