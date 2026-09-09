import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()
STRIPE = _stubs.install_stripe()

from flask import Flask  # noqa: E402

import billing  # noqa: E402
import billing_api  # noqa: E402
import util  # noqa: E402

NOW = datetime.now(timezone.utc)


class WebhookTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(billing_api.bp)
        FAKE.settings.clear()
        FAKE.settings.update({"stripe_secret_key": "sk_test", "stripe_price_monthly": "price_m",
                              "stripe_webhook_secret": "whsec_test"})
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _post(self, event, sig="t=1,v1=good"):
        STRIPE.Webhook.construct_event = lambda payload, s, secret: (
            event if s == sig else (_ for _ in ()).throw(STRIPE.SignatureVerificationError("bad")))
        c = self.app.test_client()
        with mock.patch.object(FAKE, "query", side_effect=self.q), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(billing_api, "db", FAKE), \
                mock.patch.object(billing, "db", FAKE):
            return c.post("/api/billing/webhook", data=b"{}", headers={"Stripe-Signature": sig},
                          content_type="application/json")

    def _event(self, etype, obj, eid="evt_1", created=1000):
        return {"id": eid, "type": etype, "created": created, "data": {"object": obj}}

    def test_a_bad_signature_is_rejected_and_writes_nothing(self):
        STRIPE.Webhook.construct_event = lambda *a: (_ for _ in ()).throw(
            STRIPE.SignatureVerificationError("bad"))
        c = self.app.test_client()
        with mock.patch.object(FAKE, "query", side_effect=self.q), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(billing_api, "db", FAKE):
            res = c.post("/api/billing/webhook", data=b"{}", headers={"Stripe-Signature": "nope"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.x.calls, [])

    def test_an_unconfigured_webhook_secret_never_accepts_anything(self):
        FAKE.settings["stripe_webhook_secret"] = ""
        res = self._post(self._event("invoice.paid", {"customer": "cus_1"}))
        self.assertEqual(res.status_code, 400)

    def test_a_replay_is_a_no_op(self):
        self.x.routes.append(("INSERT INTO stripe_events", 0))   # ON CONFLICT DO NOTHING
        res = self._post(self._event("invoice.paid", {"customer": "cus_1"}))
        self.assertEqual((res.status_code, res.get_json()), (200, {"received": True, "duplicate": True}))
        self.assertEqual(self.x.sql("UPDATE subscriptions"), [])

    def test_an_unknown_event_type_returns_200_not_4xx(self):
        """A 4xx makes Stripe retry it for three days."""
        res = self._post(self._event("charge.dispute.created", {"customer": "cus_1"}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p for _s, p in self.x.sql("UPDATE stripe_events SET status")][0][0], "ignored")

    def test_checkout_completed_verifies_the_claimed_user(self):
        self.q.routes.append(("FROM subscriptions WHERE stripe_customer_id", {"user_id": 7}))
        res = self._post(self._event("checkout.session.completed",
                                     {"customer": "cus_1", "client_reference_id": "99",
                                      "subscription": "sub_1"}))
        self.assertEqual(res.status_code, 400)
        self.assertIn("does not match", res.get_json()["error"])

    def test_checkout_completed_applies_the_subscription(self):
        self.q.routes.append(("FROM subscriptions WHERE stripe_customer_id", {"user_id": 7}))
        self.q.routes.append(("FROM users WHERE id", {"email": None, "username": "amy"}))
        res = self._post(self._event("checkout.session.completed",
                                     {"customer": "cus_1", "client_reference_id": "7",
                                      "subscription": "sub_1"}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("UPDATE subscriptions")[0][1]["uid"], 7)

    def test_out_of_order_events_are_dropped_by_the_where_clause(self):
        self.q.routes.append(("FROM subscriptions WHERE stripe_customer_id", {"user_id": 7}))
        self._post(self._event("customer.subscription.updated",
                               {"customer": "cus_1", "status": "active", "items": {"data": [{}]}},
                               created=500))
        sql, params = self.x.sql("UPDATE subscriptions")[0]
        self.assertIn("event_created)s >= last_event_at", sql)
        self.assertEqual(params["event_created"], 500)

    def test_payment_failed_opens_a_grace_window_once(self):
        self.q.routes.append(("FROM subscriptions WHERE stripe_customer_id", {"user_id": 7}))
        self.q.routes.append(("FROM users WHERE id", {"email": "a@b.co", "username": "amy"}))
        self.x.routes.append(("payment_failed_email_at = now()", 1))
        with mock.patch("mailer.send_async") as send:
            res = self._post(self._event("invoice.payment_failed", {"customer": "cus_1"}))
        self.assertEqual(res.status_code, 200)
        self.assertIn("grace_until", self.x.sql("UPDATE subscriptions")[0][0])
        self.assertEqual(send.call_args.args[0], "payment_failed")

    def test_invoice_paid_clears_the_lapse_and_its_email_stamps(self):
        self.q.routes.append(("FROM subscriptions WHERE stripe_customer_id", {"user_id": 7}))
        self._post(self._event("invoice.paid", {"customer": "cus_1"}))
        sql = self.x.sql("UPDATE subscriptions")[0][0]
        self.assertIn("lapsed_at = NULL", sql)
        self.assertIn("payment_failed_email_at = NULL", sql)


class PeriodEndTest(unittest.TestCase):
    """Recent Stripe versions moved current_period_end onto the subscription item; reading only
    the old place stores NULL, which the evaluator treats as 'active forever'."""

    def test_reads_the_item_first_then_the_subscription(self):
        ts = int(NOW.timestamp())
        self.assertEqual(billing._period_end({"items": {"data": [{"current_period_end": ts}]}}),
                         datetime.fromtimestamp(ts, timezone.utc))
        self.assertEqual(billing._period_end({"items": {"data": [{}]}, "current_period_end": ts}),
                         datetime.fromtimestamp(ts, timezone.utc))
        self.assertIsNone(billing._period_end({"items": {"data": [{}]}}))


class BillingEndpointsTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(billing_api.bp)
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, uid=3):
        c = self.app.test_client()
        if uid:
            with c.session_transaction() as s:
                s["user_id"] = uid
                s["role"] = "user"
        with mock.patch.object(FAKE, "query", side_effect=self.q), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(billing_api, "db", FAKE), \
                mock.patch.object(billing, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None,
                                      content_type="application/json")

    def _enable(self):
        FAKE.settings.update({"stripe_secret_key": "sk_test", "stripe_price_monthly": "price_m"})

    def test_checkout_is_400_when_billing_is_off(self):
        self.assertEqual(self._call("post", "/api/billing/checkout", {}).status_code, 400)

    def test_checkout_requires_a_verified_email(self):
        self._enable()
        self.q.routes.append(("FROM users u LEFT JOIN subscriptions", {
            "id": 3, "role": "user", "email": "a@b.co", "email_verified_at": None,
            "sub_status": "trialing", "trial_end": NOW + timedelta(days=3)}))
        res = self._call("post", "/api/billing/checkout", {"plan": "monthly"})
        self.assertEqual((res.status_code, res.get_json()["code"]), (403, "email_unverified"))

    def test_checkout_returns_only_the_url(self):
        self._enable()
        self.q.routes.append(("FROM users u LEFT JOIN subscriptions", {
            "id": 3, "role": "user", "email": "a@b.co", "email_verified_at": NOW,
            "sub_status": "trialing", "trial_end": NOW + timedelta(days=3),
            "stripe_customer_id": "cus_1"}))
        res = self._call("post", "/api/billing/checkout", {"plan": "monthly"})
        self.assertEqual((res.status_code, res.get_json()), (200, {"url": "https://checkout.test/s"}))

    def test_portal_409s_without_a_customer(self):
        self._enable()
        self.q.routes.append(("FROM users u LEFT JOIN subscriptions",
                              {"id": 3, "role": "user", "stripe_customer_id": None}))
        self.assertEqual(self._call("post", "/api/billing/portal").status_code, 409)

    def test_status_requires_a_session(self):
        self.assertEqual(self._call("get", "/api/billing/status", uid=None).status_code, 401)


if __name__ == "__main__":
    unittest.main()
