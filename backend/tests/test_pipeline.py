import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

import dedupe  # noqa: E402
from importer import pipeline  # noqa: E402
from importer.models import Mapping, ParsedRow, ParseResult  # noqa: E402

BOUND = lambda: mock.patch.object(pipeline, "db", FAKE)  # noqa: E731

NAME = "EUGENE BRAVERMAN"


def row(i, desc, amount, day=1):
    return ParsedRow(row_index=i, txn_date=date(2026, 5, day), posted_date=None, description=desc,
                     amount=Decimal(amount) if amount is not None else None, balance=None, raw={"cells": [desc]})


def positive_rows(n=8):
    return [row(i, f"Shop {i} Eugene Braverman SHOP{i}", "10.00") for i in range(n)]


class PhrasesForTest(unittest.TestCase):
    def test_saved_phrases_win_and_junk_is_dropped(self):
        st = {"stats": {"stripped_phrases": [NAME, 3, None]}}
        self.assertEqual(pipeline._phrases_for(st, ["anything"]), [NAME])

    def test_recomputed_from_descriptions_when_absent(self):
        docs = [f"Shop {i} Eugene Braverman RAW{i}" for i in range(16)]
        self.assertEqual(pipeline._phrases_for({"stats": {}}, docs), [NAME])
        self.assertEqual(pipeline._phrases_for(None, docs), [NAME])
        self.assertEqual(pipeline._phrases_for({"stats": {}}, docs[:3]), [])


class BuildStagedTest(unittest.TestCase):
    def test_fingerprints_occurrences_and_phrase_stripping(self):
        rows = [row(0, "Geico Eugene Braverman GEICO AUTO", "-52.10"),
                row(1, "Geico Eugene Braverman GEICO AUTO", "-52.10"),
                row(2, "Broken line", None)]
        staged = pipeline._build_staged(rows, 5, [NAME])
        geico = staged[0]["merchant"]
        self.assertEqual((geico.key, geico.name), ("GEICO AUTO", "Geico Auto"))
        expected_fp = dedupe.fingerprint(5, date(2026, 5, 1), Decimal("-52.10"), geico.clean)
        self.assertEqual([(s["fingerprint"], s["occurrence"]) for s in staged],
                         [(expected_fp, 1), (expected_fp, 2), (None, 1)])

    def test_phrases_are_detected_when_not_supplied(self):
        staged = pipeline._build_staged(positive_rows(16), 5)
        self.assertTrue(all("Braverman" not in s["merchant"].name for s in staged))


class PreviewStatsTest(unittest.TestCase):
    def test_counts(self):
        staged = pipeline._build_staged([row(0, "A", "1"), row(1, "A", "1"), row(2, "B", "2"), row(3, "C", None)], 1)
        existing = {(staged[2]["fingerprint"], 1): 99}
        self.assertEqual(pipeline._preview_stats(staged, existing), {
            "rows_total": 4, "rows_valid": 3, "rows_invalid": 1, "dupes_existing": 1, "dupes_in_file": 1,
            "predicted": {"rule": 0, "merchant": 0, "builtin": 0, "none": 3},
        })


class AutoFlipTest(unittest.TestCase):
    def _result(self, profile=None, confidence=0.0, flip=False):
        return ParseResult(rows=positive_rows(), profile=profile, profile_confidence=confidence,
                           mapping=Mapping(flip_sign=flip), warnings=["Most amounts are positive; flip in the preview."])

    def _run(self, result, account_type="credit_card", account_id=3):
        router = _stubs.Router([("SELECT account_type FROM accounts", {"account_type": account_type})])
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=router):
            pipeline._auto_flip({"account_id": account_id}, result)
        return result

    def test_generic_card_export_with_positive_charges_is_flipped(self):
        r = self._run(self._result())
        self.assertTrue(all(x.amount == Decimal("-10.00") for x in r.rows))
        self.assertTrue(r.mapping.flip_sign)
        self.assertEqual(r.warnings, [pipeline.AUTO_FLIP_WARNING])

    def test_recognised_profile_keeps_its_sign_convention(self):
        r = self._run(self._result(profile="chase", confidence=0.85))
        self.assertTrue(all(x.amount == Decimal("10.00") for x in r.rows))
        self.assertFalse(r.mapping.flip_sign)

    def test_explicit_flip_account_missing_or_checking_do_nothing(self):
        for result, kwargs in ((self._result(flip=True), {}),
                               (self._result(), {"account_id": None}),
                               (self._result(), {"account_type": "checking"})):
            r = self._run(result, **kwargs)
            self.assertTrue(all(x.amount == Decimal("10.00") for x in r.rows))
            self.assertNotIn(pipeline.AUTO_FLIP_WARNING, r.warnings)

    def test_should_auto_flip_mirrors_the_decision_for_previewed_rows(self):
        amounts = [Decimal("10")] * 8
        router = _stubs.Router([("SELECT account_type FROM accounts", {"account_type": "credit_card"})])
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=router):
            self.assertTrue(pipeline._should_auto_flip({"mapping": {}, "bank_profile": None}, 3, amounts))
            self.assertFalse(pipeline._should_auto_flip({"mapping": {"flip_sign": True}}, 3, amounts))
            self.assertFalse(pipeline._should_auto_flip({"mapping": {}, "bank_profile": "amex", "profile_confidence": 0.9}, 3, amounts))
            self.assertFalse(pipeline._should_auto_flip({"mapping": {}}, None, amounts))


class RecoverInterruptedTest(unittest.TestCase):
    def test_resets_parsing_and_committing_rows(self):
        router = _stubs.Router(default=2)
        with BOUND(), mock.patch.object(FAKE, "execute", side_effect=router):
            pipeline.recover_interrupted()
        self.assertEqual(len(router.calls), 2)
        parsing, committing = router.calls[0][0], router.calls[1][0]
        self.assertIn("SET status = 'error'", parsing)
        self.assertIn("WHERE status = 'parsing'", parsing)
        self.assertIn("SET status = 'previewed'", committing)
        self.assertIn("WHERE status = 'committing'", committing)


class CommitStatementTest(unittest.TestCase):
    def test_refuses_statement_that_is_not_previewed(self):
        router = _stubs.Router([
            ("SELECT * FROM statements WHERE id", {"id": 7, "user_id": 1, "status": "committed"}),
            ("SELECT * FROM accounts WHERE id", {"id": 3, "user_id": 1, "currency": "USD"}),
        ])
        lock = _stubs.Router(default=0)
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=router), mock.patch.object(FAKE, "execute", side_effect=lock):
            with self.assertRaises(pipeline.ImportError_) as ctx:
                pipeline.commit_statement(7, 3)
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("committed", str(ctx.exception))
        sql, params = lock.calls[0]
        self.assertIn("status = 'committing'", sql)
        self.assertIn("status = 'previewed'", sql)
        self.assertEqual(params, (3, 7))

    def test_requires_an_account_the_user_owns(self):
        router = _stubs.Router([("SELECT * FROM statements WHERE id", {"id": 7, "user_id": 1, "status": "previewed"})])
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=router):
            with self.assertRaises(pipeline.ImportError_) as ctx:
                pipeline.commit_statement(7, 99)
        self.assertEqual(ctx.exception.status, 400)

    def test_unknown_statement(self):
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=_stubs.Router()):
            with self.assertRaises(pipeline.ImportError_) as ctx:
                pipeline.commit_statement(404, 3)
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()


class RowShimTest(unittest.TestCase):
    def test_preview_stats_counts_twins_on_staged_import_rows(self):
        base = {"txn_date": date(2026, 5, 1), "description": "GUSTO PAYROLL", "amount": Decimal("100"), "is_valid": True}
        ledger = pipeline._RowShim({**base, "id": 1, "raw": {"balance": "1.00"}})
        twin = pipeline._RowShim({**base, "id": 2, "raw": {"twin_of": 0}})
        no_raw = pipeline._RowShim({**base, "id": 3})
        staged = [{"id": r.description, "fingerprint": f"fp{i}", "occurrence": 1, "row": r, "category_id": None}
                  for i, r in enumerate((ledger, twin, no_raw))]
        self.assertEqual(pipeline._preview_stats(staged, {}), {
            "rows_total": 3, "rows_valid": 3, "rows_invalid": 0, "dupes_existing": 0, "dupes_in_file": 1,
            "predicted": {"rule": 0, "merchant": 0, "builtin": 0, "none": 3},
        })


class ParseFailureMessageTest(unittest.TestCase):
    def _run(self, exc):
        FAKE.calls.clear()
        with mock.patch.object(pipeline, "_load", return_value={"id": 5}), \
                mock.patch.object(pipeline, "_parse_file", side_effect=exc), self.assertLogs(pipeline.log):
            pipeline.parse_statement(5)
        errors = [p for kind, sql, p in FAKE.calls if kind == "execute" and "status = 'error'" in sql]
        return errors[0][0]

    def test_internal_exceptions_become_a_plain_language_message(self):
        self.assertEqual(self._run(KeyError("Amount")), pipeline.UNREADABLE_MESSAGE)

    def test_import_errors_keep_their_own_message(self):
        self.assertEqual(self._run(pipeline.ImportError_("The workbook has no data rows")), "The workbook has no data rows")
