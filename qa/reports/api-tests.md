# iSpend — black-box API test suite (qa/e2e/test_api.py)

## How to run

```bash
cd /Users/ruolez/Desktop/Dev/ispend
VENV=/private/tmp/claude-501/-Users-ruolez-Desktop-Dev-ispend/272cd059-cdbc-40ed-a58b-b1f0ef765b8f/scratchpad/qa-venv
$VENV/bin/pytest qa/e2e/test_api.py -v            # full run (~35 s, 373 tests)
$VENV/bin/pytest qa/e2e/test_api.py -v -x         # stop at first failure
$VENV/bin/pytest qa/e2e/test_api.py -k "TestReports or TestIsolation"
```

- Target: `http://localhost:5559` (override with `ISPEND_BASE_URL`). The dev stack must already be running.
- Files: `qa/e2e/conftest.py` (sessions, user lifecycle, upload/poll/commit helpers) and `qa/e2e/test_api.py` (tests).
- Users: the suite logs in as `admin` (read-only use + user management) and creates `qa_api1` / `qa_api2`
  (`qa-api1-pass` / `qa-api2-pass`). A temporary `qa_api_inactive` is created and removed inside one test.
  At session end both qa_api users are **deleted permanently** (`DELETE /api/users/<id>?permanent=true`), which
  cascades to every account/statement/transaction/category/rule they own and removes their upload directory.
  Set `QA_KEEP_USERS=1` to keep them for inspection. A leftover `qa_api*` user from an interrupted run is
  recreated from scratch on the next run, so the suite is deterministic.
- admin's own data was verified unchanged after the runs (5 accounts / 571 transactions before and after; shared
  settings still empty). `qa_tester` was only used for three manual `curl` probes (statement uploaded and deleted).
- Expected report numbers are computed by hand from the fixture CSVs and a hand-made dataset (`manual` fixture:
  9 categorized transactions + 2 transfer legs in Jun–Sep of the current year), never from the API itself.
- Logs of the final runs: `qa/reports/api-run-x.log` (with `-x`), `qa/reports/api-run-full.log` (full, `-rA`).

## Result table (final run: 320 passed, 53 failed — every failure is a product defect, see findings)

| Area (test class) | Tests | Pass | Fail | Notes |
|---|---|---|---|---|
| TestAuth (login/me/logout, 401 on 34 GET + 53 mutating routes, 403 for non-admin, admin self-protection, inactive user, password rules, preferences) | 107 | 107 | 0 | |
| TestIsolation (IDOR: 26 foreign-id routes, bulk/review/pair with foreign ids, foreign refs, unscoped writes, list leaks) | 31 | 31 | 0 | Every foreign object → 404, nothing modified or leaked |
| TestImportPipeline (23 fixtures round-trip, re-upload dedupe, delete semantics, auto-flip, flip-signs, mapping edit, relabel/reparse, row include/category, bad files, 413, unicode/traversal names, paging) | 40 | 39 | 1 | F-08 |
| TestTransactions (create/validate, every list filter, presets, limit/cursor extremes, totals vs summary, events, PUT, bulk, delete, export, suggest, rule-draft, pair/unpair/auto-pair) | 18 | 17 | 1 | F-01 |
| TestCategories (tree, CRUD, nesting, dup names, merge, delete w/ refs, system delete, reset) | 5 | 4 | 1 | F-09 |
| TestRules (rule drives commit categorization, 5 match types, validation incl. bad regex → 400, apply/update/reorder/run/delete) | 8 | 8 | 0 | |
| TestReview (count, grouped queue, resolve + create_rule, mark transfer, learn/renormalize, AI 502 paths, merchant memory) | 3 | 3 | 0 | |
| TestAccounts | 3 | 3 | 0 | |
| TestReports (summary, fixture-vs-CSV, by-category, monthly, trends, top-merchants, MoM, recurring/dismiss, dashboard, insights/anomalies, empty user, lenient/invalid params) | 27 | 27 | 0 | All hand-computed numbers match |
| TestSettings (secret masking, mask round-trip, client shape, admin view) | 2 | 2 | 0 | |
| TestRobustness (non-object JSON bodies, malformed JSON / wrong content-type, nulls in every field, type confusion, huge/SQL-ish/unicode strings, upload form garbage) | 129 | 79 | 50 | F-02 … F-07 |
| **Total** | **373** | **320** | **53** | |

Failing tests → findings: `test_export_csv_escapes_formula_cells` → F-01; `test_non_object_json_body_never_500[*]` (29) →
F-02; `test_type_confusion_never_500[*]` (18) → F-03/F-04/F-05/F-06; `test_nulls_in_every_field_never_500[POST|PUT /api/accounts]`
+ `test_upload_field_name_and_form_garbage` → F-07/F-04; `test_commit_reports_known_duplicates_as_skipped` → F-08;
`test_kind_mismatch_child_under_income_parent` → F-09.

Behaviours verified as correct and worth knowing: all 12 bank profiles detect and sign-normalize exactly as the fixture
maths predicts (`scotiabank.csv` intentionally lands on `wells_fargo`, relabel works); in-file twin rows (chase_checking
WHOLEFDS ×2) both import; re-upload commits 0 rows and leaves counts unchanged; `DELETE /api/statements/<id>` on a committed
statement is refused with 409 until `?with_transactions=true`, which removes exactly its transactions; deleting an
account removes its statements; `..`/unicode filenames are basenamed and stored; 25 MB+1 upload → JSON 413; cross-user
isolation is complete (including `category_id` of another user → 404, merge/reassign into foreign → 400/404, reorder with
foreign ids is a no-op).

---

### [P2] CSV export lets spreadsheet formulas through (CSV injection)
- **Area:** security
- **Where:** `backend/transactions_api.py:279-283` (`export_csv`), also `backend/reports_api.py:27-36` (`_csv_response`, merchant/category names)
- **What:** Description, merchant and notes cells are written verbatim. A statement line (or a manual transaction) whose text starts with `=`, `+`, `-`, `@` becomes an executable formula when the export is opened in Excel/Google Sheets/LibreOffice (DDE / `HYPERLINK` exfiltration of other cells). Statement descriptions come from third-party bank files, so the attacker does not need an account.
  Request: `GET /api/transactions/export?account_id=54&q=HYPERLINK` (as qa_api1, after `POST /api/transactions {"description":"=HYPERLINK(\"http://evil\",\"click\")","notes":"@SUM(A1:A9)",...}`)
  Response row: `2026-05-03,QA Manual,"=HYPERLINK(""http://evil"",""click"")",Hyperlink Http Evil,,-3.00,USD,@SUM(A1:A9),`
  Test: `TestTransactions::test_export_csv_escapes_formula_cells`. No backend log line (200 OK).
- **Repro:** create a transaction whose description starts with `=`; download the CSV; open in Excel.
- **Fix:** in both CSV writers, prefix any text cell starting with `=`, `+`, `-`, `@`, `\t`, `\r` with a single quote (or a leading space), e.g. `def safe(v): return "'" + v if v and v[0] in "=+-@\t\r" else v`; apply to description_raw, merchant_name, notes, category and report `name`/`merchant_name` columns.

### [P2] Any JSON endpoint returns 500 when the body is valid JSON but not an object
- **Area:** backend
- **Where:** every handler using `data = request.get_json(silent=True) or {}` then `data.get(...)` / `data.items()` / `"k" in data` — e.g. `backend/auth.py:50`, `accounts_api.py:70/98`, `categories_api.py:718/773/816/829`, `statements_api.py:188/212/233/284/305`, `transactions_api.py:303/317/416/461/567`, `review_api.py:99/174`, `rules_api.py:106/135/148/174`, `merchants_api.py:37`, `settings_api.py:53`, `reports_api.py:168`, `ai_api.py` (`insights/generate`)
- **What:** Bodies `[1,2,3]`, `"just a string"`, `12345`, `true` crash 29 of the 30 JSON endpoints probed (only `POST /api/ai/categorize` survives because it checks AI is enabled first). Unauthenticated `POST /api/auth/login` is affected, so this is reachable without a session.
  Request: `POST /api/auth/login` `Content-Type: application/json` body `[1,2,3]`
  Response: `500 {"error":"Something went wrong on the server. The details were logged."}`
  Backend log: `File "/app/auth.py", line 50, in login` → `AttributeError: 'list' object has no attribute 'get'` (54 occurrences in the run); for `PUT /api/settings` body `[1]`: `AttributeError: 'list' object has no attribute 'items'` (`settings_api.py:53`); for `PUT /api/transactions/<id>` body `12345`: `TypeError: argument of type 'int' is not iterable` (`transactions_api.py:462`).
  Test: `TestRobustness::test_non_object_json_body_never_500[*]` (29 parametrizations).
- **Repro:** `curl -X POST localhost:5559/api/auth/login -H 'Content-Type: application/json' -d '[1]'`
- **Fix:** add `util.json_body()` that returns `{}` when `get_json(silent=True)` is not a `dict` (or returns a 400 "JSON object expected") and use it everywhere instead of the inline idiom.

### [P2] Non-integer ids in JSON bodies are passed straight to SQL → 500
- **Area:** backend
- **Where:** `backend/transactions_api.py:417-418` (`create_transaction` account_id), `:435` (category_id insert), `:466` (`update_transaction` category lookup), `:581` (`bulk` categorize); `categories_api.py:726` (parent_id), `:788` (update parent_id), `:832` (merge `into`), `:872` (`?reassign_to=`); `merchants_api.py:37`; `review_api.py:110`; `statements_api.py:215` (`PUT /account`), `:248` (`PUT /rows` category_id)
- **What:** Values like `"abc"` (and, by the same path, `true`, `1.5`) reach `WHERE id = %s` on an INT column.
  Request: `PUT /api/transactions/9611` body `{"category_id":"abc"}`
  Response: `500 {"error":"Something went wrong on the server. The details were logged."}`
  Backend log: `File "/app/transactions_api.py", line 466, in update_transaction` → `File "/app/db.py", line 32, in query` → `psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type integer: "abc"` (22 occurrences). Same trace for the other listed sites (`POST /api/transactions {"account_id":"abc",...}` → line 417; `POST /api/categories {"parent_id":"abc"}` → categories_api.py:726; `POST /api/categories/<id>/merge {"into":"abc"}` → :832; `DELETE /api/categories/<id>?reassign_to=abc` → :872; `PUT /api/merchants/<key> {"category_id":"abc"}` → merchants_api.py:37; `POST /api/review/resolve {"ids":[..],"category_id":"abc"}` → review_api.py:110; `PUT /api/statements/<id>/account {"account_id":"abc"}` → statements_api.py:215; `PUT /api/statements/<id>/rows {"row_ids":[1],"category_id":"abc"}` → 500 verified by curl on a previewed statement).
  Tests: `TestRobustness::test_type_confusion_never_500[...body0/3/4/5/6/7/8/10/11/12, params9]`.
- **Repro:** `curl -X PUT .../api/transactions/<own id> -H 'Content-Type: application/json' -d '{"category_id":"abc"}'`
- **Fix:** coerce every id taken from a body/query through one helper (`util.to_int(value)` → int or `None`) and return 400 `"category_id must be an integer"` when it is not; `rules.py:_int_or_none` already does this for rules — reuse it.

### [P2] Unvalidated `int()` / `float()` on query-string and form parameters → 500
- **Area:** backend
- **Where:** `backend/statements_api.py:142-143` (`GET /api/statements?account_id=abc`, `?limit=abc`), `:69-70` (`GET /api/statements/<id>?offset=abc|limit=abc` while previewed), `:158` (`POST /api/statements` form field `account_id=abc`), `:289` (`POST /api/statements/<id>/commit {"account_id":"abc"}`); `transactions_api.py:332` (`GET /api/transactions/suggest?q=ab&limit=abc`), `:318` (`POST /api/transactions/auto-pair {"min_confidence":"abc"}`)
- **What:** Request: `GET /api/statements?limit=abc` → `500`. Backend log: `File "/app/statements_api.py", line 143, in list_statements` → `ValueError: invalid literal for int() with base 10: 'abc'` (12 occurrences across the sites above); `POST /api/transactions/auto-pair {"min_confidence":"abc"}` → `File "/app/transactions_api.py", line 318, in auto_pair` → `ValueError: could not convert string to float: 'abc'`; `POST /api/statements` multipart with `account_id=abc` → `statements_api.py:158` same ValueError.
  `GET /api/transactions?limit=abc` and `GET /api/review?limit=abc` already handle this correctly (try/except) — the fix is to apply the same pattern.
  Tests: `TestRobustness::test_type_confusion_never_500[GET-/api/statements-params18/19, GET-/api/transactions/suggest-params22, POST-/api/statements/{st}/commit-body14, POST-/api/transactions/auto-pair-body16]`, `test_upload_field_name_and_form_garbage`; `?offset=abc` verified with curl (500).
- **Repro:** `curl -b cookie 'localhost:5559/api/statements?limit=abc'`
- **Fix:** a small `int_arg(name, default, lo, hi)` helper with try/except returning the default (or 400), used in the six places.

### [P2] `GET /api/transactions?category_id=<non-numeric>` builds `AND ()` → SQL syntax error → 500
- **Area:** backend
- **Where:** `backend/transactions_api.py:94-103` (`build_filters`)
- **What:** When `category_id` contains only junk tokens (no ints and not `none`) `parts` is empty and the code appends `" AND ()"`. Same code path serves `/api/transactions/export`.
  Request: `GET /api/transactions?category_id=abc` → `500`.
  Backend log: `File "/app/transactions_api.py", line 204, in list_transactions` → `psycopg2.errors.SyntaxError: syntax error at or near ")"` / `LINE 4: ..._at FROM transactions t WHERE t.user_id = 17 AND () ORDER BY...`
  Test: `TestRobustness::test_type_confusion_never_500[GET-/api/transactions-None-params23]`.
- **Repro:** `curl -b cookie 'localhost:5559/api/transactions?category_id=abc'`
- **Fix:** only append the clause `if parts:` (or treat an all-junk filter as "no rows": `AND FALSE`).

### [P2] Out-of-range month → `date()` ValueError → 500 in insights, month-over-month and `range=month:`
- **Area:** backend
- **Where:** `backend/reports.py:52` (`month_bounds`) via `reports.py:114` (`resolve_range`), `reports.py:354` (`month_over_month`), `ai_api.py:83` (`_period`). `parse_month` (`reports.py:60-70`) validates the month (1–12) but not the year.
- **What:** Requests: `GET /api/insights?month=99999-01` → 500; `GET /api/reports/month-over-month?month=99999-01` → 500; `GET /api/reports/summary?range=month:99999-01` → 500 (all verified). `GET /api/transactions?range=month:99999-01` is fine (its `range_bounds` catches ValueError).
  Backend log: `File "/app/ai_api.py", line 83, in _period` → `File "/app/reports.py", line 52, in month_bounds` → `ValueError: year 99999 is out of range`.
  Test: `TestRobustness::test_type_confusion_never_500[GET-/api/insights-None-params40]`.
- **Repro:** `curl -b cookie 'localhost:5559/api/insights?month=99999-01'`
- **Fix:** in `parse_month` also require `1 <= y <= 9999` (and catch `ValueError` around `date()` in `resolve_range`'s `month:` branch), falling back to the current month like the other invalid inputs.

### [P2] `POST/PUT /api/accounts` with `"name": null` → 500
- **Area:** backend
- **Where:** `backend/accounts_api.py:31` (`_validate`): `name = (data.get("name") if "name" in data else ... or "").strip()` — the `or ""` only applies to the else-branch, so an explicit `null` is `.strip()`-ed.
- **What:** Request: `POST /api/accounts` body `{"name": null, "institution": null, ...}` → `500`. Backend log: `File "/app/accounts_api.py", line 72, in create_account` → `File "/app/accounts_api.py", line 31, in _validate` → `AttributeError: 'NoneType' object has no attribute 'strip'` (also via `update_account`, line 98).
  Tests: `TestRobustness::test_nulls_in_every_field_never_500[POST-/api/accounts-body2]`, `[PUT-/api/accounts/{acct}-body3]`.
- **Repro:** `curl -X POST .../api/accounts -H 'Content-Type: application/json' -d '{"name":null}'`
- **Fix:** `name = ((data.get("name") if "name" in data else (existing or {}).get("name")) or "").strip()`.

### [P2] Commit result does not count rows skipped as already-imported duplicates
- **Area:** backend
- **Where:** `backend/importer/pipeline.py:762-764` and `:788-790` (`_commit_locked`)
- **What:** Preview rows flagged `duplicate_of` get `include=False` at staging (`pipeline.py:589`). At commit they are neither `candidates` (so the `existing` check at 788 never sees them) nor `excluded_by_user` (which requires `duplicate_of IS NULL`). The result — and therefore `statements.stats` and the UI's import summary — says `skipped_duplicates: 0` when in fact every row was skipped as a duplicate; `skipped_duplicates` only counts duplicates discovered *at commit time* (a race that practically never happens).
  Request: re-upload `discover.csv` into the same account (preview shows `dupes_existing: 3`), then `POST /api/statements/<id>/commit {"account_id": <acct>}`
  Response: `{"imported":0,"skipped_duplicates":0,"skipped_invalid":0,"excluded_by_user":0,"categorized":{...},"ai_queued":false}`
  Test: `TestImportPipeline::test_commit_reports_known_duplicates_as_skipped`. No backend log line (200 OK).
- **Repro:** import any fixture twice into one account; look at the commit summary of the second import.
- **Fix:** `skipped_dupes = sum(1 for r in all_rows if r["is_valid"] and r["duplicate_of"] is not None)` before the loop (and keep adding commit-time collisions to it).

### [P3] A subcategory can be created with a different `kind` than its parent
- **Area:** backend
- **Where:** `backend/categories_api.py:731` (`kind = data.get("kind") or (parent["kind"] if parent else "expense")`)
- **What:** `POST /api/categories {"name":"QA Expense Under Income","parent_id":<Income id>,"kind":"expense"}` → `201 {"kind":"expense", ...}`. The update path (`:807-808`) deliberately re-syncs children when a parent's kind changes, and the `transfer` kind drives the `is_transfer` trigger (migration 002), so a mismatched child (e.g. an `expense` child under `Transfers`, or a `transfer` child under `Dining`) silently changes how its transactions are counted.
  Test: `TestCategories::test_kind_mismatch_child_under_income_parent`.
- **Repro:** as above.
- **Fix:** when `parent_id` is given, ignore/validate the supplied kind: `kind = parent["kind"]` (return 400 if a different kind is explicitly requested).

### [P3] System categories (including `Transfers`) can be deleted without any guard
- **Area:** backend
- **Where:** `backend/categories_api.py:846-880` (`delete_category`), `:825-843` (`merge`)
- **What:** `DELETE /api/categories/<id of "Taxes" (is_system=true)>` → `200 {"ok":true,"references":{...}}`. Deleting the `Transfers` tree removes the slugs `transfers.internal` / `transfers.card_payment` that `transfers._transfer_category_id`, `rules_api._transfer_category_id`, `categorizer._transfer_category` and `review_api.resolve` look up; from then on pairing/"mark as transfer" silently stores `category_id = NULL` (they all tolerate `None`). `POST /api/categories/reset-defaults` re-creates them, so this is recoverable. Verified: `TestCategories::test_system_category_deletion_and_reset_defaults` (asserts current behaviour).
- **Repro:** `curl -X DELETE .../api/categories/<Transfers id>` (unused) → 200.
- **Fix:** refuse to delete/merge `is_system` roots of kind `transfer`/`income` (409 "System category; hide it instead"), or at minimum the `transfers*` slugs; expose a soft-hide flag if users want them gone from menus.

### [P3] Report date parameters fail open instead of returning 400
- **Area:** api
- **Where:** `backend/reports.py:303-311` (`resolve_range` custom branch), `transactions_api.py:198` (sort fallback)
- **What:** `from=garbage` is treated as 1970-01-01, `to=2024-99-99` as today, `from > to` are silently swapped, `range=bogus` becomes `custom`, and `sort=evil` falls back to `-date` — all `200`. Meanwhile `months=abc`, `limit=many`, `parent_id=abc` return 400. The inconsistency makes client bugs invisible (a mistyped date returns "all time" numbers). Verified by `TestReports::test_lenient_params_never_500` and `test_reversed_dates_are_swapped_not_rejected` (which assert the current lenient behaviour so they do not fail).
- **Repro:** `GET /api/reports/summary?from=garbage` → 200 with `range.start = 1970-01-01`.
- **Fix:** return 400 `"from/to must be YYYY-MM-DD"` when a supplied date does not parse and 400 when `from > to` (or document the swap); return 400 for unknown `range`/`sort` values.

### [P3] Notes for the auditor (not defects, verified behaviour)
- `PUT /api/settings` and `/api/settings/openrouter/*` are **per-user** endpoints (`login_required`), not admin-only; a non-admin sending `shared: true` / `clear_shared: true` is correctly ignored (`settings_api.py:586, 598`) — verified, shared settings unchanged.
- Admin protection covers self-delete / self-deactivate / self-demote only (`settings_api.py:687-702`); there is no "last remaining admin" check, but since an admin can never remove themself the instance cannot be left without an admin.
- Invalid `sort` on `/api/transactions` and `limit` ≤ 0 / > 500 are clamped rather than rejected (`transactions_api.py:198-202`).
- OpenRouter network endpoints (`/api/settings/openrouter/models`, `/test`) were not exercised to keep the suite offline-deterministic; the AI paths without a key return 502 as designed (verified).
- A binary `.exe` renamed `.csv` is accepted by the sniffer (extension wins) and parses to a preview with 0 valid rows or an error state — never 500 and never importable (verified).

## Summary

- **P0:** 0 **P1:** 0 **P2:** 8 **P3:** 3 (+ notes). Suite: 373 tests, 320 pass, 53 fail; every failure maps to one of the findings above (50 of the failures are the robustness probes behind F-02…F-07).
- Cross-user isolation, auth gating (87 routes), all 23 fixture imports/dedupe/rollback flows and all report maths are correct.

Top 5:
1. **CSV export formula injection** (`transactions_api.py:279`, `reports_api.py:27`) — bank-supplied descriptions become executable spreadsheet formulas; one-line escaping fix.
2. **Non-object JSON body → 500 on 29 endpoints incl. unauthenticated `/api/auth/login`** (`request.get_json(...) or {}` idiom) — replace with a shared `json_body()` helper.
3. **Non-integer ids reach SQL → 500** in 12 handlers (`transactions_api.py:417/466/581`, `categories_api.py:726/832/872`, `merchants_api.py:37`, `review_api.py:110`, `statements_api.py:215/248`) — coerce ids with one helper, 400 on failure.
4. **Unvalidated `int()`/`float()` on query/form params → 500** (`statements_api.py:69/142/158/289`, `transactions_api.py:318/332`) plus `category_id=abc` → SQL `AND ()` (`transactions_api.py:103`) and `month=99999-01` → `ValueError` (`reports.py:52`).
5. **Import commit reports `skipped_duplicates: 0` for rows already imported** (`importer/pipeline.py:762-790`) — the import summary shown to the user is wrong for every re-import.
