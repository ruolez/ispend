import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

_stubs.install()

import builtin_hints  # noqa: E402
import seed_categories  # noqa: E402

ICONS_JS = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "js", "icons.js")


def slugs(taxonomy):
    return [slug for slug, *_rest, children in taxonomy] + [c[0] for *_r, children in taxonomy for c in children]


class TaxonomiesTest(unittest.TestCase):
    def test_both_kinds_carry_what_transfer_pairing_needs(self):
        needed = {"transfers", "transfers.card_payment", "transfers.internal"}
        for kind, taxonomy in seed_categories.TAXONOMIES.items():
            with self.subTest(kind=kind):
                self.assertEqual(needed - set(slugs(taxonomy)), set())

    def test_slugs_are_unique_and_children_sit_under_their_parent(self):
        for kind, taxonomy in seed_categories.TAXONOMIES.items():
            with self.subTest(kind=kind):
                all_slugs = slugs(taxonomy)
                self.assertEqual(len(all_slugs), len(set(all_slugs)))
                strays = [c[0] for slug, *_r, children in taxonomy for c in children if not c[0].startswith(slug + ".")]
                self.assertEqual(strays, [])

    def test_business_books_still_get_hints_for_common_merchants(self):
        """A hint whose slug (or its parent) is missing from the profile simply does nothing; the
        business set keeps the ones a business also sees."""
        business = set(slugs(seed_categories.BUSINESS_TAXONOMY))
        hinted = set(builtin_hints.all_slugs())
        shared = ("fees.bank", "subscriptions.software", "travel.flights", "transport.fuel", "dining.restaurants",
                  "utilities.internet", "insurance", "taxes", "income.interest")
        self.assertEqual([s for s in shared if s not in hinted], [], "each of these is a hint target")
        self.assertEqual([s for s in shared if s not in business], [])

    @unittest.skipUnless(os.path.exists(ICONS_JS), "needs the frontend checkout")
    def test_every_icon_exists_in_the_frontend_set(self):
        with open(ICONS_JS) as f:
            known = set(re.findall(r"^\s*'([a-z0-9-]+)':", f.read(), re.M))
        used = {icon for taxonomy in seed_categories.TAXONOMIES.values()
                for _s, _n, _k, _c, icon, children in taxonomy for icon in [icon] + [c[2] for c in children]}
        self.assertEqual(used - known, set())


if __name__ == "__main__":
    unittest.main()
