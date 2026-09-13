"""The one unauthenticated surface the landing page reads."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()
STRIPE = _stubs.install_stripe()

from flask import Flask  # noqa: E402

import billing  # noqa: E402
import landing  # noqa: E402
import public_api  # noqa: E402


class PublicLandingTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(public_api.bp)
        self.client = self.app.test_client()
        FAKE.settings.clear()
        billing._prices_cache.update({"at": 0, "data": None})
        STRIPE.Price.retrieve = lambda pid: {
            "unit_amount": 900 if pid == "price_m" else 9000,
            "currency": "usd", "recurring": {"interval": "month" if pid == "price_m" else "year"}}

    def _sell(self):
        FAKE.settings.update({"stripe_secret_key": "sk_test", "stripe_price_monthly": "price_m",
                              "stripe_price_yearly": "price_y", "signup_enabled": "1"})

    def _get(self):
        res = self.client.get("/api/public/landing")
        return res, json.loads(res.data)

    def test_anonymous_request_succeeds(self):
        res, _ = self._get()
        self.assertEqual(res.status_code, 200)

    def test_payload_shape(self):
        res, body = self._get()
        self.assertEqual(set(body),
                         {"app_name", "signup_enabled", "billing_enabled", "trial_days", "prices", "landing"})
        self.assertEqual(body["app_name"], "iSpend")

    def test_billing_off_reports_no_plans(self):
        res, body = self._get()
        self.assertIs(body["billing_enabled"], False)
        self.assertEqual(body["prices"], [])

    def test_billing_on_returns_both_intervals_without_stripe_identifiers(self):
        self._sell()
        res, body = self._get()
        self.assertIs(body["billing_enabled"], True)
        self.assertEqual(body["prices"], [
            {"plan": "monthly", "amount_cents": 900, "currency": "USD", "interval": "month"},
            {"plan": "yearly", "amount_cents": 9000, "currency": "USD", "interval": "year"},
        ])
        self.assertNotIn(b"price_id", res.data)
        self.assertNotIn(b"price_m", res.data)

    def test_a_stripe_outage_still_renders_the_page(self):
        self._sell()

        def boom(pid):
            raise RuntimeError("stripe is down")

        STRIPE.Price.retrieve = boom
        res, body = self._get()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["prices"], [])
        self.assertIs(body["billing_enabled"], True)

    def test_signup_switch_is_reflected(self):
        _, off = self._get()
        self.assertIs(off["signup_enabled"], False)
        FAKE.settings["signup_enabled"] = "1"
        _, on = self._get()
        self.assertIs(on["signup_enabled"], True)

    def test_trial_days_come_from_the_admin_setting(self):
        _, body = self._get()
        self.assertEqual(body["trial_days"], 14)
        FAKE.settings["billing_trial_days"] = "30"
        _, body = self._get()
        self.assertEqual(body["trial_days"], 30)

    def test_copy_comes_from_the_admin_setting_and_is_normalised(self):
        FAKE.settings[landing.SETTING_KEY] = json.dumps(
            {"heading": "  Pick a plan  ", "paid": {"name": "Household", "features": ["<b>One</b>"]}})
        _, body = self._get()
        self.assertEqual(body["landing"]["heading"], "Pick a plan")
        self.assertEqual(body["landing"]["paid"]["name"], "Household")
        self.assertEqual(body["landing"]["paid"]["features"], ["One"])
        self.assertEqual(body["landing"]["paid"]["cta_label"], landing.DEFAULT_LANDING["paid"]["cta_label"])

    def test_unconfigured_server_still_returns_complete_copy(self):
        _, body = self._get()
        self.assertEqual(body["landing"], landing.DEFAULT_LANDING)


if __name__ == "__main__":
    unittest.main()
