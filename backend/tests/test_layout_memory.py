import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

import layout_memory  # noqa: E402

MAPPING = {"date": 0, "description": [1], "amount": 2, "sign": "as_is", "flip_sign": True}
HEADER = ["Date", "Payee", "Amount"]
PROVISIONAL_DELETE = "DELETE FROM import_layouts WHERE user_id = %s AND layout_key = %s AND account_id IS NULL"


class LearnTest(unittest.TestCase):
    def _learn(self, *args, **kwargs):
        router = _stubs.Router(default=1)
        with mock.patch.object(FAKE, "execute", side_effect=router):
            layout_memory.learn(*args, **kwargs)
        return router.calls

    def test_upserts_per_user_layout_and_account_and_counts_the_use(self):
        calls = self._learn(1, "abc", MAPPING, header=HEADER, bank_profile=None, account_id=5, filename="export.csv")
        sql, params = calls[0]
        self.assertIn("INSERT INTO import_layouts", sql)
        self.assertIn("ON CONFLICT (user_id, layout_key, account_id) DO UPDATE", sql)
        self.assertIn("times_used = import_layouts.times_used + EXCLUDED.times_used", sql)
        self.assertEqual(params, (1, "abc", json.dumps(HEADER), json.dumps(MAPPING), None, 5, "export.csv", 1))

    def test_a_mapping_applied_but_not_imported_yet_is_saved_without_counting_a_use(self):
        calls = self._learn(1, "abc", MAPPING, account_id=5, used=False)
        self.assertEqual(calls[0][1], (1, "abc", None, json.dumps(MAPPING), None, 5, None, 0))

    def test_saving_for_an_account_replaces_the_provisional_accountless_row(self):
        calls = self._learn(1, "abc", MAPPING, account_id=5)
        self.assertEqual(calls[1], (PROVISIONAL_DELETE, (1, "abc")))

    def test_saving_without_an_account_keeps_other_rows(self):
        self.assertEqual(len(self._learn(1, "abc", MAPPING, account_id=None)), 1)

    def test_backfill_never_overwrites_or_deletes_what_is_already_remembered(self):
        calls = self._learn(1, "abc", MAPPING, account_id=5, overwrite=False)
        self.assertEqual(len(calls), 1)
        self.assertIn("ON CONFLICT (user_id, layout_key, account_id) DO NOTHING", calls[0][0])

    def test_nothing_is_written_without_a_key_or_mapping(self):
        self.assertEqual(self._learn(1, None, MAPPING) + self._learn(1, "abc", {}), [])


class ForgetTest(unittest.TestCase):
    def test_delete_is_scoped_to_the_user(self):
        router = _stubs.Router(default=0)
        with mock.patch.object(FAKE, "execute", side_effect=router):
            self.assertFalse(layout_memory.forget(2, 9))
        sql, params = router.calls[0]
        self.assertEqual((sql, params), ("DELETE FROM import_layouts WHERE user_id = %s AND id = %s", (2, 9)))


class LookupTest(unittest.TestCase):
    def test_prefers_the_accounts_own_mapping_then_the_most_recent_one(self):
        row = {"id": 3, "mapping": MAPPING, "bank_profile": None, "account_id": 5}
        router = _stubs.Router([("FROM import_layouts WHERE user_id = %s AND layout_key = %s", row)])
        with mock.patch.object(FAKE, "query", side_effect=router):
            self.assertEqual(layout_memory.lookup(1, "abc", 5), row)
        sql, params = router.calls[0]
        self.assertIn("ORDER BY (account_id IS NOT DISTINCT FROM %s) DESC, last_used_at DESC, id DESC LIMIT 1", sql)
        self.assertEqual(params, (1, "abc", 5))

    def test_account_is_optional(self):
        router = _stubs.Router()
        with mock.patch.object(FAKE, "query", side_effect=router):
            self.assertIsNone(layout_memory.lookup(1, "abc"))
        self.assertEqual(router.calls[0][1], (1, "abc", None))


if __name__ == "__main__":
    unittest.main()
