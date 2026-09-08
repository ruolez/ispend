"""Backup -> restore round-trip against two real scratch databases.

The stubbed db module cannot exercise any of what actually matters here: COPY round-tripping
NUMERIC/TEXT[]/JSONB exactly, the deferred split constraint trigger firing at COMMIT, the
self-FK two-pass, or sequence restoration. Run on its own:

    ISPEND_TEST_DSN=postgresql://ispend:<password>@postgres/ispend \\
        python -m unittest tests.test_backup_roundtrip
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from urllib.parse import urlparse

DSN = os.environ.get("ISPEND_TEST_DSN")


def _real_db_available():
    mod = sys.modules.get("db")
    return DSN and (mod is None or type(mod).__name__ != "FakeDB")


@unittest.skipUnless(_real_db_available(), "set ISPEND_TEST_DSN and run this file on its own")
class BackupRoundTripTest(unittest.TestCase):
    """Seeds a row in EVERY exported table, exports, restores into a second database, and
    compares table by table."""

    @classmethod
    def setUpClass(cls):
        import psycopg2

        u = urlparse(DSN)
        cls.base = {"host": u.hostname, "port": u.port or 5432, "user": u.username,
                    "password": u.password, "dbname": u.path.lstrip("/")}
        cls.src = f"ispend_bk_src_{os.getpid()}"
        cls.dst = f"ispend_bk_dst_{os.getpid()}"
        admin = psycopg2.connect(**cls.base)
        admin.autocommit = True
        with admin.cursor() as cur:
            for name in (cls.src, cls.dst):
                cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
                cur.execute(f'CREATE DATABASE "{name}"')
        admin.close()

        os.environ.setdefault("SECRET_KEY", "integration")
        os.environ["POSTGRES_PASSWORD"] = cls.base["password"] or ""
        import config
        cls.config = config
        cls.tmp_src = tempfile.mkdtemp()
        cls.tmp_dst = tempfile.mkdtemp()
        cls.archive_path = os.path.join(tempfile.mkdtemp(), "roundtrip.zip")

        from flask import Flask
        cls.app = Flask(__name__)
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

        cls._use(cls.src, cls.tmp_src)
        import db
        cls.db = db
        db.run_migrations()
        cls._seed()

        from backup import export, restore, tables
        cls.tables = tables
        cls.empty_tables = [t for t in tables.TABLE_ORDER
                            if not db.query(f"SELECT 1 FROM {t} LIMIT 1", one=True)]
        cls.manifest = export.export_archive(cls.archive_path)
        cls.before = cls._dump_all_for(db)
        cls.src_epoch = db.get_setting("session_epoch")

        # Schema from code, data from the archive.
        cls._use(cls.dst, cls.tmp_dst)
        db.run_migrations()
        cls.warnings = restore.restore_archive(cls.archive_path)
        cls.after = cls._dump_all_for(db)

    @classmethod
    def _use(cls, dbname, statements_dir):
        import db
        try:
            db.close_db()
        except Exception:
            pass
        cls.config.POSTGRES = {**cls.base, "dbname": dbname}
        cls.config.STATEMENTS_DIR = statements_dir

    @classmethod
    def tearDownClass(cls):
        import psycopg2
        try:
            cls.db.close_db()
        except Exception:
            pass
        cls.ctx.pop()
        admin = psycopg2.connect(**cls.base)
        admin.autocommit = True
        with admin.cursor() as cur:
            for name in (cls.src, cls.dst):
                cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()
        for d in (cls.tmp_src, cls.tmp_dst):
            shutil.rmtree(d, ignore_errors=True)

    # ---------- seeding ----------

    @classmethod
    def _seed(cls):
        db = cls.db
        import seed_categories
        uid = db.execute("INSERT INTO users (username, password_hash, role, preferences) "
                         "VALUES ('rt_user', 'hash$abc', 'user', %s) RETURNING id",
                         (json.dumps({"theme": "dark", "saved_views": [{"id": "v1", "name": "Café ☕"}]}),),
                         returning=True)["id"]
        admin_id = db.execute("INSERT INTO users (username, password_hash, role) "
                              "VALUES ('rt_admin', 'hash$def', 'admin') RETURNING id", returning=True)["id"]
        locked = db.execute("INSERT INTO users (username, password_hash, status, locked_at, lock_reason) "
                            "VALUES ('rt_locked', 'h', 'locked', now(), 'because') RETURNING id",
                            returning=True)["id"]
        cls.uid, cls.admin_id, cls.locked_id = uid, admin_id, locked
        seed_categories.seed_for_user(db.get_db(), uid)

        # settings: NULL must stay distinct from '', and control characters must survive COPY
        db.execute("INSERT INTO settings (key, value) VALUES ('openrouter_api_key', 'sk-secret')")
        db.execute("INSERT INTO settings (key, value) VALUES (%s, %s)",
                   (f"u{uid}:openrouter_model", "vendor/model"))
        db.execute("INSERT INTO settings (key, value) VALUES ('rt_null', NULL)")
        db.execute("INSERT INTO settings (key, value) VALUES ('rt_empty', '')")
        db.execute("INSERT INTO settings (key, value) VALUES (%s, %s)",
                   ("rt_weird", "tab\there\nnewline\\backslash \\N literal \"quote\" ünïcode ☕"))

        acct = db.execute("INSERT INTO accounts (user_id, name, account_type, currency) "
                          "VALUES (%s, 'Chequing', 'checking', 'CAD') RETURNING id", (uid,), returning=True)["id"]
        cls.acct = acct
        # 3-deep category chain exercises the self-FK two-pass
        top = db.execute("INSERT INTO categories (user_id, name, slug, kind) "
                         "VALUES (%s, 'RT Top', 'rt-top', 'expense') RETURNING id", (uid,), returning=True)["id"]
        mid = db.execute("INSERT INTO categories (user_id, parent_id, name, slug, kind) "
                         "VALUES (%s, %s, 'RT Mid', 'rt-mid', 'expense') RETURNING id",
                         (uid, top), returning=True)["id"]
        cls.cat = mid
        xfer = db.execute("INSERT INTO categories (user_id, name, slug, kind) "
                          "VALUES (%s, 'RT Transfer', 'rt-transfer', 'transfer') RETURNING id",
                          (uid,), returning=True)["id"]
        tag = db.execute("INSERT INTO tags (user_id, name) VALUES (%s, 'rt-tag') RETURNING id",
                         (uid,), returning=True)["id"]
        rule = db.execute(
            """INSERT INTO rules (user_id, name, match_type, match_field, pattern, category_id)
               VALUES (%s, 'rt rule', 'regex', 'description_clean', %s, %s) RETURNING id""",
            (uid, r"^ACME\s+\d+$", mid), returning=True)["id"]

        sha = "a" * 64
        os.makedirs(os.path.join(cls.tmp_src, str(uid)), exist_ok=True)
        for suffix, body in ((".pdf", b"%PDF-1.4 rt original"), (".ocr.pdf", b"%PDF-1.4 rt ocr")):
            with open(os.path.join(cls.tmp_src, str(uid), sha + suffix), "wb") as f:
                f.write(body)
        st1 = db.execute(
            """INSERT INTO statements (user_id, account_id, original_filename, stored_path, ocr_path,
                                       file_sha256, file_size, file_kind, status, stats)
               VALUES (%s, %s, 'rt.pdf', %s, %s, %s, 21, 'pdf', 'committed', '{}') RETURNING id""",
            (uid, acct, f"{uid}/{sha}.pdf", f"{uid}/{sha}.ocr.pdf", sha), returning=True)["id"]
        # second row reusing the SAME blob: the archive must store it once
        st2 = db.execute(
            """INSERT INTO statements (user_id, account_id, original_filename, stored_path,
                                       file_sha256, file_size, file_kind, status, stats)
               VALUES (%s, %s, 'rt-again.pdf', %s, %s, 21, 'pdf', 'previewed', '{}') RETURNING id""",
            (uid, acct, f"{uid}/{sha}.pdf", sha), returning=True)["id"]
        cls.st1, cls.st2 = st1, st2

        def txn(desc, amount, fingerprint, **kw):
            cols = {"user_id": uid, "account_id": acct, "statement_id": st1, "txn_date": "2026-01-05",
                    "amount": amount, "currency": "CAD", "description_raw": desc,
                    "description_clean": desc, "merchant_key": desc.lower(), "merchant_name": desc,
                    "fingerprint": fingerprint, **kw}
            keys = ", ".join(cols)
            ph = ", ".join(["%s"] * len(cols))
            return db.execute(f"INSERT INTO transactions ({keys}) VALUES ({ph}) RETURNING id",
                              tuple(cols.values()), returning=True)["id"]

        # NUMERIC edges: a float round-trip would break the exact-equality split trigger
        cls.t_small = txn("Penny", Decimal("-0.01"), "fp-penny")
        cls.t_big = txn("Huge", Decimal("9999999999.99"), "fp-huge", category_confidence=Decimal("0.999"))
        cls.t_split = txn("Split parent", Decimal("-100.00"), "fp-split",
                          category_id=mid, category_status="confirmed", category_source="manual",
                          raw=json.dumps({"memo": "raw ☕", "n": 1}))
        a = txn("Transfer out", Decimal("-50.00"), "fp-xfer-a", category_id=xfer, category_status="confirmed")
        b = txn("Transfer in", Decimal("50.00"), "fp-xfer-b", category_id=xfer, category_status="confirmed")
        db.execute("UPDATE transactions SET transfer_pair_id = %s WHERE id = %s", (b, a))
        db.execute("UPDATE transactions SET transfer_pair_id = %s WHERE id = %s", (a, b))
        cls.t_a, cls.t_b = a, b

        with db.transaction():
            for amt in (Decimal("-33.33"), Decimal("-66.67")):
                db.execute("INSERT INTO transaction_splits (transaction_id, category_id, amount) "
                           "VALUES (%s, %s, %s)", (cls.t_split, mid, amt), commit=False)
        db.execute("INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (%s, %s)", (cls.t_split, tag))
        db.execute("INSERT INTO transaction_events (transaction_id, kind, detail, user_id) "
                   "VALUES (%s, 'manual', %s, NULL)", (cls.t_split, json.dumps({"note": "orphan actor"})))
        db.execute("""INSERT INTO import_rows (statement_id, row_index, raw, problems, duplicate_of,
                                               description, amount, category_rule_id)
                      VALUES (%s, 0, %s, %s, %s, 'staged', -1.00, %s)""",
                   (st2, json.dumps({"c": ["x", None]}),
                    ['has "quote"', "has ,comma", "has \\backslash", "has {brace}", ""],
                    cls.t_small, rule))
        db.execute("INSERT INTO budgets (user_id, category_id, month, amount) "
                   "VALUES (%s, %s, '2026-01-01', 250.00)", (uid, mid))
        db.execute("INSERT INTO merchant_memory (user_id, merchant_key, category_id) "
                   "VALUES (%s, 'penny', %s)", (uid, mid))
        db.execute("INSERT INTO insights (user_id, period_start, period_end, model, content) "
                   "VALUES (%s, '2026-01-01', '2026-01-31', 'm', 'markdown ☕')", (uid,))
        db.execute("INSERT INTO recurring_dismissals (user_id, merchant_key) VALUES (%s, 'penny')", (uid,))
        db.execute("INSERT INTO anomaly_dismissals (user_id, transaction_id) VALUES (%s, %s)", (uid, cls.t_big))
        db.execute("INSERT INTO ai_calls (user_id, purpose, model, status) VALUES (NULL, 'test', 'm', 'ok')")
        db.execute("INSERT INTO audit_log (user_id, action, detail) VALUES (NULL, 'user.purge', %s)",
                   (json.dumps({"username": "gone"}),))
        db.execute("INSERT INTO audit_log (user_id, action) VALUES (%s, 'auth.login')", (admin_id,))

    # ---------- the round trip ----------

    def test_the_seed_covers_every_exported_table(self):
        """Otherwise the comparison below proves nothing about the tables it skipped."""
        self.assertEqual(self.empty_tables, [])

    def test_every_table_comes_back_identical(self):
        self.assertEqual(self.warnings, [])
        self.maxDiff = None
        for table in self.tables.TABLE_ORDER:
            self.assertEqual(self.after[table], self.before[table],
                             f"{table} differs after the round trip")

    def test_a_shared_blob_is_stored_once_and_files_come_back_byte_for_byte(self):
        paths = [f["path"] for f in self._files_index()]
        # Two statements rows reference the same content-addressed file.
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(sorted(paths),
                         sorted([f"{self.uid}/{'a' * 64}.ocr.pdf", f"{self.uid}/{'a' * 64}.pdf"]))
        for rel in paths:
            with open(os.path.join(self.tmp_src, rel), "rb") as a, \
                    open(os.path.join(self.tmp_dst, rel), "rb") as b:
                self.assertEqual(a.read(), b.read(), rel)

    def test_schema_migrations_stays_the_targets_own_record(self):
        head = self.db.query("SELECT max(filename) AS m FROM schema_migrations", one=True)["m"]
        self.assertEqual(head, self.manifest["app"]["migration_head"])

    def test_sequences_are_advanced_past_the_restored_ids(self):
        """Without setval the next insert collides with a restored id."""
        for table in self.tables.TABLE_ORDER:
            if not self.tables.has_sequence(table):
                continue
            row = self.db.query(
                f"SELECT nextval(pg_get_serial_sequence(%s, 'id')) AS n, "
                f"(SELECT COALESCE(MAX(id), 0) FROM {table}) AS m", (table,), one=True)
            self.assertGreater(row["n"], row["m"], f"{table} sequence was not advanced")

    def test_every_pre_restore_cookie_is_invalidated(self):
        """SECRET_KEY is unchanged, so a session carrying user_id 1 would otherwise bind to
        whoever is id 1 in the restored data."""
        epoch = self.db.get_setting("session_epoch")
        self.assertTrue(epoch)
        self.assertNotEqual(epoch, self.src_epoch)

    def test_triggers_are_enabled_again_after_a_restore(self):
        """The restore disables transactions_transfer_kind and relies on the deferred split
        trigger; both have to be live afterwards or the app silently loses its guarantees."""
        import psycopg2

        self.assertEqual(
            self.db.query("SELECT tgenabled FROM pg_trigger WHERE tgname = 'transactions_transfer_kind'",
                          one=True)["tgenabled"], "O")
        with self.assertRaises(psycopg2.errors.RaiseException):
            with self.db.transaction():
                self.db.execute("INSERT INTO transaction_splits (transaction_id, category_id, amount) "
                                "VALUES (%s, NULL, 1.00)", (self.t_split,), commit=False)
        self.db.get_db().rollback()

    def test_zz_restore_is_idempotent(self):
        """Named to sort last: it re-runs the restore, so it must not precede the others."""
        from backup import restore

        restore.restore_archive(self.archive_path)
        again = self._dump_all_for(self.db)
        for table in self.tables.TABLE_ORDER:
            self.assertEqual(again[table], self.before[table],
                             f"{table} differs after a second restore")

    def test_table_order_covers_every_live_table(self):
        """The assertion that makes adding a table without updating the backup fail loudly."""
        from backup import tables

        live = {r["tablename"] for r in self.db.query(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")}
        self.assertEqual(set(tables.TABLE_ORDER) | tables.EXCLUDED, live)

    def test_every_migration_has_a_fixup_decision(self):
        from backup import fixups

        migrations = {f for f in os.listdir(os.path.join(os.path.dirname(__file__), "..", "migrations"))
                      if f.endswith(".sql")}
        undecided = migrations - set(fixups.FIXUPS) - fixups.NO_FIXUP_NEEDED
        self.assertEqual(undecided, set(),
                         "a new migration must be classified in FIXUPS or NO_FIXUP_NEEDED")

    # ---------- helpers ----------

    def _files_index(self):
        from backup import archive
        with archive.ArchiveReader(self.archive_path) as arc:
            return arc.files_index()

    @classmethod
    def _dump_all_for(cls, db):
        """Every table as a list of dicts. psycopg2 returns Decimal/date/list/dict natively, so
        this compares exact numerics and array/JSON structure, not their string forms."""
        from backup import tables
        out = {}
        for t in tables.TABLE_ORDER:
            rows = db.query(f"SELECT * FROM {t} ORDER BY {tables.order_by(t)}") or []
            if t == "settings":
                # The restore deliberately rotates this one key, so it is the single row that
                # MUST differ; test_every_pre_restore_cookie_is_invalidated asserts that it did.
                rows = [r for r in rows if r["key"] != "session_epoch"]
            out[t] = rows
        return out


if __name__ == "__main__":
    unittest.main()
