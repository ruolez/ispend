"""Finance data is scoped by profile, not by user: a query that filters finance rows on user_id
shows one profile's rows in another. This guard counts every SQL string in the backend that uses
user_id as a predicate, join key or conflict target and holds each module to the number below.

Modules in PER_USER_MODULES work on users, billing, support or whole accounts by design and are not
counted, nor is SQL that only touches per-person tables (PER_USER_TABLES). Everywhere else the
number may only go down: convert the site to profile_id, then lower the count. A site that really
is per user stays in the count with the reason next to it.
"""
import ast
import os
import re
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PER_USER_MODULES = re.compile(r"^(admin_\w+|privacy|billing\w*|ledger|support\w*|activity|backup/\w+)\.py$")

PER_USER_TABLES = {"users", "profiles", "subscriptions", "auth_tokens", "data_exports", "ai_calls", "settings",
                   "audit_log", "login_events", "support_reports", "user_activity_days"}
TABLE = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(\w+)", re.I)

PREDICATE = re.compile(
    r"(?<!\w)(?:\w+\.)?user_id\s*(?:=|<>|!=|\bIN\b|\bANY\b)"   # t.user_id = %s, user_id IN (…)
    r"|=\s*\w+\.user_id\b"                                      # … = t.user_id (joins)
    r"|ON\s+CONFLICT\s*\([^)]*\buser_id",                       # conflict targets on user keys
    re.I,
)

# Remaining user_id predicates per module. Lower a number when you convert a site; never raise one
# for finance data.
ALLOWED = {
    "auth.py": 3,                 # the session hook's join of the person, subscription and profiles
    "db.py": 3,                   # startup seeding by owner; promote_admin checks the person's data
    "profiles.py": 1,             # the person's profiles, with counts per profile
    "statements_api.py": 2,       # the uploader's name (users join)
    "transactions_api.py": 1,     # who acted on an event (users join)
}


def _modules():
    for dirpath, dirnames, filenames in os.walk(BACKEND):
        dirnames[:] = [d for d in dirnames if d not in ("tests", "__pycache__", "migrations", "bank_profiles")]
        for name in filenames:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                yield os.path.relpath(path, BACKEND).replace(os.sep, "/"), path


def scoping_sites():
    """{module: [matched SQL fragment, …]} for every counted module with at least one site."""
    found = {}
    for rel, path in _modules():
        if PER_USER_MODULES.match(rel):
            continue
        with open(path) as f:
            tree = ast.parse(f.read(), filename=rel)
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                tables = {t.lower() for t in TABLE.findall(node.value)}
                if tables and tables <= PER_USER_TABLES:
                    continue
                for m in PREDICATE.finditer(node.value):
                    start = node.value.rfind("\n", 0, m.start()) + 1
                    end = node.value.find("\n", m.end())
                    hits.append(" ".join(node.value[start:end if end >= 0 else None].split()))
        if hits:
            found[rel] = hits
    return found


class ProfileScopingTest(unittest.TestCase):
    def test_no_module_adds_user_scoped_finance_sql(self):
        over = {rel: hits for rel, hits in scoping_sites().items() if len(hits) > ALLOWED.get(rel, 0)}
        self.assertEqual(over, {}, "finance SQL must filter on profile_id; these modules gained user_id predicates")

    def test_counts_are_tight(self):
        sites = scoping_sites()
        stale = {rel: (n, len(sites.get(rel, []))) for rel, n in ALLOWED.items() if len(sites.get(rel, [])) < n}
        self.assertEqual(stale, {}, "a converted site must lower ALLOWED (module: (allowed, found))")

    def test_detects_predicates_joins_and_conflict_targets(self):
        cases = {
            "SELECT 1 FROM accounts WHERE id = %s AND user_id = %s": 1,
            "JOIN transactions b ON b.user_id = a.user_id": 1,
            "JOIN accounts a ON t.account_id = a.id AND a.profile_id = t.user_id": 1,
            "ON CONFLICT (user_id, merchant_key, direction) DO UPDATE": 1,
            "WHERE t.user_id IN (SELECT 1)": 1,
            "INSERT INTO accounts (user_id, profile_id, name) VALUES (%s, %s, %s)": 0,
            "WHERE profile_id = %s": 0,
        }
        self.assertEqual({sql: len(PREDICATE.findall(sql)) for sql in cases}, cases)


if __name__ == "__main__":
    unittest.main()
