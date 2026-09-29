import os
import random
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

_stubs.install()

import support  # noqa: E402

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
BODY = "The import page spins forever after I pick a PDF."


class RefTest(unittest.TestCase):
    def test_ref_round_trips_for_any_id(self):
        rng = random.Random(7)  # noqa: S311 - reproducible test inputs
        for report_id in [1, 2, 999, 1000] + [rng.randint(1, 10 ** 7) for _ in range(200)]:
            self.assertEqual(support.parse_ref(support.ref(report_id)), report_id)

    def test_parse_ref_accepts_the_ways_people_type_it(self):
        self.assertEqual([support.parse_ref(t) for t in ("R-1042", "r1042", " #R-1042 ", "1042")], [42, 42, 42, 42])

    def test_parse_ref_rejects_what_is_not_a_reference(self):
        self.assertEqual([support.parse_ref(t) for t in ("", "R-", "abc", "R-999", "R-12x", None)],
                         [None, None, None, None, None, None])


class ValidateReportTest(unittest.TestCase):
    def test_a_complete_report_is_cleaned(self):
        value, problem = support.validate_report(
            {"kind": "bug", "subject": "  Import   stuck ", "body": f"  {BODY}\r\n\r\n\r\n\r\nThanks  ", "impact": "blocking"})
        self.assertEqual((value, problem), ({"kind": "bug", "subject": "Import stuck",
                                             "body": f"{BODY}\n\nThanks", "impact": "blocking"}, None))

    def test_the_subject_defaults_to_the_first_line(self):
        value, _ = support.validate_report({"kind": "question", "body": "How do I split a transaction?\nMore detail here."})
        self.assertEqual(value["subject"], "How do I split a transaction?")

    def test_a_long_first_line_is_cut_at_a_word(self):
        words = " ".join(["statement"] * 30)
        value, _ = support.validate_report({"kind": "bug", "body": words})
        self.assertEqual(value["subject"], ("statement " * 8).strip() + "…")
        self.assertLessEqual(len(value["subject"]), support.SUBJECT_AUTO_MAX + 1)

    def test_problems_are_reported_in_plain_words(self):
        cases = [
            ({"kind": "rant", "body": BODY}, "Choose what kind of report this is"),
            ({"kind": "bug", "body": "  "}, "Tell us what happened"),
            ({"kind": "bug", "body": "broken"}, f"Add a little more detail (at least {support.BODY_MIN} characters)"),
            ({"kind": "bug", "body": "x" * (support.BODY_MAX + 1)}, f"Keep it under {support.BODY_MAX} characters"),
            ({"kind": "bug", "body": BODY, "subject": "s" * 141}, "Keep the subject under 140 characters"),
            ({"kind": "bug", "body": BODY, "impact": "catastrophic"}, "Invalid impact"),
        ]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertEqual(support.validate_report(data), (None, message))

    def test_control_characters_are_dropped_but_tabs_and_newlines_stay(self):
        value, _ = support.validate_report({"kind": "bug", "body": f"{BODY}\x00\x07\n\tindented"})
        self.assertEqual(value["body"], f"{BODY}\n\tindented")


class ValidateMessageTest(unittest.TestCase):
    def test_a_reply_needs_words_or_a_screenshot(self):
        self.assertEqual(support.validate_message({"body": "  "}), (None, "Write a message"))
        self.assertEqual(support.validate_message({"body": ""}, has_files=True), ("(screenshot)", None))
        self.assertEqual(support.validate_message({"body": " ok "}), ("ok", None))
        self.assertEqual(support.validate_message({"body": "x" * (support.BODY_MAX + 1)}),
                         (None, f"Keep it under {support.BODY_MAX} characters"))


class RedactTest(unittest.TestCase):
    def test_money_numbers_and_addresses_are_masked(self):
        cases = {
            "Could not save $1,234.56 for jane.doe@example.com": "Could not save [amount] for [email]",
            "Card 4111 1111 1111 1111 declined": "Card [number] declined",
            "Account 000123456789 not found": "Account [number] not found",
            "Amount -45.10 is invalid": "Amount [amount] is invalid",
            "HTTP 500 on row 12": "HTTP 500 on row 12",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(support.redact(raw), expected)

    def test_no_run_of_four_digits_survives(self):
        rng = random.Random(11)  # noqa: S311 - reproducible test inputs
        for _ in range(300):
            text = "".join(rng.choice("0123456789 ,.$-abc@x") for _ in range(rng.randint(0, 60)))
            out = support.redact(text)
            self.assertIsNone(support.re.search(r"\d{4}", out), (text, out))

    def test_redact_is_idempotent(self):
        rng = random.Random(3)  # noqa: S311 - reproducible test inputs
        for _ in range(300):
            text = "".join(rng.choice("0123456789 ,.$-abc@x.") for _ in range(rng.randint(0, 60)))
            once = support.redact(text)
            self.assertEqual(support.redact(once), once)


class TemplatePathTest(unittest.TestCase):
    def test_ids_queries_and_fragments_are_removed(self):
        cases = {
            "/api/transactions/123/splits?x=1": "/api/transactions/:id/splits",
            "/transactions.html?account=5#row-9": "/transactions.html",
            "https://ispend.example/api/statements/77": "/api/statements/:id",
            "/help.html": "/help.html",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(support.template_path(raw), expected)


class CleanContextTest(unittest.TestCase):
    def test_only_allowlisted_fields_survive_and_are_redacted(self):
        raw = {
            "page": "/transactions.html?q=rent", "app_version": "1.0.0", "build": "abc123", "viewport": "390x844@3",
            "theme": "dark", "standalone": True, "online": True, "language": "en-US", "timezone": "America/Chicago",
            "request_id": "a1b2c3d4e5f6",
            "errors": [{"message": "TypeError at $12.50", "source": "/js/pages/help.js:10", "at": "2026-09-29T12:00:00Z",
                        "stack": "should be dropped"}],
            "requests": [{"method": "post", "path": "/api/transactions/9", "status": 500,
                          "request_id": "ffff0000aaaa", "message": "Server error", "body": {"amount": 5}}],
            "cookies": "session=secret", "html": "<div>balance 1,000.00</div>",
        }
        self.assertEqual(support.clean_context(raw), {
            "page": "/transactions.html", "app_version": "1.0.0", "build": "abc123", "viewport": "390x844@3",
            "theme": "dark", "standalone": True, "online": True, "language": "en-US", "timezone": "America/Chicago",
            "request_id": "a1b2c3d4e5f6",
            "errors": [{"message": "TypeError at [amount]", "source": "/js/pages/help.js:10", "at": "2026-09-29T12:00:00Z"}],
            "requests": [{"method": "POST", "path": "/api/transactions/:id", "status": 500,
                          "request_id": "ffff0000aaaa", "message": "Server error"}],
        })

    def test_garbage_is_dropped_field_by_field(self):
        raw = {"theme": "neon", "standalone": "yes", "request_id": "not hex!", "errors": "nope",
               "requests": [{"method": "BREW", "status": "x"}, 5], "viewport": "v" * 100}
        self.assertEqual(support.clean_context(raw), {})

    def test_lists_are_capped_and_the_result_stays_small(self):
        many = [{"message": "e" * 5000, "source": "s" * 5000, "at": "t" * 500}] * 50
        out = support.clean_context({"errors": many, "requests": [{"path": "/" + "p" * 5000, "message": "m" * 5000}] * 50})
        self.assertEqual((len(out["errors"]), len(out["requests"])), (support.CONTEXT_LIST_MAX, support.CONTEXT_LIST_MAX))
        self.assertLess(len(support.json.dumps(out)), 16384)

    def test_non_dict_input_gives_an_empty_context(self):
        self.assertEqual([support.clean_context(v) for v in (None, "x", [1], 5)], [{}, {}, {}, {}])


class NextStatusTest(unittest.TestCase):
    def test_transitions(self):
        recent = NOW - timedelta(days=3)
        cases = [
            (("open", "user", "reply", None), "open"),
            (("in_progress", "user", "reply", None), "in_progress"),
            (("waiting", "user", "reply", None), "open"),
            (("resolved", "user", "reply", recent), "open"),
            (("waiting", "user", "resolve", None), "resolved"),
            (("open", "admin", "reply", None), "waiting"),
            (("resolved", "admin", "reply", recent), "waiting"),
            (("open", "admin", "reply_resolve", None), "resolved"),
            (("in_progress", "admin", "note", None), "in_progress"),
        ]
        for (current, actor, action, resolved_at), expected in cases:
            with self.subTest(current=current, actor=actor, action=action):
                self.assertEqual(support.next_status(current, actor, action, resolved_at, NOW), expected)

    def test_a_report_resolved_long_ago_is_not_reopened_by_the_customer(self):
        old = NOW - timedelta(days=support.REOPEN_DAYS, seconds=1)
        self.assertIsNone(support.next_status("resolved", "user", "reply", old, NOW))
        self.assertEqual(support.next_status("resolved", "user", "reply", NOW - timedelta(days=support.REOPEN_DAYS), NOW), "open")

    def test_unknown_actions_are_rejected(self):
        with self.assertRaises(ValueError):
            support.next_status("open", "user", "note", None, NOW)


if __name__ == "__main__":
    unittest.main()
