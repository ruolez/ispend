"""Landing-page copy normalisation: pure, no database, no network."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

_stubs.install()

import landing  # noqa: E402

D = landing.DEFAULT_LANDING


class NormalizeLandingTest(unittest.TestCase):
    def test_absent_input_returns_the_shipped_defaults(self):
        self.assertEqual(landing.normalize(None), D)

    def test_non_mapping_input_returns_the_shipped_defaults(self):
        for junk in ("", "not json", 7, [], ["a"], True):
            self.assertEqual(landing.normalize(junk), D, junk)

    def test_json_string_is_parsed(self):
        got = landing.normalize(json.dumps({"heading": "Pick a plan"}))
        self.assertEqual(got["heading"], "Pick a plan")

    def test_unknown_keys_are_dropped(self):
        got = landing.normalize({"heading": "Hi", "script": "<script>", "paid": {"evil": 1},
                                 "free": {"name": "Self-hosted"}})
        self.assertEqual(set(got), set(D))
        self.assertEqual(set(got["paid"]), set(D["paid"]))

    def test_whitespace_is_collapsed_and_markup_stripped(self):
        got = landing.normalize({"heading": "  Simple\n\n  pricing <b>now</b> "})
        self.assertEqual(got["heading"], "Simple pricing now")

    def test_text_is_clamped_to_its_limit(self):
        got = landing.normalize({"paid": {"name": "N" * 500}})
        self.assertEqual(got["paid"]["name"], "N" * landing.LIMITS["name"])

    def test_blank_required_text_falls_back_to_the_default(self):
        got = landing.normalize({"heading": "   ", "paid": {"name": "", "cta_label": "  "}})
        self.assertEqual(got["heading"], D["heading"])
        self.assertEqual(got["paid"]["name"], D["paid"]["name"])
        self.assertEqual(got["paid"]["cta_label"], D["paid"]["cta_label"])

    def test_optional_text_can_be_cleared(self):
        got = landing.normalize({"paid": {"tagline": ""}, "footnote": "  "})
        self.assertEqual(got["paid"]["tagline"], "")
        self.assertEqual(got["footnote"], "")

    def test_the_ribbon_is_off_by_default_and_settable(self):
        self.assertEqual(landing.normalize({})["paid"]["badge"], "")
        self.assertEqual(landing.normalize({"paid": {"badge": " Best value "}})["paid"]["badge"], "Best value")

    def test_features_are_capped_stripped_and_de_blanked(self):
        given = ["  keep me  ", "", "   ", *[f"f{i}" for i in range(20)]]
        got = landing.normalize({"paid": {"features": given}})
        self.assertEqual(got["paid"]["features"],
                         ["keep me", *[f"f{i}" for i in range(landing.MAX_FEATURES - 1)]])

    def test_features_absent_keeps_the_default_but_an_empty_list_clears_them(self):
        self.assertEqual(landing.normalize({"paid": {}})["paid"]["features"], D["paid"]["features"])
        self.assertEqual(landing.normalize({"paid": {"features": []}})["paid"]["features"], [])

    def test_features_reject_non_string_members(self):
        got = landing.normalize({"paid": {"features": ["ok", 5, None, {"a": 1}, "also ok"]}})
        self.assertEqual(got["paid"]["features"], ["ok", "also ok"])

    def test_normalize_is_idempotent(self):
        messy = [
            {}, {"paid": {"features": ["a" * 400, "  b  "] * 30}},
            {"heading": " x " * 90, "paid": {"cta_label": "   "}},
            {"paid": {"badge": "Best value"}, "footnote": "", "yearly_note": "  two  months  free "},
            json.dumps({"sub": "<em>Start</em> free"}),
        ]
        for given in messy:
            once = landing.normalize(given)
            self.assertEqual(landing.normalize(once), once, given)
            self.assertEqual(landing.normalize(json.dumps(once)), once, given)

    def test_defaults_are_self_consistent(self):
        self.assertEqual(landing.normalize(D), D)
        self.assertLessEqual(len(D["paid"]["features"]), landing.MAX_FEATURES)


class LoadTest(unittest.TestCase):
    def setUp(self):
        _stubs.install().settings.clear()

    def test_load_returns_defaults_when_nothing_is_stored(self):
        self.assertEqual(landing.load(), D)

    def test_load_normalises_whatever_is_stored(self):
        _stubs.install().settings[landing.SETTING_KEY] = json.dumps({"heading": "  Stored  "})
        self.assertEqual(landing.load()["heading"], "Stored")

    def test_load_survives_a_corrupt_row(self):
        _stubs.install().settings[landing.SETTING_KEY] = "{not json"
        self.assertEqual(landing.load(), D)

    def test_save_writes_normalised_json(self):
        landing.save({"heading": "  Plans  ", "paid": {"name": "Pro" * 100}})
        stored = json.loads(_stubs.install().settings[landing.SETTING_KEY])
        self.assertEqual(stored["heading"], "Plans")
        self.assertEqual(len(stored["paid"]["name"]), landing.LIMITS["name"])


if __name__ == "__main__":
    unittest.main()
