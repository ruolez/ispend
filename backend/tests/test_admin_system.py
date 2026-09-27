"""Admin › System: health, error details and stopping a stuck import."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()
_stubs.install_stripe()

from flask import Flask  # noqa: E402

import admin_api  # noqa: E402
import admin_system  # noqa: E402


class SystemTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "t"
        self.app.register_blueprint(admin_system.bp)
        FAKE.settings.clear()
        self.q = _stubs.Router(default={})
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, role="admin"):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"], s["role"] = 1, role
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x):
            return getattr(c, method)(path)

    def test_every_route_is_admin_only(self):
        for method, path in (("get", "/api/admin/system/health"), ("get", "/api/admin/system/errors/abc"),
                             ("post", "/api/admin/system/imports/5/fail"), ("get", "/api/admin/imports"),
                             ("get", "/api/admin/ai")):
            with self.subTest(path=path):
                self.assertEqual(self._call(method, path, role="user").status_code, 403)

    def test_health_survives_an_unreadable_volume_and_reports_every_part(self):
        """A dashboard must never 500 because a volume is unmounted."""
        self.q.routes = [("FROM statements\n", []), ("status IN ('parsing', 'committing') AND updated_at <", []),
                         ("GROUP BY 1 ORDER BY COUNT(*) DESC", []), ("ORDER BY received_at DESC LIMIT 5", []),
                         ("ORDER BY 2 DESC LIMIT 12", []), ("GROUP BY fingerprint", [])]
        with mock.patch.object(admin_api.os, "scandir", side_effect=OSError("not mounted")), \
                mock.patch.object(admin_system.shutil, "disk_usage", side_effect=OSError("gone")):
            res = self._call("get", "/api/admin/system/health?refresh=1")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(set(body), {"imports", "email", "stripe", "backups", "storage", "errors", "status"})
        self.assertEqual((body["storage"]["disk_scan_ok"], body["storage"]["disk_bytes"]), (False, None))
        self.assertEqual(body["email"]["status"], "warn", "no SMTP configured is worth a look")
        self.assertEqual(body["backups"]["status"], "warn", "no backup yet")

    def test_only_a_stuck_import_can_be_stopped(self):
        self.x.routes = [("UPDATE statements SET status = 'error'", None)]
        self.assertEqual(self._call("post", "/api/admin/system/imports/5/fail").status_code, 409)
        self.x = _stubs.Router([("UPDATE statements SET status = 'error'", {"user_id": 9})], default=1)
        res = self._call("post", "/api/admin/system/imports/5/fail")
        self.assertEqual(res.status_code, 200)
        sql, params = self.x.sql("UPDATE statements SET status = 'error'")[0]
        self.assertIn("status IN ('parsing', 'committing')", sql)
        self.assertEqual(params[1:], (5, admin_system.STUCK_MINUTES))
        self.assertEqual(self.x.sql("INSERT INTO audit_log")[0][1][3], 9, "audited against the person")

    def test_unknown_error_group_is_404(self):
        self.q = _stubs.Router()
        self.assertEqual(self._call("get", "/api/admin/system/errors/nope").status_code, 404)

    def test_statuses_combine_to_the_worst(self):
        self.assertEqual([admin_system._worst("ok", "warn"), admin_system._worst("ok", "error", "warn"),
                          admin_system._worst("ok")], ["warn", "error", "ok"])


if __name__ == "__main__":
    unittest.main()
