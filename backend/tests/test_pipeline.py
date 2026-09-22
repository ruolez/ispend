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


class LayoutMemoryParseTest(unittest.TestCase):
    CSV = ("Post Date,Transaction Date,Description,Amount\n"
           "08/02/2024,08/01/2024,COFFEE,-4.50\n08/03/2024,08/03/2024,RENT,-1200.00\n")
    SAVED = {"has_header": True, "skip_rows": 0, "date": 1, "posted_date": 0, "description": [2], "amount": 3,
             "sign": "as_is", "flip_sign": True}
    HIT = {"id": 3, "mapping": SAVED, "bank_profile": None, "account_id": 5, "times_used": 2,
           "last_used_at": "2026-09-01", "sample_filename": "july.csv"}

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "u1.csv"), "w") as f:
            f.write(self.CSV)
        self.st = {"id": 7, "user_id": 1, "stored_path": "u1.csv", "file_kind": "csv", "account_id": None}

    def _parse(self, routes, mapping=None):
        router = _stubs.Router(routes)
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=router), \
                mock.patch.object(pipeline.config, "STATEMENTS_DIR", self.dir):
            result, _ = pipeline._parse_file(self.st, mapping, None)
        return result, router

    def test_saved_mapping_is_applied_and_reported(self):
        result, router = self._parse([("FROM import_layouts", self.HIT)])
        self.assertEqual((result.mapping_source, result.mapping.flip_sign, result.layout),
                         ("memory", True, {"id": 3, "account_id": 5, "times_used": 2, "last_used_at": "2026-09-01",
                                           "sample_filename": "july.csv"}))
        self.assertEqual([r.amount for r in result.rows], [Decimal("4.50"), Decimal("1200.00")])
        self.assertFalse(any(w.startswith("Bank not recognised") for w in result.warnings))
        self.assertEqual(router.calls[0][1], (1, result.layout_key, None))

    def test_lookup_asks_for_the_statements_account(self):
        self.st["account_id"] = 5
        _, router = self._parse([("FROM import_layouts", self.HIT)])
        self.assertEqual(router.calls[0][1][2], 5)

    def test_without_a_saved_mapping_detection_runs_as_before(self):
        result, _ = self._parse([])
        self.assertEqual((result.mapping_source, result.layout, result.mapping.flip_sign), ("auto", None, False))
        self.assertEqual([r.amount for r in result.rows], [Decimal("-4.50"), Decimal("-1200.00")])
        self.assertTrue(any(w.startswith("Bank not recognised") for w in result.warnings))
        self.assertTrue(result.layout_key)

    def test_explicit_mapping_is_the_users_and_skips_the_lookup(self):
        result, router = self._parse([], mapping=self.SAVED)
        self.assertEqual((result.mapping_source, router.sql("import_layouts")), ("user", []))

    def _parse_statement_with(self, source):
        result = ParseResult(rows=positive_rows(), mapping=Mapping(), mapping_source=source)
        flip = mock.Mock()
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=_stubs.Router(default={"id": 7, "user_id": 1})), \
                mock.patch.object(FAKE, "execute", side_effect=_stubs.Router(default=1)), \
                mock.patch.object(pipeline, "_parse_file", return_value=(result, None)), \
                mock.patch.object(pipeline, "_auto_flip", flip), mock.patch.object(pipeline, "_stage"):
            pipeline.parse_statement(7)
        return flip.call_count

    def test_auto_flip_is_skipped_for_a_remembered_mapping(self):
        self.assertEqual((self._parse_statement_with("memory"), self._parse_statement_with("auto")), (0, 1))

    def test_should_auto_flip_respects_a_remembered_mapping(self):
        router = _stubs.Router([("SELECT account_type FROM accounts", {"account_type": "credit_card"})])
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=router):
            self.assertFalse(pipeline._should_auto_flip({"mapping": {}, "bank_profile": None,
                                                         "stats": {"mapping_source": "memory"}}, 3, [Decimal("10")] * 8))


class RememberLayoutTest(unittest.TestCase):
    ST = {"id": 7, "user_id": 1, "file_kind": "csv", "original_filename": "x.csv", "mapping": {"date": 0, "amount": 1},
          "bank_profile": None}
    CONFIDENT, SHAKY = 0.9, 0.35

    def _run(self, st, stats, **kwargs):
        router = _stubs.Router(default=1)
        with BOUND(), mock.patch.object(FAKE, "execute", side_effect=router):
            saved = pipeline._remember_layout(st, stats, ["Date", "Amount"], 5, **kwargs)
        return saved, router.sql("INSERT INTO import_layouts")

    def test_unrecognised_bank_is_saved_with_header_account_and_filename(self):
        saved, calls = self._run(self.ST, {"layout_key": "k", "mapping_source": "auto"})
        self.assertTrue(saved)
        self.assertEqual([p for _, p in calls], [(1, "k", '["Date", "Amount"]', '{"date": 0, "amount": 1}', None, 5, "x.csv", 1)])

    def test_confidently_recognised_bank_is_saved_only_when_the_user_changed_something(self):
        chase = {**self.ST, "bank_profile": "chase", "profile_confidence": self.CONFIDENT}
        self.assertEqual([self._run(chase, {"layout_key": "k", "mapping_source": source})[0]
                          for source in ("auto", "user", "memory")], [False, True, True])

    def test_shaky_bank_match_is_saved_even_when_accepted_unedited(self):
        shaky = {**self.ST, "bank_profile": "wells_fargo", "profile_confidence": self.SHAKY}
        self.assertTrue(self._run(shaky, {"layout_key": "k", "mapping_source": "auto"})[0])

    def test_pdfs_and_statements_without_a_key_are_never_saved(self):
        self.assertEqual(self._run({**self.ST, "file_kind": "pdf"}, {"layout_key": "k", "mapping_source": "user"}), (False, []))
        self.assertEqual(self._run(self.ST, {"mapping_source": "user"}), (False, []))

    def test_use_count_and_overwrite_are_passed_through(self):
        _, calls = self._run(self.ST, {"layout_key": "k", "mapping_source": "user"}, used=False, overwrite=False)
        self.assertEqual(calls[0][1][-1], 0)
        self.assertIn("DO NOTHING", calls[0][0])


class LearnOnMappingTest(unittest.TestCase):
    """A mapping the user applies in the preview is remembered even if the statement is never imported."""
    USER_MAPPING = {"date": 0, "amount": 1}
    HEADER = ["Date", "Amount"]

    def _remembered(self, mapping, rows_valid):
        staged = {"id": 7, "user_id": 1, "account_id": 5, "file_kind": "csv", "mapping": self.USER_MAPPING,
                  "stats": {"layout_key": "k", "rows_valid": rows_valid, "header": self.HEADER}}
        result = ParseResult(rows=positive_rows(), mapping=Mapping(), mapping_source="user" if mapping else "auto")
        remember = mock.Mock(return_value=True)
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=_stubs.Router(default=staged)), \
                mock.patch.object(FAKE, "execute", side_effect=_stubs.Router(default=1)), \
                mock.patch.object(pipeline, "_parse_file", return_value=(result, None)), \
                mock.patch.object(pipeline, "_auto_flip"), mock.patch.object(pipeline, "_stage"), \
                mock.patch.object(pipeline, "_remember_layout", remember):
            pipeline.parse_statement(7, mapping=mapping)
        return remember.call_args_list

    def test_user_mapping_with_valid_rows_is_remembered_for_the_account_without_counting_a_use(self):
        staged_stats = {"layout_key": "k", "rows_valid": 3, "header": self.HEADER}
        self.assertEqual(self._remembered(self.USER_MAPPING, 3),
                         [mock.call(mock.ANY, staged_stats, self.HEADER, 5, used=False)])

    def test_mapping_that_parses_nothing_is_not_remembered(self):
        self.assertEqual(self._remembered(self.USER_MAPPING, 0), [])

    def test_automatic_parse_is_not_remembered_before_import(self):
        self.assertEqual(self._remembered(None, 3), [])


class AccountSwitchTest(unittest.TestCase):
    """The account is often chosen after parsing: its own saved mapping then replaces the borrowed one."""
    APPLIED = Mapping(date=0, amount=1, description=[2]).to_dict()
    OWN = {**APPLIED, "flip_sign": True}

    def _switch(self, source, hit):
        st = {"id": 7, "user_id": 1, "status": "previewed", "account_id": None, "mapping": self.APPLIED,
              "stats": {"layout_key": "k", "mapping_source": source}}
        router = _stubs.Router([("FROM import_layouts", hit), ("SELECT * FROM statements", st)])
        parse = mock.Mock()
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=router), \
                mock.patch.object(FAKE, "execute", side_effect=_stubs.Router(default=1)), \
                mock.patch.object(pipeline, "parse_statement", parse):
            pipeline.recompute_dupes(7, 9)
        return parse.call_args_list

    def test_accounts_own_differing_mapping_triggers_a_reparse(self):
        self.assertEqual(self._switch("memory", {"id": 4, "account_id": 9, "mapping": self.OWN}), [mock.call(7)])

    def test_same_mapping_or_another_accounts_mapping_keeps_the_preview(self):
        self.assertEqual(self._switch("memory", {"id": 4, "account_id": 9, "mapping": self.APPLIED}), [])
        self.assertEqual(self._switch("memory", {"id": 4, "account_id": 3, "mapping": self.OWN}), [])

    def test_a_mapping_the_user_edited_is_never_replaced(self):
        self.assertEqual(self._switch("user", {"id": 4, "account_id": 9, "mapping": self.OWN}), [])


class BackfillLayoutsTest(unittest.TestCase):
    CSV = "Date,Payee,Amount\n2026-05-01,COFFEE,-4.50\n"
    MAPPING = {"date": 0, "description": [1], "amount": 2}

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "a.csv"), "w") as f:
            f.write(self.CSV)
        FAKE.settings.pop("layout_backfill_done", None)

    def _st(self, sid, account_id, **over):
        return {"id": sid, "user_id": 1, "account_id": account_id, "file_kind": "csv", "stored_path": "a.csv",
                "original_filename": f"s{sid}.csv", "mapping": {**self.MAPPING, "flip_sign": sid == 30},
                "bank_profile": None, "profile_confidence": 0, "stats": {}, **over}

    def _backfill(self, statements):
        inserts = _stubs.Router(default=1)
        with BOUND(), mock.patch.object(FAKE, "query", side_effect=_stubs.Router([("FROM statements", statements)])), \
                mock.patch.object(FAKE, "execute", side_effect=inserts), \
                mock.patch.object(pipeline.config, "STATEMENTS_DIR", self.dir):
            pipeline.backfill_layouts()
        return inserts.sql("INSERT INTO import_layouts")

    def test_newest_statement_per_account_wins_and_nothing_is_overwritten(self):
        from importer import layout
        key, _ = layout.fingerprint([["Date", "Payee", "Amount"], ["2026-05-01", "COFFEE", "-4.50"]])
        calls = self._backfill([self._st(30, 5), self._st(20, 5), self._st(10, 6)])  # newest first, as queried
        self.assertTrue(all("DO NOTHING" in sql for sql, _ in calls))
        self.assertEqual([(p[1], p[2], p[3], p[5], p[6]) for _, p in calls], [
            (key, '["Date", "Payee", "Amount"]', '{"date": 0, "description": [1], "amount": 2, "flip_sign": false}', 6, "s10.csv"),
            (key, '["Date", "Payee", "Amount"]', '{"date": 0, "description": [1], "amount": 2, "flip_sign": true}', 5, "s30.csv"),
        ])

    def test_confident_bank_matches_and_unreadable_files_are_skipped(self):
        chase = self._st(11, 5, bank_profile="chase", profile_confidence=0.9)
        gone = self._st(12, 5, stored_path="missing.csv")
        self.assertEqual(self._backfill([chase, gone]), [])

    def test_a_missing_file_still_counts_when_the_statement_kept_its_layout_key(self):
        kept = self._st(13, 5, stored_path="missing.csv", stats={"layout_key": "kept", "mapping_source": "user"})
        self.assertEqual([p[1] for _, p in self._backfill([kept])], ["kept"])

    def test_runs_once(self):
        self.assertEqual(len(self._backfill([self._st(10, 6)])), 1)
        self.assertEqual(self._backfill([self._st(10, 6)]), [])
