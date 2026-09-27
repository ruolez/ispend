"""The revenue ledger: monthly value, change classification, invoices, webhooks and reconcile."""
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()
STRIPE = _stubs.install_stripe()

from flask import Flask  # noqa: E402

import billing_api  # noqa: E402
import billing_reconcile  # noqa: E402
import ledger  # noqa: E402
import ops_tick  # noqa: E402
import util  # noqa: E402

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
T = int(NOW.timestamp())
DAY = 86400


def price(unit, interval="month", count=1, pid="price_m"):
    return {"id": pid, "unit_amount": unit, "currency": "usd",
            "recurring": {"interval": interval, "interval_count": count}}


def sub(unit=1000, interval="month", count=1, status="active", qty=1, discounts=None, **extra):
    return {"id": "sub_1", "status": status, "customer": "cus_1",
            "items": {"data": [{"price": price(unit, interval, count), "quantity": qty}]},
            "discounts": discounts or [], **extra}


def coupon(duration="forever", percent=None, amount=None, end=None):
    return {"coupon": {"id": "C1", "percent_off": percent, "amount_off": amount, "duration": duration},
            "end": end}


class MrrTest(unittest.TestCase):
    def test_monthly_value(self):
        cases = [
            # (subscription, expected MRR in cents)
            (sub(999), 999),
            (sub(9999, "year"), 833),
            (sub(3000, "month", 3), 1000),
            (sub(500, "week"), 2167),
            (sub(1000, qty=2), 2000),
            (sub(1000, status="past_due"), 1000),
            (sub(1000, status="trialing"), 0),
            (sub(1000, status="canceled"), 0),
            (sub(1000, status="incomplete"), 0),
            (sub(1000, discounts=[coupon(percent=20)]), 800),
            (sub(1000, discounts=[coupon(amount=250)]), 750),
            (sub(1000, discounts=[coupon(amount=5000)]), 0),
            (sub(1000, discounts=[coupon("once", percent=50)]), 1000),
            (sub(1000, discounts=[coupon("repeating", percent=50, end=T + DAY)]), 500),
            (sub(1000, discounts=[coupon("repeating", percent=50, end=T - DAY)]), 1000),
            (sub(12000, "year", discounts=[coupon(percent=25)]), 750),
            (sub(1000, discounts=["di_unexpanded"]), 1000),
        ]
        for s, expected in cases:
            with self.subTest(sub=s):
                self.assertEqual(ledger.snapshot(s, now=NOW)["mrr_cents"], expected)

    def test_legacy_plan_objects_and_the_old_discount_field(self):
        legacy = {"status": "active", "items": {"data": [{"plan": {"amount": 2400, "interval": "year",
                                                                    "currency": "cad"}}]},
                  "discount": coupon(percent=50)}
        snap = ledger.snapshot(legacy, now=NOW)
        self.assertEqual((snap["mrr_cents"], snap["currency"], snap["billing_interval"]), (100, "cad", "year"))

    def test_snapshot_flattens_the_price_facts(self):
        s = sub(1000, discounts=[coupon(percent=10)], start_date=T - 30 * DAY, canceled_at=None)
        snap = ledger.snapshot(s, now=NOW)
        self.assertEqual({k: snap[k] for k in ("currency", "unit_amount_cents", "quantity", "billing_interval",
                                               "interval_count", "discount", "started_at")},
                         {"currency": "usd", "unit_amount_cents": 1000, "quantity": 1, "billing_interval": "month",
                          "interval_count": 1, "started_at": NOW - timedelta(days=30),
                          "discount": {"coupon": "C1", "percent_off": 10, "amount_off": None,
                                       "duration": "forever", "ends_at": None}})


class ClassifyTest(unittest.TestCase):
    def test_changes(self):
        def row(status, plan=None, mrr=0, cape=False):
            return {"status": status, "plan": plan, "mrr_cents": mrr, "cancel_at_period_end": cape}
        cases = [
            (row("trialing"), row("active", "monthly", 999), [("subscribed", 0, 999)]),
            (row("active", "monthly", 999), row("canceled", "monthly", 0), [("canceled", 999, 0)]),
            (row("active", "monthly", 999), row("unpaid", "monthly", 0), [("canceled", 999, 0)]),
            (row("active", "monthly", 999), row("paused", "monthly", 0), [("status_changed", 999, 0)]),
            (row("active", "monthly", 999), row("active", "yearly", 833), [("plan_changed", 999, 833)]),
            (row("active", "monthly", 999), row("active", "monthly", 799), [("mrr_changed", 999, 799)]),
            (row("none"), row("trialing", "monthly"), [("trial_started", 0, 0)]),
            (row("active", "monthly", 999), row("past_due", "monthly", 999), [("status_changed", 999, 999)]),
            (row("active", "monthly", 999), row("active", "monthly", 999, True), [("cancel_scheduled", 999, 999)]),
            (row("active", "monthly", 999, True), row("active", "monthly", 999), [("cancel_unscheduled", 999, 999)]),
            (row("active", "monthly", 999), row("active", "monthly", 999), []),
            (row("active", "monthly", 999, True), row("canceled", "monthly", 0), [("canceled", 999, 0)]),
            (None, row("active", "monthly", 999), [("subscribed", 0, 999)]),
        ]
        for old, new, expected in cases:
            with self.subTest(old=old, new=new):
                self.assertEqual(ledger.classify(old, new), expected)


NEW_INVOICE = {
    "id": "in_new", "customer": "cus_1", "currency": "usd", "subtotal": 1000, "amount_paid": 870,
    "billing_reason": "subscription_cycle", "attempt_count": 1,
    "total_taxes": [{"amount": 70}], "total_discount_amounts": [{"amount": 200}],
    "discounts": [coupon(percent=20)],
    "parent": {"subscription_details": {"subscription": "sub_1"}},
    "payments": {"data": [{"payment": {"payment_intent": "pi_1"}}]},
    "status_transitions": {"paid_at": T},
    "lines": {"data": [{"period": {"start": T, "end": T + 30 * DAY},
                        "pricing": {"price_details": {"id": "price_m"}}}]},
}
OLD_INVOICE = {
    "id": "in_old", "customer": "cus_1", "currency": "cad", "subtotal": 2400, "amount_paid": 2400, "tax": 0,
    "subscription": "sub_0", "payment_intent": "pi_0", "charge": "ch_0",
    "lines": {"data": [{"price": price(2400, "year", pid="price_y"), "period": {"start": T, "end": T + 365 * DAY}}]},
}


class InvoiceFactsTest(unittest.TestCase):
    def test_both_api_shapes(self):
        new = ledger.invoice_facts(NEW_INVOICE)
        old = ledger.invoice_facts(OLD_INVOICE)
        pick = ("stripe_subscription_id", "stripe_payment_intent_id", "stripe_charge_id", "currency",
                "subtotal_cents", "discount_cents", "tax_cents", "amount_paid_cents", "coupon", "price_id",
                "paid_at", "period_start")
        self.assertEqual({k: new[k] for k in pick},
                         {"stripe_subscription_id": "sub_1", "stripe_payment_intent_id": "pi_1",
                          "stripe_charge_id": None, "currency": "usd", "subtotal_cents": 1000,
                          "discount_cents": 200, "tax_cents": 70, "amount_paid_cents": 870, "coupon": "C1",
                          "price_id": "price_m", "paid_at": NOW, "period_start": NOW})
        self.assertEqual({k: old[k] for k in pick},
                         {"stripe_subscription_id": "sub_0", "stripe_payment_intent_id": "pi_0",
                          "stripe_charge_id": "ch_0", "currency": "cad", "subtotal_cents": 2400,
                          "discount_cents": 0, "tax_cents": 0, "amount_paid_cents": 2400, "coupon": None,
                          "price_id": "price_y", "paid_at": None, "period_start": NOW})
        self.assertEqual(old["billing_interval"], "year")


class _Db(unittest.TestCase):
    def setUp(self):
        FAKE.settings.clear()
        FAKE.settings.update({"stripe_secret_key": "sk_test", "stripe_price_monthly": "price_m",
                              "stripe_price_yearly": "price_y", "stripe_webhook_secret": "whsec"})
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)
        self.patches = [mock.patch.object(FAKE, "query", side_effect=self.q),
                        mock.patch.object(FAKE, "execute", side_effect=self.x)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()


class WebhookLedgerTest(_Db):
    def _post(self, etype, obj, eid="evt_9", created=T):
        app = Flask(__name__)
        app.secret_key = "t"
        app.register_blueprint(billing_api.bp)
        event = {"id": eid, "type": etype, "created": created, "data": {"object": obj}}
        STRIPE.Webhook.construct_event = lambda payload, s, secret: event
        with mock.patch.object(util, "db", FAKE), mock.patch("mailer.send_async"):
            return app.test_client().post("/api/billing/webhook", data=b"{}", headers={"Stripe-Signature": "x"})

    def test_a_subscription_change_is_written_to_the_ledger_with_its_event_id(self):
        self.q.routes.append(("FROM subscriptions WHERE stripe_customer_id", {"user_id": 7}))
        self.q.routes.append(("JOIN subscriptions s ON s.user_id = u.id WHERE u.id",
                              {"id": 7, "role": "user", "created_at": NOW, "sub_status": "active"}))
        self.x.routes.insert(0, ("WITH old AS", {
            "o_status": "trialing", "o_plan": "monthly", "o_mrr": 0, "o_cape": False, "status": "active",
            "plan": "monthly", "mrr_cents": 1000, "cancel_at_period_end": False, "currency": "usd",
            "stripe_subscription_id": "sub_1"}))
        res = self._post("customer.subscription.updated", sub(1000))
        self.assertEqual(res.status_code, 200)
        params = self.x.sql("WITH old AS")[0][1]
        self.assertEqual((params["mrr_cents"], params["unit_amount_cents"], params["uid"]), (1000, 1000, 7))
        (sql, event), = self.x.sql("INSERT INTO subscription_events")
        self.assertEqual(event[:5] + event[9:12] + event[13:], (7, "sub_1", "evt_9", "webhook", "subscribed",
                                                                0, 1000, "usd", NOW))
        self.assertEqual(len(self.x.sql("SET ent_state")), 1)
        self.assertEqual(self.x.sql("UPDATE stripe_events SET status")[0][1][:2], ("processed", 7))

    def test_a_dropped_out_of_order_event_writes_no_history(self):
        self.q.routes.append(("FROM subscriptions WHERE stripe_customer_id", {"user_id": 7}))
        self.x.routes.insert(0, ("WITH old AS", None))
        self._post("customer.subscription.updated", sub(1000))
        self.assertEqual(self.x.sql("INSERT INTO subscription_events"), [])

    def test_invoices_are_recorded_paid_or_failed(self):
        for etype, status in (("invoice.paid", "paid"), ("invoice.payment_failed", "failed")):
            with self.subTest(etype=etype):
                self.q.routes = [("FROM subscriptions WHERE stripe_customer_id", {"user_id": 7}),
                                 ("FROM users WHERE id", {"email": None, "username": "amy"})]
                self.x = _stubs.Router([("INSERT INTO payments", {"inserted": True})], default=1)
                with mock.patch.object(FAKE, "execute", side_effect=self.x):
                    self._post(etype, NEW_INVOICE)
                (sql, params), = self.x.sql("INSERT INTO payments")
                self.assertEqual((params["status"], params["user_id"], params["plan"], params["amount_paid_cents"]),
                                 (status, 7, "monthly", 870))
                self.assertEqual(params["failed_at"] is not None, status == "failed")

    def test_a_refund_finds_its_payment_by_charge_then_payment_intent(self):
        self.x.routes.insert(0, ("WHERE stripe_charge_id", None))
        self.x.routes.insert(0, ("WHERE stripe_payment_intent_id", {"user_id": 7}))
        res = self._post("charge.refunded", {"id": "ch_1", "payment_intent": "pi_1", "amount": 1000,
                                             "amount_captured": 1000, "amount_refunded": 400})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("WHERE stripe_payment_intent_id")[0][1], (400, "partially_refunded", "pi_1"))
        self.assertEqual(self.x.sql("UPDATE stripe_events SET status")[0][1][:2], ("processed", 7))


class ReconcileTest(_Db):
    def test_history_from_stripe_timestamps(self):
        trial_start, trial_end, ended = T - 60 * DAY, T - 46 * DAY, T - 10 * DAY
        cases = [
            ("trial then paying", sub(1000, trial_start=trial_start, trial_end=trial_end, start_date=trial_start),
             None, [("trial_started", 0, 0, trial_start), ("subscribed", 0, 1000, trial_end)]),
            ("cancelled inside the trial", sub(1000, status="canceled", trial_start=trial_start,
                                               trial_end=trial_end, start_date=trial_start,
                                               ended_at=trial_start + 3 * DAY),
             None, [("trial_started", 0, 0, trial_start), ("canceled", 0, 0, trial_start + 3 * DAY)]),
            ("paid then ended", sub(9999, "year", status="canceled", start_date=trial_start, ended_at=ended),
             None, [("subscribed", 0, 833, trial_start), ("canceled", 833, 0, ended)]),
            ("only what predates the ledger", sub(1000, start_date=trial_start),
             NOW - timedelta(days=90), []),
        ]
        for name, s, before, expected in cases:
            with self.subTest(name):
                got = [(k, m0, m1, int(at.timestamp())) for k, m0, m1, at, _ in
                       billing_reconcile.history_events(s, before=before, now=NOW)]
                self.assertEqual(got, expected)

    def test_primary_subscription(self):
        old = {"id": "sub_old", "status": "canceled", "created": 1}
        live = {"id": "sub_live", "status": "active", "created": 2}
        newer_dead = {"id": "sub_dead", "status": "incomplete_expired", "created": 3}
        self.assertEqual(billing_reconcile.primary_subscription([old, live, newer_dead])["id"], "sub_live")
        self.assertEqual(billing_reconcile.primary_subscription([old, live], "sub_old")["id"], "sub_old")
        self.assertEqual(billing_reconcile.primary_subscription([old, newer_dead])["id"], "sub_dead")

    def test_run_fixes_drift_imports_history_and_invoices(self):
        stripe_sub = sub(1000, start_date=T - 40 * DAY, created=T - 40 * DAY)
        STRIPE.Subscription.list = lambda **kw: types.SimpleNamespace(auto_paging_iter=lambda: iter(
            [stripe_sub, {**stripe_sub, "id": "sub_x", "customer": "cus_unknown"}]))
        STRIPE.Invoice = types.SimpleNamespace()
        STRIPE.Invoice.list = lambda **kw: types.SimpleNamespace(auto_paging_iter=lambda: iter(
            [{**NEW_INVOICE, "status": "paid"}, {"id": "in_draft", "status": "draft"}]))
        self.q.routes += [
            ("stripe_customer_id = %s", lambda sql, p: {"user_id": 7} if p == ("cus_1",) else None),
            ("MIN(occurred_at)", []),
            ("FROM subscriptions WHERE user_id", {"stripe_subscription_id": "sub_1", "status": "trialing",
                                                  "plan": "monthly", "mrr_cents": 0,
                                                  "cancel_at_period_end": False, "current_period_end": None}),
        ]
        self.x.routes.insert(0, ("INSERT INTO payments", {"inserted": True}))
        self.x.routes.insert(0, ("WITH old AS", None))
        with mock.patch.object(billing_reconcile.db, "set_setting") as set_setting:
            result = billing_reconcile.run()
        self.assertEqual({k: result[k] for k in ("checked", "fixed", "history_events", "payments_added",
                                                 "unknown_customers", "error")},
                         {"checked": 1, "fixed": [{"user_id": 7, "fields": ["status", "mrr_cents"]}],
                          "history_events": 1, "payments_added": 1, "unknown_customers": 1, "error": None})
        self.assertEqual(len(self.x.sql("kind = 'baseline'")), 1, "the placeholder gives way to real history")
        self.assertEqual(json.loads(set_setting.call_args_list[-2].args[1])["checked"], 1)
        self.assertEqual(set_setting.call_args_list[-1].args, (billing_reconcile.RUNNING_KEY, ""))

    def test_a_stripe_failure_is_recorded_and_the_running_flag_cleared(self):
        STRIPE.Subscription.list = lambda **kw: (_ for _ in ()).throw(RuntimeError("stripe down"))
        with mock.patch.object(billing_reconcile.db, "set_setting") as set_setting, \
                self.assertLogs("billing_reconcile", "ERROR"):
            result = billing_reconcile.run()
        self.assertEqual(result["error"], "stripe down")
        self.assertEqual(set_setting.call_args_list[-1].args, (billing_reconcile.RUNNING_KEY, ""))


class ReconcileEndpointTest(_Db):
    def _post(self, body=None):
        import admin_billing
        app = Flask(__name__)
        app.secret_key = "t"
        app.register_blueprint(admin_billing.bp)
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"], sess["role"] = 1, "admin"
        with mock.patch.object(util, "db", FAKE), mock.patch("jobs.spawn") as spawn:
            res = c.post("/api/admin/billing/reconcile", json=body or {})
        return res, spawn

    def test_refused_without_stripe_or_while_running(self):
        FAKE.settings["stripe_secret_key"] = ""
        self.assertEqual(self._post()[0].status_code, 409)
        FAKE.settings["stripe_secret_key"] = "sk_test"
        FAKE.settings[billing_reconcile.RUNNING_KEY] = datetime.now(timezone.utc).isoformat()
        res, spawn = self._post()
        self.assertEqual((res.status_code, spawn.call_count), (409, 0))

    def test_an_incremental_run_starts_two_days_before_the_last_one(self):
        last = datetime(2026, 9, 10, tzinfo=timezone.utc)
        FAKE.settings[billing_reconcile.LAST_KEY] = json.dumps({"started_at": last.isoformat()})
        for body, since in (({}, last - timedelta(days=2)), ({"full": True}, None)):
            with self.subTest(body=body):
                FAKE.settings[billing_reconcile.RUNNING_KEY] = ""
                res, spawn = self._post(body)
                self.assertEqual(res.status_code, 202)
                self.assertEqual(spawn.call_args.args[:2], (billing_reconcile.run, since))


class OpsTickTest(_Db):
    def test_first_run_imports_everything_then_daily_with_overlap(self):
        self.assertEqual(ops_tick._reconcile_due(NOW), (True, None))
        FAKE.settings[ops_tick.BACKFILL_KEY] = "1"
        cases = [
            ((NOW - timedelta(hours=3)).isoformat(), False),
            ((NOW - timedelta(hours=25)).isoformat(), True),
        ]
        for started, due in cases:
            with self.subTest(started=started):
                FAKE.settings[billing_reconcile.LAST_KEY] = json.dumps({"started_at": started})
                self.assertEqual(ops_tick._reconcile_due(NOW),
                                 (due, datetime.fromisoformat(started) - ops_tick.RECONCILE_OVERLAP))

    def test_only_changed_access_states_are_written(self):
        created = NOW - timedelta(days=3)
        rows = [{"id": 1, "role": "admin", "created_at": created, "sub_status": "none", "ent_state": "admin_exempt"},
                {"id": 2, "role": "user", "created_at": created, "sub_status": "comped",
                 "comped_until": datetime(9999, 1, 1, tzinfo=timezone.utc), "ent_state": None}]
        self.q.routes.append(("FROM users u JOIN subscriptions s", rows))
        self.assertEqual(ledger.refresh_all_ent_states(), 1)
        self.assertEqual(self.x.sql("SET ent_state")[0][1], ("active", 2))


if __name__ == "__main__":
    unittest.main()
