import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

_stubs.install()

import admin_privacy  # noqa: E402
import geo  # noqa: E402
import useragent  # noqa: E402

CHROME_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")
EDGE_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36 Edg/128.0.2739.42")
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
SAFARI_IPAD = ("Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
               "Version/17.5 Mobile/15E148 Safari/604.1")
CHROME_ANDROID = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/127.0.0.0 Mobile Safari/537.36")
ANDROID_TABLET = ("Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
CRIOS = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
         "CriOS/128.0.6613.98 Mobile/15E148 Safari/604.1")


class ParseTest(unittest.TestCase):
    def test_real_user_agents(self):
        cases = [
            (CHROME_MAC, {"browser": "Chrome 128", "os": "macOS", "device": "desktop"}),
            (EDGE_WIN, {"browser": "Edge 128", "os": "Windows", "device": "desktop"}),
            (SAFARI_IPHONE, {"browser": "Safari 18", "os": "iOS", "device": "mobile"}),
            (SAFARI_IPAD, {"browser": "Safari 17", "os": "iOS", "device": "tablet"}),
            (CHROME_ANDROID, {"browser": "Chrome 127", "os": "Android", "device": "mobile"}),
            (ANDROID_TABLET, {"browser": "Chrome 126", "os": "Android", "device": "tablet"}),
            (FIREFOX_LINUX, {"browser": "Firefox 130", "os": "Linux", "device": "desktop"}),
            (CRIOS, {"browser": "Chrome 128", "os": "iOS", "device": "mobile"}),
            ("curl/8.4.0", {"browser": None, "os": None, "device": "bot"}),
            ("python-requests/2.32.3", {"browser": None, "os": None, "device": "bot"}),
            ("", {"browser": None, "os": None, "device": "unknown"}),
            (None, {"browser": None, "os": None, "device": "unknown"}),
            ("something odd", {"browser": None, "os": None, "device": "unknown"}),
        ]
        for ua, expected in cases:
            with self.subTest(ua=ua):
                self.assertEqual(useragent.parse(ua), expected)

    def test_label(self):
        self.assertEqual([useragent.label(useragent.parse(u)) for u in (SAFARI_IPHONE, "curl/8", "")],
                         ["Safari 18 on iOS", "Unknown device", "Unknown device"])


class NetworkPrefixTest(unittest.TestCase):
    def test_prefixes(self):
        cases = {"203.0.113.77": "203.0.113.0/24", "2001:db8:abcd:12::1": "2001:db8:abcd::/48",
                 "not-an-ip": None, None: None, "": None}
        self.assertEqual({ip: geo.network_prefix(ip) for ip in cases}, cases)

    def test_country_is_none_without_a_local_source(self):
        self.assertIsNone(geo.country("8.8.8.8", {"CF-IPCountry": "US"}))


class RedactTest(unittest.TestCase):
    def test_user_content_is_hidden_and_counts_are_kept(self):
        cases = [
            # (detail, clean, hidden)
            ({"id": 4, "filename": "chase-march.pdf", "kind": "pdf"}, {"id": 4, "kind": "pdf"}, 1),
            ({"id": 9, "name": "Joint checking"}, {"id": 9}, 1),
            ({"merchant_key": "starbucks"}, {}, 1),
            ({"category_id": 3, "month": "2026-09", "amount": 450.0}, {"category_id": 3, "month": "2026-09"}, 1),
            ({"id": 1, "imported": 40, "categorized": {"rule": 10, "none": 30}, "ai_queued": False},
             {"id": 1, "imported": 40, "categorized": {"rule": 10, "none": 30}, "ai_queued": False}, 0),
            ({"id": 2, "fields": ["category_id", "notes"]}, {"id": 2, "fields": ["category_id", "notes"]}, 0),
            ({"references": {"rules": 2, "budgets": {"x": 1}}}, {}, 1),
            ({"status": "x" * 41}, {}, 1),
            (None, None, 0),
            ("raw text", None, 1),
        ]
        for detail, clean, hidden in cases:
            with self.subTest(detail=detail):
                self.assertEqual(admin_privacy.redact_detail(detail), (clean, hidden))

    def test_admin_rows_pass_whole_and_user_rows_carry_a_hidden_count(self):
        admin_row = {"by_admin": True, "detail": {"id": 5, "username": "eve", "reason": "spam"}}
        user_row = {"by_admin": False, "detail": {"id": 5, "name": "Savings"}}
        self.assertEqual(admin_privacy.redact_row(admin_row), admin_row)
        self.assertEqual(admin_privacy.redact_row(user_row),
                         {"by_admin": False, "detail": {"id": 5}, "hidden_fields": 1})


if __name__ == "__main__":
    unittest.main()
