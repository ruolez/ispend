import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

_stubs.install()

import entitlement  # noqa: E402

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
TRIAL, GRACE = 14, 7


def ev(**row):
    return entitlement.evaluate(row, now=NOW, enabled=True, trial=TRIAL, grace=GRACE)


class EvaluateTest(unittest.TestCase):
    def test_billing_off_gives_everyone_full_access(self):
        """An existing self-hosted install must upgrade into exactly the behaviour it had."""
        for status in ("canceled", "past_due", "unpaid", None, "wat"):
            with self.subTest(status=status):
                out = entitlement.evaluate({"sub_status": status}, now=NOW, enabled=False)
                self.assertEqual((out["state"], out["can_write"]), (entitlement.ACTIVE, True))

    def test_admin_is_exempt(self):
        out = ev(role="admin", sub_status="canceled", lapsed_at=NOW - 90 * DAY)
        self.assertEqual((out["state"], out["can_write"]), (entitlement.ADMIN_EXEMPT, True))

    def test_a_live_comp_beats_every_other_signal(self):
        out = ev(sub_status="canceled", lapsed_at=NOW - 90 * DAY, comped_until=NOW + DAY)
        self.assertEqual((out["state"], out["comped"]), (entitlement.ACTIVE, True))

    def test_an_expired_comp_is_ignored(self):
        out = ev(sub_status="canceled", lapsed_at=NOW - 90 * DAY, comped_until=NOW - DAY)
        self.assertEqual(out["state"], entitlement.READ_ONLY)

    def test_comped_forever(self):
        out = ev(sub_status="comped", comped_until=datetime.max.replace(tzinfo=timezone.utc))
        self.assertEqual(out["state"], entitlement.ACTIVE)

    def test_every_stripe_status(self):
        cases = [
            ("trialing", {"trial_end": NOW + 3 * DAY}, entitlement.TRIALING),
            ("trialing", {"trial_end": NOW - 1 * DAY}, entitlement.GRACE),
            ("trialing", {"trial_end": NOW - 30 * DAY}, entitlement.READ_ONLY),
            ("active", {"current_period_end": NOW + 10 * DAY}, entitlement.ACTIVE),
            ("active", {"current_period_end": None}, entitlement.ACTIVE),
            # a missed webhook: the period lapsed and nobody told us
            ("active", {"current_period_end": NOW - 1 * DAY}, entitlement.GRACE),
            ("active", {"current_period_end": NOW - 30 * DAY}, entitlement.READ_ONLY),
            ("past_due", {"lapsed_at": NOW - 1 * DAY}, entitlement.GRACE),
            ("past_due", {"lapsed_at": NOW - 30 * DAY}, entitlement.READ_ONLY),
            ("unpaid", {"lapsed_at": NOW - 1 * DAY}, entitlement.GRACE),
            ("canceled", {"lapsed_at": NOW - 1 * DAY}, entitlement.GRACE),
            ("canceled", {"lapsed_at": NOW - 30 * DAY}, entitlement.READ_ONLY),
            ("incomplete", {"lapsed_at": NOW - 1 * DAY}, entitlement.GRACE),
            ("incomplete_expired", {"lapsed_at": NOW - 30 * DAY}, entitlement.READ_ONLY),
            ("paused", {"lapsed_at": NOW - 30 * DAY}, entitlement.READ_ONLY),
        ]
        for status, extra, expected in cases:
            with self.subTest(status=status, extra=extra):
                self.assertEqual(ev(sub_status=status, stripe_subscription_id="sub_1", **extra)["state"],
                                 expected)

    def test_an_unknown_status_fails_soft_into_grace(self):
        """Failing closed would lock out every paying customer at once the day Stripe adds a
        status: a self-inflicted outage with no warning."""
        out = ev(sub_status="quantum_superposition", stripe_subscription_id="sub_1")
        self.assertEqual(out["state"], entitlement.GRACE)

    def test_a_missing_subscription_row_self_heals_into_a_trial(self):
        out = ev(created_at=NOW - 3 * DAY)
        self.assertEqual((out["state"], out["days_left"]), (entitlement.TRIALING, TRIAL - 3))
        self.assertEqual(ev(created_at=NOW - 30 * DAY)["state"], entitlement.READ_ONLY)

    def test_boundaries_are_strict(self):
        """now == the boundary is past it, in both directions, so there is no ambiguity."""
        self.assertEqual(ev(sub_status="trialing", trial_end=NOW)["state"], entitlement.GRACE)
        self.assertEqual(ev(sub_status="trialing", trial_end=NOW + timedelta(seconds=1))["state"],
                         entitlement.TRIALING)
        lapsed = NOW - GRACE * DAY
        self.assertEqual(ev(sub_status="canceled", lapsed_at=lapsed)["state"], entitlement.READ_ONLY)
        self.assertEqual(ev(sub_status="canceled", lapsed_at=lapsed + timedelta(seconds=1))["state"],
                         entitlement.GRACE)

    def test_a_manually_extended_grace_wins_but_a_stale_one_does_not(self):
        extended = ev(sub_status="canceled", lapsed_at=NOW - 30 * DAY, grace_until=NOW + 5 * DAY)
        self.assertEqual(extended["state"], entitlement.GRACE)
        # stale value left by a missed webhook, older than the computed window
        stale = ev(sub_status="canceled", lapsed_at=NOW - DAY, grace_until=NOW - 20 * DAY)
        self.assertEqual(stale["state"], entitlement.GRACE)

    def test_days_left_counts_up_not_down_past_zero(self):
        self.assertEqual(ev(sub_status="trialing", trial_end=NOW + 3 * DAY)["days_left"], 3)
        self.assertIsNone(ev(sub_status="active", current_period_end=NOW + 3 * DAY)["days_left"])

    def test_public_json_serialises_datetimes_and_hides_nothing_secret(self):
        out = entitlement.public_json(ev(sub_status="trialing", trial_end=NOW + DAY,
                                         stripe_customer_id="cus_secret"))
        self.assertEqual(out["trial_end"], (NOW + DAY).isoformat())
        self.assertNotIn("stripe_customer_id", out)


class EnforceTest(unittest.TestCase):
    """The gate itself: deny-by-default over methods, not an opt-in decorator."""

    def test_read_only_paths(self):
        for path in sorted(entitlement.WRITE_ALLOWLIST):
            with self.subTest(path=path):
                self.assertTrue(entitlement.write_allowed(path))

    def test_ordinary_mutations_are_not_allowlisted(self):
        for path in ("/api/transactions/7", "/api/accounts", "/api/categories/3", "/api/budgets",
                     "/api/tags", "/api/statements", "/api/settings", "/api/ai/categorize"):
            with self.subTest(path=path):
                self.assertFalse(entitlement.write_allowed(path))

    def test_the_verified_read_only_post_is_allowed_but_its_siblings_are_not(self):
        self.assertTrue(entitlement.write_allowed("/api/transactions/7/rule-draft"))
        self.assertFalse(entitlement.write_allowed("/api/transactions/7/splits"))
        self.assertFalse(entitlement.write_allowed("/api/transactions/rule-draft"))

    def test_admin_paths_are_delegated_to_admin_required(self):
        self.assertTrue(entitlement.write_allowed("/api/admin/users/5/lock"))
        self.assertTrue(entitlement.write_allowed("/api/admin/backup"))

    def test_trailing_slashes_do_not_bypass_the_gate(self):
        self.assertFalse(entitlement.write_allowed("/api/accounts/"))
        self.assertTrue(entitlement.write_allowed("/api/billing/checkout/"))


if __name__ == "__main__":
    unittest.main()


class AllowlistMatchesRealRoutesTest(unittest.TestCase):
    """The allowlist is the only thing standing between a lapsed account and the whole API, so a
    typo or an entry left behind by a rename has to fail loudly."""

    @classmethod
    def setUpClass(cls):
        from unittest import mock
        import db as fake
        with mock.patch.object(fake, "query", return_value=None), \
                mock.patch.object(fake, "execute", return_value=None):
            import app
            cls.app = app.create_app()
        cls.rules = {str(r) for r in cls.app.url_map.iter_rules()}

    def test_every_allowlisted_path_resolves(self):
        adapter = self.app.url_map.bind("localhost")
        for path in sorted(entitlement.WRITE_ALLOWLIST):
            with self.subTest(path=path):
                self.assertTrue(any(r == path or r == path + "/" for r in self.rules)
                                or adapter.test(path, "POST") or adapter.test(path, "PUT"),
                                f"{path} is allowlisted but no route serves it")

    def test_the_gate_is_registered_after_the_entitlement_loader(self):
        names = [f.__name__ for f in self.app.before_request_funcs[None]]
        self.assertIn("refresh_session_user", names)
        self.assertLess(names.index("refresh_session_user"), names.index("_guards"))
