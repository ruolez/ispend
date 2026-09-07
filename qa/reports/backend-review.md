# iSpend backend review (static analysis + code reading + read-only live probes)

Scope: every module under `backend/` (API blueprints, importer, bank_profiles, logic modules, migrations), tests, Dockerfile, compose files, nginx configs, install.sh. Tooling: ruff (`E,F,W,B,UP,S,PL`, then narrowed to `F,B,S6xx,S110,PLW`), pyflakes, targeted curl probes against http://localhost:5559 as `qa_tester` (plus two throw-away `qa_*` users created and deleted via the admin API; admin data untouched). Live evidence is quoted inline; items marked "unverified" were established by reading code only.

---

### [P0] Deactivating a user or changing a role does not take effect until they log in again
- **Area:** security
- **Where:** backend/auth.py:15-34 (`login_required` / `admin_required` only look at the signed session cookie), backend/auth.py:74-81 (only `/api/auth/me` re-checks `is_active`), backend/settings_api.py:151-176 (`update_user`, `delete_user` change the DB row only)
- **What:** Authorization state (`user_id`, `role`) is copied into the client-side session cookie at login and never re-validated against the `users` table for any endpoint except `/me`. A user the admin deactivates (or permanently deletes) keeps a working cookie for `PERMANENT_SESSION_LIFETIME` = 14 days (app.py:19) and can read and write all their data; a user demoted from admin keeps admin endpoints. Verified live: after `PUT /api/users/5 {"is_active": false}` the deactivated user's cookie still returned `200 []` on `GET /api/accounts` and `201 {...,"id":10,"name":"qa deact acct"}` on `POST /api/accounts`; after demotion `role: user`, the old cookie still got `200` from `GET /api/users` (admin-only). Only `GET /api/auth/me` returned 401 — so the UI shell redirects to login while the API stays fully open to anyone who kept the cookie or scripts the API.
- **Repro:** admin: `POST /api/users {"username":"qa_x","password":"...","role":"user"}`; login as qa_x (save cookie); admin: `PUT /api/users/<id> {"is_active": false}`; with qa_x cookie: `GET /api/accounts` → 200.
- **Fix:** In `login_required`/`admin_required`, load `SELECT role, is_active FROM users WHERE id = %s` per request (cheap PK lookup; cache on `g`), clear the session and return 401 when missing/inactive, and take `role` from the DB row instead of the cookie. Alternatively add a `session_version`/`token_generation` column bumped by deactivate/role-change/password-reset and store it in the session.

### [P1] `POST /api/transactions` accepts another user's `category_id` (IDOR) and leaks that user's category names
- **Area:** security
- **Where:** backend/transactions_api.py:434-449 (`category_id = data.get("category_id")` inserted with no ownership check; then `categorizer.learn(uid, key, category_id)`)
- **What:** Every other write path checks `categories WHERE id = %s AND user_id = %s` (update_transaction:466, bulk:581, review resolve:110, put_rows:248), but manual transaction creation does not. The FK only requires the category to exist. Consequences verified live as `qa_tester` using admin's category id 58: the transaction was created with `category_id: 58, category_status: confirmed`, `GET /api/merchants` for qa_tester now contained a memory row pointing at category 58, and `GET /api/transactions/export` rendered `Fees > Bank Fees` — the *other user's* category path — in qa_tester's CSV. Category ids are sequential, so a user can enumerate and read every other user's category names (custom names can be sensitive) via the export/by-category/dashboard joins, and pollute their own memory/rules with foreign ids. Also: a non-integer `category_id` (`"abc"`) here and in `PUT /api/transactions/<id>`, `/bulk`, `/merchants/<key>`, `/statements/<id>/rows` produces a 500 instead of 400 (see the validation finding below).
- **Repro:** as user A: `GET /api/categories?flat=1` (note an id X); as user B: `POST /api/transactions {"account_id":<own>,"txn_date":"2026-09-01","amount":"-5","description":"x","category_id":X}` → 201 with `category_id: X`; `GET /api/transactions/export` shows A's category name.
- **Fix:** Reuse the ownership check (`SELECT id FROM categories WHERE id=%s AND user_id=%s`, 404 otherwise) before the INSERT; consider a DB-level guard (composite FK `(user_id, category_id) REFERENCES categories(user_id, id)` with a unique index on `categories(user_id, id)`) so no future path can cross users. Add an isolation test to `tests/test_transactions_api.py`.

### [P1] No rate limiting or lockout on login; 6-character password floor
- **Area:** security
- **Where:** backend/auth.py:50-65 (`login`), backend/auth.py:104, backend/settings_api.py:129,185 (`len(password) < 6`), nginx/nginx.conf:30-38 (no `limit_req`)
- **What:** Unlimited online password guessing against `POST /api/auth/login`; usernames are enumerable by timing (hash only computed when the user exists, auth.py:58). Verified live: 5 rapid wrong-password attempts all returned 401 with no delay or lockout. Combined with a 6-char minimum this is a realistic brute-force target for an internet-exposed finance app (install.sh exposes ports 80/443).
- **Repro:** `for i in $(seq 50); do curl -d '{"username":"admin","password":"guess$i"}' /api/auth/login; done` — no throttling.
- **Fix:** Add `limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;` + `limit_req zone=login burst=10 nodelay;` on `location = /api/auth/login` in both nginx configs, and/or a small per-username failure counter in Postgres (e.g. `login_attempts(username, failures, locked_until)`) with exponential backoff. Compute `check_password_hash` against a dummy hash when the user is missing. Raise the minimum password length to 10-12.

### [P1] Regex rules are compiled and run unbounded → catastrophic backtracking stalls worker threads (ReDoS)
- **Area:** security
- **Where:** backend/rules.py:41-45 (`re.compile(pattern)` is the only validation), backend/rules.py:78-91, 122-129 (`regex.search(text)` with no timeout/length cap), backend/rules_api.py:14-31 + 144-167 (`/preview` and `/run` scan every transaction synchronously), backend/importer/pipeline.py:147-168, 410-417 (rules also run for every row at preview and commit)
- **What:** Any authenticated user can save a pattern like `(a+)+$` (validation accepted it: `{"count":0,...} 200` from `/api/rules/preview`). Python's `re` is backtracking; measured with the project's own `rules.matches`: 22 chars → 0.2 s, 24 → 0.8 s, 26 → 3.2 s (doubling per character). A description of ~35 chars ending in a non-`a` hangs the request for minutes; the rule then fires on every import/preview/`/run` for that user. gunicorn runs 2 workers × 4 threads (Dockerfile:32-33), so a handful of such requests starve every user. The `--timeout 300` does not abort gthread requests.
- **Repro:** `POST /api/rules {"match_type":"regex","pattern":"(a+)+$","set_excluded":true}` then `POST /api/transactions {"description":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa!", ...}` and `POST /api/rules/run` (do not run against the shared dev instance).
- **Fix:** Cap pattern length (e.g. 200), reject nested quantifiers with a simple linter, and run matches through the `regex` package with `timeout=` (or `re2`). At minimum use a per-rule time budget in `rules_api._scan` loops and disable a rule after it times out.

### [P2] Non-integer / negative query and body values crash with HTTP 500 across many endpoints
- **Area:** api
- **Where:** backend/statements_api.py:69-70 (`int(offset)`, `int(limit)` → also negative `LIMIT -1`), :142-143 (`int(account_id)`, `int(limit)`), :158 (`int(account_id)` on upload), :213-221 (`account_id` string passed to SQL), :246-254 (`category_id` string), :289 (`int(account_id)` on commit); backend/transactions_api.py:305 (`int(data.get("a_id"))`), :318 (`float(min_confidence)`), :332 (`int(limit)` in `/suggest`), :417-418 (`account_id` string in SQL), :464-466, :578-581 (`category_id` string in SQL); backend/categories_api.py:71-74 (`parent_id`), :178-180 (`into`), :219-220 (`reassign_to`); backend/merchants_api.py:37-40 (`category_id`); backend/review_api.py:106-110
- **What:** Values are either passed to `int()` unguarded (ValueError) or handed straight to psycopg2 where `WHERE id = 'abc'` raises `invalid input syntax for type integer`. All bubble to the generic 500 handler (app.py:64-69) with a log line per request. Verified live (all `500 {"error":"Something went wrong on the server..."}`): `GET /api/statements?limit=abc`, `?account_id=abc`, `?limit=-1`; `GET /api/statements/<id>?offset=abc`, `?limit=-1`; `PUT /api/statements/<id>/account {"account_id":"abc"}`; `POST /api/statements/<id>/commit {"account_id":"abc"}`; `PUT /api/statements/<id>/rows {"row_ids":[1],"category_id":"abc"}`; `GET /api/transactions/suggest?q=ab&limit=x`; `POST /api/transactions {"account_id":"abc",...}`; `PUT /api/transactions/<id> {"category_id":"abc"}`; `POST /api/transactions/bulk {"action":"categorize","category_id":"abc"}`; `POST /api/transactions/auto-pair {"min_confidence":"abc"}`; `POST /api/categories {"name":"x","parent_id":"abc"}`; `POST /api/categories/1/merge {"into":"abc"}`; `PUT /api/merchants/FOO {"category_id":"abc"}`. Also `POST /api/transactions/pair {}` returns the raw Python message `int() argument must be a string, a bytes-like object or a real number, not 'NoneType'` (400).
- **Repro:** any of the requests above as a logged-in user.
- **Fix:** Add `util.parse_int(value, name, min=None, max=None)` that raises a `ValueError` with a user-facing message, and a small `@bp.errorhandler(ValueError)` per blueprint (or one in app.py) mapping to 400; apply it to every id/limit/offset read from `request.args`/JSON. Clamp `limit`/`offset` to `[0, MAX]` as `list_transactions` already does (transactions_api.py:199-202).

### [P2] Fuzzy merchant memory treats any token subset as a 100% match (Uber → Uber Eats, Amazon → Amazon Prime, Shell → Shell Oil)
- **Area:** backend
- **Where:** backend/categorizer.py:13, 93-100 (`process.extractOne(key, ctx.memory_keys, scorer=fuzz.token_set_ratio, score_cutoff=92)`)
- **What:** `token_set_ratio` returns 100 whenever one key's tokens are a subset of the other's. Verified inside the backend container with the pinned rapidfuzz 3.14.6: `token_set_ratio("UBER","UBER EATS")`, `("AMAZON","AMAZON PRIME")`, `("SHELL","SHELL OIL")` are all `100.0`. The alias table in merchant.py deliberately produces distinct keys `UBER` / `UBER EATS`, `AMAZON` / `AMAZON PRIME`, so after the user remembers "Uber Eats → Dining", every Uber ride is imported as *suggested* Dining (confidence 0.8, `status='suggested'`), inflating the Review queue and, once accepted in bulk, mis-categorizing spend. Same for any single-word merchant vs. a remembered multi-word one.
- **Repro:** `PUT /api/merchants/UBER%20EATS {"category_id": <dining>}` then create/import a transaction with description `UBER *TRIP` → `category_status: suggested`, `category_source: merchant`, category = dining.
- **Fix:** Use `fuzz.WRatio`/`fuzz.ratio` (or `token_sort_ratio`) with the 92 cutoff, or require `len(best_key.split()) == len(key.split())` / a shared-prefix check before accepting a subset match; never fuzzy-match keys of ≤ 1 token. Add a regression test in `tests/test_categorizer.py`.

### [P2] CSV exports do not neutralize spreadsheet formulas (CSV injection)
- **Area:** security
- **Where:** backend/transactions_api.py:271-287 (`export_csv` writes `description_raw`, `merchant_name`, `notes` verbatim), backend/reports_api.py:27-36 (`_csv_response` writes merchant/category names verbatim)
- **What:** Cells beginning with `=`, `+`, `-`, `@` (and tab/CR) are executed as formulas by Excel/LibreOffice/Google Sheets on open. Descriptions originate from bank statements (payment memos, Zelle/e-Transfer notes, merchant descriptors) and from other users' free text, so an external party can plant them. Verified live: a transaction with description `=HYPERLINK("http://evil","click")` exported as `"=HYPERLINK(""http://evil"",""click"")"` — the leading `=` is intact, Excel will evaluate it. Note that `-` is a legitimate leading character for amounts; only text columns need escaping.
- **Repro:** `POST /api/transactions {... "description":"=HYPERLINK(\"http://evil\",\"click\")"}`; `GET /api/transactions/export`.
- **Fix:** In both writers, prefix text cells whose first character is one of `= + - @ \t \r` with a single quote (`'`) or a space, and strip control characters. Add a test asserting the escaped output.

### [P2] Pairing a transfer does not check whether either side is already paired → dangling one-way pairs
- **Area:** backend
- **Where:** backend/transfers.py:49-81 (`pair` never reads `transfer_pair_id` of `a`/`b` before overwriting), backend/transactions_api.py:300-311
- **What:** If A↔B is paired and the user (or `auto_pair`, which only filters `transfer_pair_id IS NULL` on the *candidate* query) pairs A↔C, B keeps `transfer_pair_id = A` and `is_transfer = TRUE`, but A now points at C. Later `unpair(A)` clears A and C only; B stays flagged as a transfer forever (hidden from spending) with a pointer to a row that is not paired with it. `_release_partners` (transactions_api.py:519-531) and `discard_statement` (pipeline.py:513-522) rely on `transfer_pair_id` symmetry. Unverified live.
- **Repro:** pair (a,b); pair (a,c) with amounts matching; `GET /api/transactions/<b>` → `transfer_pair_id: a`, `is_transfer: true`; `POST /api/transactions/<a>/unpair` → b unchanged.
- **Fix:** In `pair`, reject (409) when `a["transfer_pair_id"]` or `b["transfer_pair_id"]` is set (or explicitly unpair the old partner first inside the same transaction). Add a DB CHECK/trigger enforcing symmetry, or at least a test.

### [P2] Changing a category's `kind` to/from `transfer` leaves existing transactions' `is_transfer`/`is_excluded` stale
- **Area:** backend
- **Where:** backend/categories_api.py:149-156 (`UPDATE categories SET ... kind=%s`), backend/migrations/002_transfer_kind_trigger.sql:29-32 (trigger fires only `ON INSERT OR UPDATE OF category_id ON transactions`)
- **What:** The invariant "category kind = transfer ⇒ is_transfer/is_excluded" is enforced only when a transaction's `category_id` changes. Re-kinding a category (expense → transfer or back) via `PUT /api/categories/<id>` changes reports (`flow_where` filters on `is_transfer`, reports.py:19-23) for future rows only; existing rows keep the old flags, so "Transfers" totals and spending totals disagree until each row is touched. Similar drift: `transfers.unpair` (transfers.py:90-94) and bulk `unset_transfer` (transactions_api.py:633-637) set `is_transfer = FALSE` even when the row is filed under a transfer-kind category. Unverified live.
- **Repro:** file a transaction under "Subscriptions"; `PUT /api/categories/<subscriptions id> {"kind":"transfer"}`; `GET /api/transactions/<id>` → `is_transfer: false`.
- **Fix:** After the kind update run `UPDATE transactions SET is_transfer = (kind='transfer'), is_excluded = ... WHERE category_id IN (cat + children) AND transfer_pair_id IS NULL AND user_id = %s`, and have `unpair`/`unset_transfer` reuse the `_release_partners` logic that consults the category kind.

### [P2] Permanently deleting a user leaves their per-user settings (including the OpenRouter API key) in the `settings` table
- **Area:** security
- **Where:** backend/settings_api.py:170-173 (`DELETE FROM users` + `rmtree` only), backend/db.py:88-94 (`u<id>:<key>` rows in the global `settings` table, no FK)
- **What:** Per-user settings are emulated with prefixed keys in a table that has no `user_id` column, so `ON DELETE CASCADE` cannot clean them. Deleting user N orphans `uN:openrouter_api_key` (plaintext secret), `uN:openrouter_model`, flags. A future user cannot receive id N (SERIAL) so no cross-user leak today, but the secret is retained indefinitely and shows up in backups. Read-only DB check confirms the pattern is in use (`u2:openrouter_api_key` holds a live `sk-or-v1-…` key in plaintext).
- **Repro:** as admin set a key for user N, `DELETE /api/users/N?permanent=true`, `SELECT key FROM settings WHERE key LIKE 'uN:%'`.
- **Fix:** Move per-user settings to a `user_settings(user_id FK ON DELETE CASCADE, key, value)` table (one migration + rewrite `db.user_setting`/`set_user_setting`), or at minimum `DELETE FROM settings WHERE key LIKE %s` with `f"u{user_id}:%"` in `delete_user`. Consider encrypting API keys at rest with a key derived from `SECRET_KEY`.

### [P2] `renormalize` computes cardholder phrases per account while imports compute them per statement → fingerprints drift and re-imports stop de-duplicating
- **Area:** backend
- **Where:** backend/maintenance.py:20-27, 46-52 (phrases from all descriptions of the account, fingerprint recomputed from the new `m.clean`), backend/importer/pipeline.py:195-197, 383-388 (phrases from the statement's rows / `stats.stripped_phrases`), backend/dedupe.py:7-10 (fingerprint includes `description_clean`)
- **What:** `merchant.frequent_phrases` (merchant.py:171-203) depends on the document set (≥15 rows, ≥35% share). A phrase stripped at account level (e.g. one card's cardholder name across many statements) may not be stripped for a single statement — and vice versa — so `description_clean` and therefore `fingerprint` differ between the stored row (after `POST /api/merchants/renormalize`) and a re-uploaded copy of the same statement. `dedupe.find_existing` then reports 0 duplicates and the import inserts every row twice (the UNIQUE constraint does not help because the fingerprints differ). Unverified live; follows from the two code paths using different inputs.
- **Repro:** import a Bilt-style statement whose rows carry the cardholder name; run renormalize; re-upload the same file → preview shows no "already imported" duplicates.
- **Fix:** Make the fingerprint independent of phrase stripping (hash `account_id|date|amount|clean_description(raw)` without phrases, or store the statement-level phrases per transaction and reuse them in renormalize), or recompute per-statement phrases in `renormalize_user` grouped by `statement_id`.

### [P2] No browser security headers on the API or the static frontend
- **Area:** security
- **Where:** nginx/nginx.conf:1-44, nginx/nginx.ssl.conf.template:17-69 (only HSTS `max-age=31536000` without `includeSubDomains`/preload), backend/app.py:49-54 (`after_request` sets only cache headers)
- **What:** Verified live: `curl -D - http://localhost:5559/` and `/api/auth/me` return no `X-Frame-Options`/`frame-ancestors`, no `Content-Security-Policy`, no `X-Content-Type-Options: nosniff`, no `Referrer-Policy`, no `Permissions-Policy`. The app is a finance dashboard with session cookies; clickjacking and any future XSS (e.g. the unvalidated account `color` field accepted `<script>x</script>` — accounts_api.py:42 — which only the frontend's `esc()` keeps safe) have no defence in depth.
- **Repro:** `curl -sD - -o /dev/null http://localhost:5559/ | grep -i x-frame` → nothing.
- **Fix:** In both nginx server blocks: `add_header X-Frame-Options "DENY" always; add_header X-Content-Type-Options "nosniff" always; add_header Referrer-Policy "same-origin" always; add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'" always;` (adjust for Chart.js/inline styles), and `Strict-Transport-Security "max-age=31536000; includeSubDomains"` on the SSL block. Note nginx `add_header` in a `location` block drops the server-level ones — repeat them in `/fonts/`, `/vendor/` or move to a shared include.

### [P2] AI statement extraction is synchronous and can outlive the nginx/gunicorn request budget
- **Area:** api
- **Where:** backend/statements_api.py:351-369 (`ai_extract` runs `importer.ai_extract_statement` inline), backend/ai_extract.py:44-58 (one OpenRouter call per page, `timeout=120` each, no page cap), nginx/nginx.conf:36 (`proxy_read_timeout 300s`), backend/Dockerfile:34 (`--timeout 300`)
- **What:** A 10-page scanned statement can take 10 × (up to 120 s) = 20 min; nginx returns 504 after 300 s while the worker keeps calling the model and finally `_stage`s the rows (pipeline.py:597), so the user sees an error, retries, and pays for the pages twice. Every other long operation (parse, OCR, re-parse of PDFs) correctly uses `jobs.spawn` + status polling. Unverified live (no OpenRouter key on the QA user).
- **Repro:** configure a slow model, upload a multi-page PDF, `POST /api/statements/<id>/ai-extract`.
- **Fix:** Run `ai_extract_statement` through `jobs.spawn`, set `status='parsing'` and let the UI poll like `reparse_async`; cap pages per call (e.g. 25) and add a per-user daily token/call budget (see cost-control item).

### [P2] Local `python3 -m unittest discover -s tests` fails on 25 modules — root cause is missing third-party packages, not `sys.path`
- **Area:** tests
- **Where:** backend/tests/_stubs.py:62-82 (only stubs `db` and `config`; Flask/dateutil/rapidfuzz are real imports), backend/tests/*.py (`sys.path.insert(0, os.path.dirname(__file__)); import _stubs`), backend/requirements.txt (no dev requirements file), README
- **What:** Running with the host `python3` or the QA venv gives `ModuleNotFoundError: No module named 'flask'` (15 modules), `'dateutil'` (8), `'rapidfuzz'` (2) — unittest reports each as `ImportError: Failed to import test module`, which is what was misread as an "`importer` ImportError". The path plumbing is fine: 97 tests that need no third-party code already pass. Verified fix: `qa-venv/bin/pip install flask werkzeug python-dateutil rapidfuzz openpyxl xlrd pdfplumber requests` then `cd backend && python -m unittest discover -s tests` → `Ran 289 tests … OK (skipped=1)` (the OCR e2e is skipped without tesseract). `-t .` currently fails (`Start directory is not importable`) because `tests/` has no `__init__.py`; with an empty `tests/__init__.py` the `-t .` form also passes (verified in a scratch copy) and would let `_stubs` be imported under one name instead of the duck-typed `_stubs`/`tests._stubs` dual identity the file comments on (lines 69-70).
- **Repro:** `cd backend && python3 -m unittest discover -s tests` without the app's dependencies installed.
- **Fix:** Add `backend/requirements-dev.txt` (or document `pip install -r backend/requirements.txt` into a venv) and a one-line `make test`; add `backend/tests/__init__.py` and standardize on `python -m unittest discover -s tests -t .` so `sys.path` hacks can go; consider a `conftest.py` if pytest is adopted.

### [P2] Known failing `test_pdf_ocr.OcrEndToEndTest` is a test-fixture bug, not a product bug
- **Area:** tests
- **Where:** backend/tests/test_pdf_ocr.py:29-44 (single 27-character line rendered with PIL's default ~11 px font), backend/importer/pdf_ocr.py:8, 15-26 (`MIN_CHARS_PER_PAGE = 40`; `image_only_pages` also flags pages with `< 40` chars and an image ≥ 30 % of the page)
- **What:** Reproduced inside the container: after OCR the output PDF has `char_count = (27, 1)` and `image_only_pages = [1]` (the scan image still covers 100 % of the page), so `needs_ocr(dst)` is legitimately `True` — 27 chars is below the 40-chars-per-page floor the product uses to distinguish scanned pages. OCR itself worked (`page_text` → `08/02/2024 COFFEE SHOP 450`; the tiny default font also cost the decimal point). Real statements have hundreds of characters per page, so the product heuristic is fine; the fixture is too sparse. Note the pipeline is not affected either way: once `ocr_path` is stored it is reused without re-checking `needs_ocr` (pipeline.py:134-139).
- **Repro:** `docker compose exec -T backend python -m unittest tests.test_pdf_ocr.OcrEndToEndTest` → `AssertionError: True is not false` at line 42.
- **Fix:** Render ≥ 3 lines with a larger font (`ImageFont.load_default(size=32)` in Pillow ≥ 10.1) so the page exceeds 40 chars (e.g. a header, two transaction lines and a totals line), keep `assertIn("COFFEE", …)`, and replace `assertFalse(needs_ocr(dst))` with `assertGreater(pdf_text.char_count(dst)[0], 0)` or keep it once the text is long enough. Also assert `"4.50" in text` to catch the decimal-point loss.

### [P2] Test-suite gaps: authentication, admin user management and cross-user isolation are untested
- **Area:** tests
- **Where:** backend/tests/ (no `test_auth.py`, `test_merchants_api.py`, `test_ai_api.py`, `test_jobs.py`, `test_db.py`); backend/tests/test_settings_api.py covers settings but not `is_active`/role enforcement; no test asserts a 404 for another user's ids
- **What:** 289 tests cover parsers, reports and most blueprints well, but the security-critical surface has none: login/logout/`/me`, `login_required`/`admin_required` (the P0 above would have been caught by "deactivated user gets 401"), password change/reset validation, `PUT /me/preferences` whitelist, per-user data isolation for every `<int:id>` route, `merchants_api` (learn/forget/apply_existing/rename_all), `ai_api` (`/ai/categorize` batching threshold, `/insights` period handling), `jobs.spawn` app-context/connection cleanup, `db.transaction` rollback, `statements` upload validation (413, unsupported type, path traversal in filename), CSV export escaping, pagination cursor round-trip. Tests use a FakeDB, so SQL is never executed — SQL typos, the `import_rows` FK behaviour, and the migration trigger are only exercised in the live container.
- **Repro:** `grep -l "is_active\|admin_required" backend/tests/*.py` → only settings/accounts stubs.
- **Fix:** Add `test_auth.py` with a Flask test client (401/403/deactivated/demoted cases), an isolation test helper that hits every `<int:id>` route with a foreign id, and a small opt-in integration suite (`ISPEND_TEST_DSN`) that runs migrations on a scratch database and exercises the trigger, `ON CONFLICT` dedupe and `discard_statement`.

### [P2] Docker/compose hardening gaps
- **Area:** security
- **Where:** backend/Dockerfile:12-49 (no `USER`; `COPY . .` with no `.dockerignore`), docker-compose.yml:13-59 (no `healthcheck` for backend or nginx; `depends_on: - backend` without a condition), install.sh:130-141 (`mkdir -p "$BACKUP_DIR"` with default umask; dumps + `.env` copies world-readable)
- **What:** The backend (which shells out to ocrmypdf/ghostscript/tesseract on user-supplied PDFs, historically a CVE-rich toolchain) runs as root inside the container; a Ghostscript escape would be root in the container. `COPY . .` bakes `tests/`, `__pycache__/` and any local `.env`/`.tmp` into the image. `/api/health` exists (app.py:71-73) but compose never uses it, so `restart: unless-stopped` will not restart a wedged-but-running gunicorn and nginx can start before the backend listens (first requests 502). Backups under `/opt/ispend-backups` contain the full financial DB dump and a copy of `.env` (SECRET_KEY, Postgres password, admin password) with 0644 permissions.
- **Repro:** `docker compose exec backend id` → `uid=0`; `ls -l /opt/ispend-backups` on a production host.
- **Fix:** Add `RUN useradd -r -u 10001 app && chown -R app /app /data` + `USER app` (ensure the `statements` volume is writable); add `backend/.dockerignore` (`tests/`, `__pycache__/`, `*.pyc`, `.env`); add a backend `healthcheck: test: ["CMD-SHELL","python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:5000/api/health')\""]` and `depends_on: backend: condition: service_healthy` for nginx; `install -d -m 700 "$BACKUP_DIR"` and `chmod 600` on each dump/env copy (`umask 077` at the top of `backup_data`).

### [P3] Missing cost controls on AI calls
- **Area:** backend
- **Where:** backend/ai_api.py:29-47 (`scope=uncategorized` → every uncategorized row, unbounded batches in a background thread), backend/ai_extract.py:44-51 (one call per page, 12 000 chars each, `max_tokens=8000`, no page cap), backend/insights.py:85-112 (`force=true` regenerates at will)
- **What:** A user with 20 000 uncategorized rows triggers 500 model calls with one click; a 60-page PDF = 60 calls. `ai_calls` records usage but nothing enforces a ceiling. Also the "tokens today" figure in `/api/ai/status` uses `date_trunc('day', now())` in DB time (UTC), not `APP_TIMEZONE` (ai_api.py:64).
- **Repro:** `POST /api/ai/categorize {"scope":"uncategorized"}` on a large account.
- **Fix:** Add `AI_MAX_ITEMS_PER_RUN` / `AI_MAX_CALLS_PER_DAY` settings checked in `ai_api.categorize`, `ai_extract.extract_rows` and `insights.generate`; return 429 with the remaining budget; compute "today" with `timezone(APP_TIMEZONE, now())`.

### [P3] `LIKE`/`ILIKE` wildcards in user search strings are not escaped
- **Area:** api
- **Where:** backend/transactions_api.py:115-119 (`q`), :333 (`/suggest`), backend/merchants_api.py:25-27
- **What:** `%` and `_` in the search string act as wildcards (`q=%` matches everything — verified 200; `q=100%` matches "100 dollars", `_` matches any char). Harmless for security (parameterized) but yields wrong results and a full-table ILIKE scan per keystroke.
- **Repro:** `GET /api/transactions?q=%25`.
- **Fix:** `like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"` with `ILIKE %s ESCAPE '\'`, or switch to `position(lower(%s) in lower(...)) > 0`; consider a `pg_trgm` GIN index on `description_clean`/`merchant_name`.

### [P3] Rule created from the Review screen does not verify `account_id` ownership
- **Area:** security
- **Where:** backend/review_api.py:139-162 (`rules_mod.validate(...)` only; `rules_api._validate_refs` is not called)
- **What:** `create_rule.account_id` can reference another user's account. The rule only ever matches the caller's own rows (so no data exposure), but it silently never fires and the FK `ON DELETE CASCADE` (001_init.sql:68) lets another user's account deletion delete this user's rule.
- **Repro:** `POST /api/review/resolve {"ids":[…],"category_id":…,"create_rule":{"pattern":"x","account_id":<foreign>}}` → 200.
- **Fix:** Import and call `rules_api._validate_refs(clean, uid)` (move it to `rules.py`/a shared helper).

### [P3] Multi-statement writes without a transaction leave partial state on failure
- **Area:** backend
- **Where:** backend/accounts_api.py:123-128 (per-statement discard, then account delete), backend/review_api.py:115-164 (update + events + learn + rule insert + rule-id backfill), backend/rules_api.py:56-78 (`apply_rules`: per-rule UPDATE, hit_count, events), backend/statements_api.py:340-346 (`flip_signs` then mapping update), backend/transactions_api.py:435-449 (insert + events + learn), :513-514 (`_release_partners` then DELETE), backend/categories_api.py:149-156, backend/accounts_api.py:102-108 (account update + currency propagation)
- **What:** `db.query/execute` default to `commit=True`, so each statement is its own transaction unless the caller uses `db.transaction()` and passes `commit=False` everywhere (as `merge`, `pair`, `flip_signs`, `_stage`, `_commit_locked` do). A failure mid-way (e.g. the 500s above, a FK violation, a DB hiccup) leaves e.g. a transaction with no `imported` event, a rule inserted but rows not tagged, or partners released but the row not deleted.
- **Repro:** code reading; e.g. kill the connection between lines 435 and 445 of transactions_api.py.
- **Fix:** Wrap each handler's writes in `with db.transaction():` and pass `commit=False`; consider flipping the default to `commit=False` with an explicit commit in an `after_request`/teardown so forgetting is safe rather than lossy.

### [P3] Admin user endpoints accept unknown ids and unvalidated payloads
- **Area:** api
- **Where:** backend/settings_api.py:180-189 (`reset_password` never checks the user exists → `{"ok":true}` for `/api/users/999999/password`), :165-177 (`delete_user` likewise), :46-69 (`put_settings` stores any string for `openrouter_model`/flags, no length cap), backend/auth.py:84-92 (`update_preferences` stores arbitrary JSON values, bounded only by the 25 MB `MAX_CONTENT_LENGTH`), backend/accounts_api.py:42 (`color` not restricted to `c1..c12` like categories are — verified `<script>x</script>` stored and echoed back)
- **What:** Silent no-ops mislead the admin UI; unbounded preference/setting values can bloat `users.preferences`/`settings`; `color` outside the palette renders as an unknown slot.
- **Repro:** `PUT /api/users/999999/password {"password":"abcdefg"}` → 200 `{"ok":true}`.
- **Fix:** Check `rowcount`/existence and return 404; validate `color in COLOR_SLOTS`, `theme in ("system","light","dark")`, `density`, `currency in CURRENCIES`, integer `default_account_id` owned by the user; cap string settings at ~200 chars.

### [P3] Session handling nits: no session rotation on login, 14-day lifetime, role/username duplicated in cookie
- **Area:** security
- **Where:** backend/auth.py:60-63 (`session["user_id"] = …` without `session.clear()`), backend/app.py:19 (`PERMANENT_SESSION_LIFETIME = 14 days`), backend/app.py:17 (`SameSite=Lax` — good; there is no CSRF token, so Lax is the only CSRF defence)
- **What:** Logging in as a second user in the same browser merges leftover keys; a stolen cookie is valid for two weeks with no server-side revocation (see P0). `SameSite=Lax` blocks cross-site POST CSRF in modern browsers but not same-site subdomain attacks; acceptable for a self-hosted app if documented.
- **Repro:** code reading.
- **Fix:** `session.clear()` before populating; consider 24 h lifetime with sliding refresh; if the P0 fix adds a server-side check, revocation comes for free.

### [P3] User-visible error strings expose internals
- **Area:** api
- **Where:** backend/importer/pipeline.py:82-84 (`_set_error(f"{e}")` stores any exception text into `statements.error_message`, shown in the UI), backend/transactions_api.py:305-307 (raw `TypeError` text), backend/openrouter.py:146-147 (`resp.text[:200]` from a third party surfaced verbatim)
- **What:** Parse failures can echo `FileNotFoundError: [Errno 2] No such file or directory: '/data/statements/4/<sha>.pdf'`, pdfplumber/openpyxl tracebacks or ocrmypdf stderr (pdf_ocr.py:49-50) to the browser. Verified live for the pair endpoint (`int() argument must be a string…`).
- **Repro:** `POST /api/transactions/pair {}`.
- **Fix:** Map known exception types to short messages ("The file could not be read", "OCR failed"), log the full text server-side with the statement id, and validate `a_id`/`b_id` before `int()`.

### [P3] Unbounded background work per upload and unbounded table growth
- **Area:** perf
- **Where:** backend/jobs.py:17-31 (one non-daemon thread per upload, no per-user/global cap), backend/statements_api.py:150-170, backend/util.py:14-20 (`audit_log` insert on nearly every request; 660 rows already for ~1 300 transactions), backend/migrations/001_init.sql:210-223 (`ai_calls` has no `(user_id, created_at)` index although `ai_api.status` filters by both), 236-243 (`audit_log` never pruned)
- **What:** A user (or script) uploading 200 PDFs spawns 200 parser threads in a 4-thread worker; only OCR is serialized (`pdf_ocr.OCR_LOCK`). `audit_log`/`ai_calls`/`transaction_events` grow forever with no retention job; `list_transactions` runs five full-filter scans per page (transactions_api.py:204-233) which is fine at 1 k rows but O(5·N) at 100 k.
- **Repro:** code reading.
- **Fix:** A `ThreadPoolExecutor(max_workers=2)` in `jobs.py` (or a `parsing` count check per user → 429), an `ai_calls (user_id, created_at DESC)` index, a nightly `DELETE FROM audit_log WHERE created_at < now() - interval '180 days'`, and compute the facet counts in one query with `GROUPING SETS` or lazily.

### [P3] Timezone and date nits
- **Area:** backend
- **Where:** backend/ai_api.py:62-66 (`date_trunc('day', now())` = UTC day), backend/importer/dates.py:57, 66 (`date.today()` — container local time, UTC — used for year inference of MM/DD rows around New Year), backend/reports.py:47-48 / transactions_api.py:37-38 (correctly use `APP_TIMEZONE`)
- **What:** "Calls today" flips at 7 pm Chicago time; a statement parsed on 31 Dec 23:30 CST (already 1 Jan UTC) can infer the wrong year for MM/DD-only rows when no period is found (`infer_year` fallback, dates.py:142-146).
- **Repro:** code reading.
- **Fix:** Pass `today()` from `reports`/`transactions_api` into `parse_date(default_year=…)`/`infer_year(today=…)` (the parameters already exist), and use `timezone(%s, now())` in the ai status query.

### [P3] `transfers.candidates` pairs across currencies and ignores account activity
- **Area:** backend
- **Where:** backend/transfers.py:16-30 (`b.amount = -a.amount` only), :40-46
- **What:** A −100.00 USD debit and a +100.00 CAD credit within 4 days are offered (and auto-paired at 0.9 confidence when either description contains PAYMENT/TRANSFER) as one transfer. Unverified live.
- **Repro:** two accounts with different currencies and offsetting amounts.
- **Fix:** Add `AND b.currency = a.currency` to the join (and to `pair`).

### [P3] Monthly insights cache is never invalidated by new data
- **Area:** backend
- **Where:** backend/insights.py:85-89 (`cached()` hit wins unless `force`), :104-110 (`UNIQUE (user_id, period_start, period_end)`)
- **What:** Insights generated on the 3rd of the month are served unchanged for the rest of the month even after importing three more statements; the user must know to press "regenerate". 
- **Repro:** generate, import, `GET /api/insights` → `cached: true`, stale numbers.
- **Fix:** Store `txn_count`/`MAX(updated_at)` of the period in the row and treat the cache as stale when they change (or expire after 24 h within the current month).

### [P3] `rules.validate` coerces booleans/floats into ids and strings into `True`
- **Area:** api
- **Where:** backend/rules.py:19-25 (`int(True)` → 1, `int(1.9)` → 1), :74 (`bool(payload.get("is_active", True))` → `"false"` is truthy), :56 (name truncated to 200 but `pattern` unbounded)
- **What:** `{"category_id": true}` becomes category 1 (the ownership check then rejects it, but only by luck); `{"is_active":"false"}` activates the rule; a multi-megabyte pattern is stored and compiled on every categorization.
- **Repro:** `POST /api/rules {"pattern":"x","category_id":true}`.
- **Fix:** Reject `bool`/non-integral floats in `_int_or_none`, parse booleans explicitly, cap `pattern` (200) and `name`.

### [P3] Lint findings worth fixing (ruff/pyflakes; style noise omitted)
- **Area:** backend
- **Where:** backend/reports.py:12 (`row_json` unused), backend/importer/generic.py:3 (`date` unused), backend/importer/pipeline.py:6 (`Decimal` unused), backend/bank_profiles/__init__.py:4 (`BankProfile`, `CsvFormat` re-exported without `__all__`), backend/maintenance.py:34 (loop var `r` unused), backend/config.py:14-20 (`int` defaults passed to `os.environ.get`, PLW1508 — works but type-confusing), backend/openrouter.py:75,134,138,152,164,168 / backend/rules.py:16,25,45,60 / backend/importer/pipeline.py:41 / backend/importer/pdf_ocr.py:45,47 (`raise … from e` missing — loses the original traceback in logs), backend/importer/pdf_text.py:13-16,102-105 and pipeline.py:551-555 (`try/except: pass` swallow without logging), backend/importer/pdf_ocr.py:43 (`subprocess.run` without `check=`; return code is handled manually — fine, but `capture_output` buffers unbounded ocrmypdf output)
- **What:** No undefined names, mutable defaults or bare excepts were found; the 26 `S608` "SQL injection" hits are all f-string composition of whitelisted fragments (`SORTS`, `ITEM_FIELDS`, `QUEUE_WHERE`, `_amt(flow)`) with parameterized values — reviewed and safe. ruff totals: 284 findings with the broad selection, 69 with the narrowed one; the rest is PLR magic numbers/complexity.
- **Repro:** `ruff check backend/ --select F,B,S110,S603,PLW --ignore E501`; `pyflakes backend/`.
- **Fix:** Apply `ruff --fix` for F401, add `__all__` in `bank_profiles/__init__.py`, chain exceptions with `from e`, log at debug level in the swallowed excepts, and adopt a `ruff.toml` with `select = ["E","F","B","S","PLW"]`, `ignore = ["E501","S101","S608"]` in CI.

---

## Summary

Counts: **P0: 1 · P1: 3 · P2: 13 · P3: 13** (30 findings).

Top 5:
1. **[P0] Deactivation/role changes are not enforced** — `login_required`/`admin_required` trust the cookie; a deactivated or demoted user keeps full (even admin) API access for 14 days. Verified live. Fix: re-check `users.is_active`/`role` per request (auth.py).
2. **[P1] IDOR in `POST /api/transactions`** — a foreign `category_id` is accepted, learned into merchant memory and leaks the other user's category names through exports/reports. Verified live. Fix: ownership check + composite FK.
3. **[P1] No login rate limiting** (plus 6-char passwords, timing-based username enumeration) on an internet-exposed finance app. Fix: nginx `limit_req` on `/api/auth/login` + per-user backoff.
4. **[P1] ReDoS via regex rules** — `(a+)+$` accepted; matching time doubles per character (26 chars → 3.2 s) and rules run on every import/preview/run, starving the 2×4 gunicorn threads. Fix: pattern caps + `regex` with timeout.
5. **[P2] Fuzzy merchant memory subset bug** — `token_set_ratio` scores `UBER`/`UBER EATS`, `AMAZON`/`AMAZON PRIME` as 100, so remembered multi-word merchants are suggested for their single-word namesakes on every import (verified with the pinned rapidfuzz). Fix: use `ratio`/`WRatio` or require equal token counts.

Also notable: ~15 endpoints return 500 on non-integer/negative ids and limits (verified), CSV exports are formula-injectable (verified), no security headers anywhere (verified), per-user API keys survive user deletion, transfer pairing can leave dangling pairs, category re-kinding desynchronizes `is_transfer`. Test-suite diagnosis: the local failure is missing `flask`/`dateutil`/`rapidfuzz` (289/289 pass after `pip install` — add a dev requirements file and `tests/__init__.py`), and the OCR e2e failure is a fixture bug (27-char page below the 40-chars/page `needs_ocr` floor), not a product defect.
