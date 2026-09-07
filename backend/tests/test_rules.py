import unittest
from decimal import Decimal
from unittest import mock

import rules


def txn(**kw):
    base = {"description_clean": "STARBUCKS COFFEE", "description_raw": "SQ *STARBUCKS COFFEE #123",
            "merchant_key": "STARBUCKS COFFEE", "amount": Decimal("-6.45"), "account_id": 1}
    base.update(kw)
    return base


class ValidateTest(unittest.TestCase):
    def test_minimal_contains_rule(self):
        clean = rules.validate({"pattern": "starbucks", "category_id": "7"})
        self.assertEqual(clean["match_type"], "contains")
        self.assertEqual(clean["match_field"], "description_clean")
        self.assertEqual(clean["category_id"], 7)
        self.assertEqual(clean["name"], "contains “starbucks”")
        self.assertTrue(clean["is_active"])

    def test_rejects_missing_pattern(self):
        with self.assertRaises(ValueError):
            rules.validate({"pattern": "  ", "category_id": 1})

    def test_rejects_missing_target(self):
        with self.assertRaises(ValueError):
            rules.validate({"pattern": "x"})
        clean = rules.validate({"pattern": "x", "set_transfer": True})
        self.assertIsNone(clean["category_id"])

    def test_preview_may_skip_target(self):
        self.assertEqual(rules.validate({"pattern": "x"}, require_target=False)["pattern"], "x")

    def test_rejects_bad_regex_and_bad_type(self):
        with self.assertRaises(ValueError):
            rules.validate({"pattern": "(", "match_type": "regex", "category_id": 1})
        with self.assertRaises(ValueError):
            rules.validate({"pattern": "x", "match_type": "fuzzy", "category_id": 1})

    def test_amount_range(self):
        clean = rules.validate({"pattern": "x", "category_id": 1, "amount_min": "-100", "amount_max": "-5.5"})
        self.assertEqual((clean["amount_min"], clean["amount_max"]), (Decimal("-100.00"), Decimal("-5.50")))
        with self.assertRaises(ValueError):
            rules.validate({"pattern": "x", "category_id": 1, "amount_min": "10", "amount_max": "1"})
        with self.assertRaises(ValueError):
            rules.validate({"pattern": "x", "category_id": 1, "amount_min": "abc"})


class MatchesTest(unittest.TestCase):
    def rule(self, **kw):
        base = {"id": 1, "match_type": "contains", "match_field": "description_clean", "pattern": "starbucks",
                "case_sensitive": False, "category_id": 5}
        base.update(kw)
        return rules.compile_rule(base)

    def test_contains_case_insensitive(self):
        self.assertTrue(rules.matches(self.rule(), txn()))
        self.assertFalse(rules.matches(self.rule(case_sensitive=True), txn()))
        self.assertTrue(rules.matches(self.rule(case_sensitive=True, pattern="STARBUCKS"), txn()))

    def test_starts_with_equals_regex(self):
        self.assertTrue(rules.matches(self.rule(match_type="starts_with", pattern="star"), txn()))
        self.assertFalse(rules.matches(self.rule(match_type="starts_with", pattern="coffee"), txn()))
        self.assertTrue(rules.matches(self.rule(match_type="equals", pattern="starbucks coffee"), txn()))
        self.assertFalse(rules.matches(self.rule(match_type="equals", pattern="starbucks"), txn()))
        self.assertTrue(rules.matches(self.rule(match_type="regex", pattern=r"star\w+ coffee$"), txn()))
        self.assertFalse(rules.matches(self.rule(match_type="regex", pattern=r"^coffee"), txn()))

    def test_match_field(self):
        self.assertTrue(rules.matches(self.rule(match_field="description_raw", pattern="sq *"), txn()))
        self.assertFalse(rules.matches(self.rule(match_field="merchant_key", pattern="sq *"), txn()))

    def test_amount_range_inclusive_on_signed_amount(self):
        self.assertTrue(rules.matches(self.rule(amount_min=Decimal("-6.45"), amount_max=Decimal("-6.45")), txn()))
        self.assertFalse(rules.matches(self.rule(amount_min=Decimal("-6.00")), txn()))
        self.assertFalse(rules.matches(self.rule(amount_max=Decimal("-7.00")), txn()))
        self.assertFalse(rules.matches(self.rule(amount_min=Decimal("-10")), txn(amount=None)))

    def test_account_filter_and_inactive(self):
        self.assertTrue(rules.matches(self.rule(account_id=1), txn()))
        self.assertFalse(rules.matches(self.rule(account_id=2), txn()))
        self.assertFalse(rules.matches(self.rule(is_active=False), txn()))

    def test_first_match_respects_order(self):
        r1 = self.rule(id=1, pattern="coffee", category_id=9)
        r2 = self.rule(id=2, pattern="starbucks", category_id=5)
        self.assertEqual(rules.first_match([r1, r2], txn())["id"], 1)
        self.assertEqual(rules.first_match([r2, r1], txn())["id"], 2)
        self.assertIsNone(rules.first_match([self.rule(pattern="tim hortons")], txn()))

    def test_count_and_sample(self):
        txns = [txn(description_clean=f"STARBUCKS {i}") for i in range(25)] + [txn(description_clean="TIMS")]
        count, sample = rules.count_and_sample({"match_type": "contains", "pattern": "starbucks"}, txns, sample_n=20)
        self.assertEqual((count, len(sample)), (25, 20))


if __name__ == "__main__":
    unittest.main()


class PatternSafetyTest(unittest.TestCase):
    def test_pattern_length_is_capped(self):
        rules.validate({"pattern": "x" * rules.MAX_PATTERN_LEN, "category_id": 1})
        with self.assertRaisesRegex(ValueError, "at most"):
            rules.validate({"pattern": "x" * (rules.MAX_PATTERN_LEN + 1), "category_id": 1})

    def test_slow_regex_is_rejected_at_validation(self):
        with mock.patch.object(rules, "is_fast_pattern", return_value=False):
            with self.assertRaisesRegex(ValueError, "too slow"):
                rules.validate({"pattern": "(a+)+$", "match_type": "regex", "category_id": 1})

    def test_known_catastrophic_patterns_finish_within_the_probe_budget(self):
        for pattern in ("(a+)+$", r"(\w+\s?)*$", r"^(\d+)*x", "(.*a){12}"):
            clean = rules.validate({"pattern": pattern, "match_type": "regex", "category_id": 1})
            self.assertEqual(clean["pattern"], pattern)

    def test_timed_out_rule_is_flagged_and_stops_matching(self):
        class Slow:
            calls = 0

            def search(self, text, timeout=None):
                Slow.calls += 1
                raise TimeoutError

        rule = rules.compile_rule({"id": 9, "match_type": "regex", "pattern": "x"})
        rule["_regex"] = Slow()
        self.assertEqual([rules.matches(rule, txn()), rules.matches(rule, txn())], [False, False])
        self.assertEqual((Slow.calls, rules.timed_out_ids([rule])), (1, [9]))
        self.assertEqual(rules.timed_out_ids([rules.compile_rule({"id": 1, "pattern": "ok"})]), [])
