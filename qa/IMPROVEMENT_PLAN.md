# iSpend — QA findings and improvement plan

Date: 2026-09-07. Audit of the app as running from the working tree (commit `4d3c38f` plus the uncommitted PNC importer work).

This document is the single entry point. It is written so that a fresh session can pick any phase and execute it without re-reading the six underlying reports. Every item carries the file/line to change, the fix, and how to verify it. The underlying reports hold the evidence (request/response dumps, screenshots, axe output):

| Report | What it covers | Findings |
|---|---|---|
| `qa/reports/backend-review.md` | Static + live review of every backend module, migrations, Docker/nginx/install.sh | P0 1 · P1 3 · P2 13 · P3 13 |
| `qa/reports/api-tests.md` | 373 black-box API tests (`qa/e2e/test_api.py`): auth, isolation, all 23 fixture imports, reports maths, robustness | P2 8 · P3 3 |
| `qa/reports/frontend-js-review.md` | Line-by-line review of all 21 JS files + 11 HTML pages | P1 4 · P2 14 · P3 33 |
| `qa/reports/css-a11y-review.md` | Tokens, contrast maths, axe-core on every page × theme × 3 viewports, dead CSS | P1 1 · P2 16 · P3 14 |
| `qa/reports/smoke-e2e.md` | Playwright: 84 page loads × personas × themes × viewports, console/network capture, 331 read-only probes, perf table | P1 1 · P2 3 · P3 4 |
| `qa/reports/flows-e2e.md` | Playwright: 12 end-to-end user flows through the real UI with a fresh user | P1 2 · P2 8 · P3 10 |

After de-duplication (the cache leak, double date picker, mobile overflow, CSV injection, non-integer 500s, merchant sort and favicon were each found by 2–3 audits) there are **~135 distinct findings: 1 P0, 8 P1, ~50 P2, ~75 P3**.

## What is healthy (verified, no action needed)

- Per-user data isolation on every `<int:id>` route, bulk, pair/unpair, merge, file download: all 404/403 for foreign ids (api-tests, 87 routes gated).
- All 23 statement fixtures (12 banks, xlsx/xls, headerless, semicolon, positive-charges) upload, detect, parse, commit, de-duplicate on re-upload and roll back on statement delete with correct sums.
- Every `/api/reports/*` number matched independently computed sums from the fixture CSVs; empty users return zeros, never 500.
- Zero console errors, page errors, unhandled rejections, failed requests or slow (>1.5 s) responses across 84 page loads and 331 interactive probes. No `undefined/NaN/null` text anywhere.
- Focus rings visible on all 792 recorded tab stops; landmarks, dialog semantics and reduced-motion handling are correct; both themes have full token coverage.
- Nothing slower than 2 s anywhere in the flows; page DOMContentLoaded ~20 ms, first data render < 1 s.
- Backend unit suite inside the container: 289 tests, 288 pass; the one failure is a fixture bug (T-3), not a product bug.

---

## Phase 0 — Housekeeping (do first, 30 min)

- **H-1** Commit the uncommitted PNC importer work (`backend/bank_profiles/pnc.py`, 3 fixtures, tests, nginx.conf changes, README) so QA fixes land on a clean base. `git status` shows 9 modified + 4 untracked files.
- **H-2** Add `backend/requirements-dev.txt` (flask, python-dateutil, rapidfuzz, openpyxl, xlrd, pdfplumber… = `requirements.txt` minus DB drivers as needed) and `backend/tests/__init__.py`; document `python -m unittest discover -s tests -t .`. The local run currently fails on 25 modules only because the host interpreter lacks the third-party packages (backend-review "Local unittest fails"). `make test` target optional.
- **H-3** Decide whether `qa/` is committed. Recommended: commit `qa/e2e/`, `qa/reports/*.md`, `qa/CONTEXT.md`, this file; ignore `qa/reports/screenshots/`, `qa/reports/*.json*`, `qa/e2e/__pycache__`, `.ruff_cache` (already in `qa/.gitignore`).

---

## Phase 1 — Security (P0/P1 — ship before anything else)

**S-1 [P0] Deactivation / role change not enforced per request.**
Where: `backend/auth.py:15-34` (`login_required`, `admin_required` trust the signed cookie); only `/api/auth/me` re-checks `is_active`.
Evidence: deactivated user's cookie → `GET /api/accounts` 200, `POST /api/accounts` 201; demoted admin → `GET /api/users` 200 (backend-review).
Fix: in both decorators load `SELECT role, is_active FROM users WHERE id=%s` (cache on `flask.g` for the request); if missing/inactive → `session.clear()` + 401; take `role` from the row, not the cookie. Also `session.clear()` before populating on login (rotation).
Verify: add `backend/tests/test_auth.py` cases (401 after deactivate, 403 after demote) and `qa/e2e/test_api.py::TestAuth`.

**S-2 [P1] Cross-user data leak in the browser (shared machine).**
Where: `frontend/js/nav.js:156-172` Sign out only calls logout + redirects; `frontend/js/pages/login.js:22-24` login does not clear storage; `frontend/js/store.js:14-33` sessionStorage cache `ispend.cache.*` served fresh-within-TTL without revalidation; `frontend/js/api.js:75-113` query memory `ispend.q:*`; `pickers.js:212` `ispend.period`; `review.js:2` `ispend.review.skipped`; localStorage `ispend.recentCats`, `ispend.importAccount`, `ispend.catCollapsed`, `ispend.breakdown.*`.
Evidence: qa_tester's account filter listed admin's five accounts right after signing in (smoke F1, flows F11 screenshots).
Fix: add `clearUserState()` to `api.js` removing every `ispend.*` key from both storages except `ispend.theme`, `ispend.density`, `ispend.sidebar`; call it on Sign out (before navigation) and on login success. Additionally namespace the store cache by user id (`ispend.cache.<uid>.<key>`) and stamp `ispend.uid` in `initNav`, clearing when it differs (covers session expiry + re-login).
Verify: `test_smoke.py::test_auth_*`, `test_flows.py::F11`.

**S-3 [P1] IDOR: `POST /api/transactions` accepts another user's `category_id`.**
Where: `backend/transactions_api.py:434-449` (also learned into merchant memory → foreign category names leak via exports/reports).
Fix: ownership check `SELECT id FROM categories WHERE id=%s AND user_id=%s` → 404; consider composite FK `(user_id, category_id)`. Add an isolation test.

**S-4 [P1] No login rate limiting; 6-char password floor; timing-based username enumeration.**
Where: `backend/auth.py:50-65`, `settings_api.py:129,185`, `nginx/nginx.conf:30-38`, `nginx/nginx.ssl.conf.template`.
Fix: `limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;` + `limit_req zone=login burst=10 nodelay;` on `location = /api/auth/login` in both nginx configs; hash against a dummy hash when the user is missing; raise minimum to 10 chars (or zxcvbn-style check).

**S-5 [P1] ReDoS through regex rules.**
Where: `backend/rules.py:41-45` (only `re.compile`), `rules.py:78-91,122-129`, `rules_api.py:14-31,144-167`, `importer/pipeline.py:147-168,410-417`.
Evidence: `(a+)+$` accepted; 26-char input → 3.2 s; rules run on every preview/commit/run with 2×4 gunicorn threads.
Fix: cap pattern length (200), reject nested quantifiers, run matches via the `regex` package with `timeout=` (or `re2`); disable a rule after it times out.

**S-6 [P1] Stored XSS in the rule delete confirm.**
Where: `frontend/js/pages/rules.js:177` (`body: \`“${r.pattern}”…\`` into `ui.confirm`, which inserts raw at `ui.js:107`). Pattern can originate from imported statement text via rule-draft.
Fix: `esc(r.pattern)`; make `ui.confirm` escape `body` by default and accept `{html}` for rich cases. Also `categories.js:333` (`placeholder="…${parent.name}…"` → `esc`).

**S-7 [P2] CSV export formula injection.**
Where: `backend/transactions_api.py:271-287`, `backend/reports_api.py:27-36`.
Fix: prefix cells starting with `= + - @ \t \r` with `'`; strip control chars. Test in `test_transactions_api.py` and `test_api.py::TestExport`.

**S-8 [P2] No security headers.**
Where: `nginx/nginx.conf`, `nginx/nginx.ssl.conf.template` (HSTS without includeSubDomains), `backend/app.py:49-54`.
Fix: `X-Frame-Options DENY`, `X-Content-Type-Options nosniff`, `Referrer-Policy same-origin`, CSP `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'` (Chart.js is local so `script-src 'self'` works), HSTS with `includeSubDomains`.

**S-9 [P2] Open redirect on login `next`.**
Where: `frontend/js/pages/login.js:24` (`/\evil.com` passes the leading-slash check).
Fix: `const u = new URL(next, location.origin); location.href = u.origin === location.origin ? u.pathname + u.search + u.hash : '/index.html'`.

**S-10 [P2] Deleted user's per-user settings (incl. OpenRouter key) remain in `settings`.**
Where: `settings_api.py:170-173`, `db.py:88-94` (`u<id>:<key>` rows, no FK).
Fix: `DELETE FROM settings WHERE key LIKE 'u<id>:%'` in `delete_user`, or migrate to `user_settings(user_id FK CASCADE)`. Consider encrypting keys at rest with `SECRET_KEY`.

**S-11 [P2] Docker hardening.** `backend/Dockerfile` runs as root, no `.dockerignore`; compose has no backend/nginx healthcheck; install.sh backups world-readable. Fix per backend-review "Docker/compose hardening gaps".

P3 security nits (backend-review): `LIKE` wildcard escaping in search (`transactions_api.py:115-119,333`, `merchants_api.py:25-27`); review-created rule skips `account_id` ownership (`review_api.py:139-162` → call `rules_api._validate_refs`); session lifetime 14 d / role in cookie; internal error strings surfaced to users (`pipeline.py:82-84`, `openrouter.py:146-147`); AI cost caps.

---

## Phase 2 — Data correctness (wrong numbers or wrong behaviour)

**D-1 [P1] Unconfirmed "suggested" transfers are excluded from spending/income.**
Where: `backend/importer/pipeline.py:423-424` sets `is_transfer`/`is_excluded` for `status="suggested"` decisions from builtin hints (`categorizer.py:9`, `builtin_hints.py:9-19`). Contradicts README ("Suggestions are never auto-confirmed"); dashboard and reports drop rows nobody confirmed (flows F8 screenshots).
Fix: set the flags only when the decision is `confirmed` (rule / memory / manual); Review's Accept/Transfer sets them. Product decision to confirm: alternatively show a "(unconfirmed transfers)" bucket in totals.

**D-2 [P1] Settings → AI "Share this key with all users" is a no-op.**
Where: `frontend/js/pages/settings.js:141` sends `PUT /api/settings {shared:true}`; `backend/settings_api.py:53-64` only publishes when key+model are in the same payload (they are masked client-side so never are). UI toasts success and the switch snaps back.
Fix: backend copies the caller's stored user key/model into global settings when `shared:true` arrives without them; frontend reflects `shared_available` from the response.

**D-3 [P2] Import commit reports `skipped_duplicates: 0` on re-imports.**
Where: `backend/importer/pipeline.py:762-764, 788-790`. Fix: count `is_valid AND duplicate_of IS NOT NULL` before the loop and add commit-time collisions.

**D-4 [P2] "Skip N duplicates" switch on the import preview is dead.**
Where: `import.js:329, 399`; `statements_api.py:96`; `pipeline.py:397-400` always skips existing fingerprints. Fix: remove the switch and per-row checkbox for duplicate rows and state "N duplicates will be skipped" — or make commit honour `include`. Pick one; the first is simpler and matches "duplicate safe".

**D-5 [P2] Rule "Apply to existing · Only uncategorized" skips suggested rows while the preview counts them.**
Where: `rules_api.py:18-19` (`category_id IS NULL`) vs preview `:155` and review queue `review_api.py:17` (`OR category_status='suggested'`). Fix: treat `suggested` as uncategorized in `_scan`; preview uses the same filter as apply.

**D-6 [P2] Fuzzy merchant memory treats token subsets as 100 % matches.**
Where: `backend/categorizer.py:13, 93-100` (`token_set_ratio`, cutoff 92): UBER ↔ UBER EATS, AMAZON ↔ AMAZON PRIME. Fix: `WRatio`/`ratio` or require equal token count; never fuzzy-match single-token keys. Regression test in `test_categorizer.py`.

**D-7 [P2] Transfer pairing overwrites existing pairs → dangling one-way pairs.** `transfers.py:49-81`, `transactions_api.py:300-311`. Fix: 409 if either side already paired (or unpair inside the same transaction). Also `candidates` should require same currency (`transfers.py:16-30`).

**D-8 [P2] Changing a category's kind to/from `transfer` leaves `is_transfer/is_excluded` stale.** `categories_api.py:149-156`; trigger in `migrations/002` fires only on `category_id` change. Fix: after the kind update, `UPDATE transactions … WHERE category_id IN (cat+children) AND transfer_pair_id IS NULL`.

**D-9 [P2] `renormalize` fingerprints drift from import-time fingerprints** (phrases per account vs per statement) so re-imports stop de-duplicating after a renormalize. `maintenance.py:20-27,46-52` vs `pipeline.py:195-197,383-388`, `dedupe.py:7-10`. Fix: make the fingerprint independent of phrase stripping, or renormalize per statement.

**D-10 [P2] Display currency CAD renders exactly like USD.** `format.js:20` uses `currencyDisplay:'narrowSymbol'`. Fix: use `'symbol'` (CA$) when the currency differs from the locale default, or append the ISO code when >1 currency is in play. Related P3: Transactions summary picks the alphabetically first currency instead of `tx.displayCurrency` (`transactions.js:209-210`).

**D-11 [P2] Compact money formatter cache returns wrong/duplicate axis labels** ("$2K, $2K, $3K"). `format.js:20-24` memo key omits the value-dependent fraction-digit rule; consumers `charts.js:53-55` (every y-axis). Fix: include the branch in the key.

**D-12 [P2] Transactions drawer loses History / Other charges / Imported from after a category change.** `transactions.js:453, 523`. Fix: copy only category fields onto `it`, or refetch `/api/transactions/:id` before `setBody`.

**D-13 [P2] Review "Always do this" is on for every group** (`review.js:72` `always: g.count >= 1`) → a rule per decision (209 rules on the admin account is the symptom). Fix: default `count >= 2` or off, user opt-in.

**D-14 [P3]** Out-of-range month / bad `range=month:` (`reports.py:52,60-70`, `ai_api.py:83`); report date params fail open instead of 400; subcategory may have a different kind than its parent (`categories_api.py:731`); system categories incl. Transfers deletable (`categories_api.py:846-880`); monthly insights cache never invalidated (`insights.py:85-89`); `date.today()` UTC in year inference (`importer/dates.py:57,66`); Chase card CSV auto-assigned to Chase checking by institution alone (`statements_api.py:48`, `import.js:441-449`); Reports "By category" CSV exports `level=top` while the table shows subs (`reports.js:94 vs 117`); "Spending by month" chart ignores the range picker (`reports.js:116-117`); hidden 24-month windows for recurring/cash flow with no explanation (`reports.py:421`, `reports.js:29`).

---

## Phase 3 — Robustness: eliminate every 500 (one sweep, ~2 h)

All verified live by `test_api.py` (50 failing probes map here). One pair of helpers fixes everything:

```python
# backend/util.py
def json_body():            # {} unless get_json(silent=True) is a dict → else abort(400, "JSON object expected")
def to_int(v, name, lo=None, hi=None, required=False):   # int or None; abort(400, f"{name} must be an integer")
```
plus `@app.errorhandler(ValueError)` → 400 in `app.py`.

- **R-1** Non-object JSON body → 500 on 29 endpoints incl. unauthenticated `/api/auth/login` (every `request.get_json(silent=True) or {}`). Replace with `json_body()`.
- **R-2** Non-integer ids in bodies reach SQL → 500: `transactions_api.py:417,435,466,581`; `categories_api.py:726,788,832,872`; `merchants_api.py:37`; `review_api.py:110`; `statements_api.py:215,248`.
- **R-3** Unvalidated `int()/float()` on query/form params: `statements_api.py:69-70,142-143,158,289`; `transactions_api.py:305,318,332`. Also negative `LIMIT`.
- **R-4** `GET /api/transactions?category_id=abc` builds `AND ()` → SQL error (`transactions_api.py:94-103`): only append the clause `if parts`.
- **R-5** `POST/PUT /api/accounts {"name": null}` → 500 (`accounts_api.py:31`).
- **R-6** Admin user endpoints return `{"ok":true}` for unknown ids (`settings_api.py:165-189`); preferences accept arbitrary JSON (`auth.py:84-92`) — validate theme/density/currency/color/default_account_id, cap strings.
- **R-7** Import failure shows the raw Python exception in the preview and nothing in the upload list (`import.js:97`, `pipeline.py:82-84`, `csv_parser.py`). Map parser exceptions to "This file does not look like a CSV statement"; render `error_message` in the file row.
- **R-8** Multi-statement writes without a transaction (`accounts_api.py:123-128`, `review_api.py:115-164`, `rules_api.py:56-78`, `statements_api.py:340-346`, `transactions_api.py:435-449`): wrap in `with db.transaction():`.
- **R-9** AI statement extraction runs synchronously past the 300 s nginx/gunicorn budget (`statements_api.py:351-369`, `ai_extract.py:44-58`): run through `jobs.spawn` and poll like reparse; cap pages.

Verify: `pytest qa/e2e/test_api.py` must go 373/373 green.

---

## Phase 4 — Frontend correctness (races, leaks, silent failures)

- **F-1 [P1] Transactions filter-change race** (`transactions.js:175-207`): `tx.loading` gate drops the new first load and renders the stale response under the new filters. Fix: bump `tx.seq` and reset `loading` in `reload()`; or AbortController per request.
- **F-2 [P2] Double date-range picker on Transactions** (`transactions.js:41` + `:127` `mountRangeButton`): delete line 41. Verified by smoke + flows (Esc needed twice, shortcuts stay disabled).
- **F-3 [P2] No request sequencing on Dashboard / Insights / Reports / Review** (`dashboard.js:80-118,229-252,346-358`; `insights.js:22-31`; `reports.js:67-77,105-122,171-178,198-206,226-242,270-279`; `review.js:65-78` mode switch parses the wrong shape → TypeError, list stuck on skeleton). Fix: per-page `seq` captured before each fetch, as transactions does.
- **F-4 [P2] Import preview shrinks to 500 rows after any account/mapping/category/AI change** (`import.js:368-376,431-432,469,479,495`; `statements_api.py:14-15,70` ROW_LIMIT 500). Fix: `reloadStatement(f,{silent:true})` after those calls, or honour `?limit=` on PUT.
- **F-5 [P2] Chart instance leaks** (`reports.js:200,215,220-221` sparklines; `categories.js:180,219`; `dashboard.js:251,279` donut error branch + theme listener per render; `charts.js:61-72` registry never pruned). Fix: prune `!canvas.isConnected` entries in `makeChart`/`rerenderAll` and destroy.
- **F-6 [P2] Silent failures**: Settings menu actions (`settings.js:332,338,341,380,384`) call `api()` without try/catch; page init on Transactions/Review has no error handling → blank page (`transactions.js:27-29`, `review.js:16-18`); `updateItem` rethrows into un-caught callers (`transactions.js:401,314,349,527`); Settings model list never retries after a failed fetch (`settings.js:218-221`).
- **F-7 [P2] Modal primary actions double-submit** (`ui.js:78-86` sets `.is-loading` but not `disabled`; submit relays at `transactions.js:661,588`, `rules.js:292`, `settings.js:91,327`, `import.js:601`, `nav.js:197`). Fix: `b.disabled = true` during the action; relays bail on `.is-loading`.
- **F-8 [P2] Bulk actions refetch N transactions individually** (`transactions.js:413-417`). Fix: `/bulk` returns updated rows, or single `reload()` when `ids.length > 20`. Related: summary totals stale after delete/transfer/exclude (`transactions.js:403-412`).
- **F-9 [P2] Import mapping editor: role change made no visible change; Debit column with negative numbers yields 0 rows (cause unverified)** (`import.js:347-360,397`; importer amount parsing). Investigate: log the PUT, show an inline error when a mapping yields 0 valid rows, treat negatives in a Debit column as debits.
- **F-10 [P2] Breakdown table: Enter on a category link toggles the row instead of following it** (`breakdown.js:95` missing the `closest('a')` guard that `:92` has).
- **F-11 [P2] Theme flash for saved-dark users on a fresh browser** (`nav.js:143` applies after `/api/auth/me`). Fix: `Theme.set(res.preferences.theme)` in `login.js` before redirecting.
- **F-12 [P3] batch**: `apiUpload` 401 drops `next` (`api.js:40`); `rules.html?new=1` remembered by query memory (`api.js:79`); `rangeFromQuery` trusts any string → "Until —" (`pickers.js:151-170`); palette transaction result lacks `range=all` (`nav.js:247`); topbar theme toggle not persisted (`nav.js:112,224` → `setThemePref`); `ui.menu` ignores `onClose` → dashboard `aria-expanded` stuck (`ui.js:213-227`); rule-name check mismatch for `starts_with` (`rules.js:78`); double-escaping into toast/title (`import.js:435-436,497`, `categories.js:385`); search Enter + debounce double reload (`transactions.js:38-39`); whole-table re-render on selection (`transactions.js:317,336,431`); review renders twice on resolve (`review.js:328-342`); `icon()` resolves prototype keys (`icons.js:137`, validate server-side `categories_api.py:85,128`); reports trend keyed by series index (`reports.js:184-189`); "Other" bucket links to all transactions (`reports.js:149`); transfer-only merchants cannot be renamed (`rules.js:400-402` vs `merchants_api.py:37-39`); import drop outside dropzone navigates away (`import.js:81-83`); `autoAccount` suppresses review render (`import.js:176,441-450`); "same file uploaded before" hint vanishes after parse (`statements_api.py:27,169`, `import.js:103,147`); second tab keeps stale category name 60 s (`store.js:19-25` → BroadcastChannel); Review fetches transfer candidates on every load (`review.js:25`); Categories side panel shows spending data for income categories (`categories.js:29-30,205,212`); `settings.html` uses `data-admin` not `data-admin-only` (README mismatch); dead/duplicate JS listed in frontend-js-review.

---

## Phase 5 — Mobile and responsive (390 px)

- **M-1 [P2] Horizontal page overflow on Dashboard (434 px) and Insights (490 px)** with data. Root cause: `.grid` `1fr` tracks inherit min-content (`app.css:83-88`), `.card-head` never wraps (`:273`), `.recent .list-item`, `.ins-grid`. Verified fix: `.grid > * { min-width: 0 }` (or `minmax(0,1fr)`), `.card-head { flex-wrap: wrap }` ≤768, `.bd-wrap/.tbl-wrap { overflow-x:auto; max-width:100% }`.
- **M-2 [P2] Transactions empty/skeleton rows squeezed into the 28 px checkbox column** (`transactions.css:66-68` grid rows vs `<td colspan=7>`). Fix: `.tbl-tx td[colspan] { grid-column: 1 / -1 }` or render the empty state in `#tx-foot`.
- **M-3 [P2] Hover-only row actions invisible on touch** (Statements, Insights dismiss; `app.css:344-345`, `statements.css:11-17`). Fix: `@media (hover:none) { .row-actions, .cat-actions, .rule-actions, .rec-dismiss { opacity:1 } }`.
- **M-4 [P2] Page-action buttons lose their name ≤640** (`app.css:566` hides `.label` with `display:none`; `#btn-add`, `#btn-export`, `#btn-import`, `#month-btn`). Fix: `.sr-only` instead, or `aria-label`.
- **M-5 [P2] Touch targets < 24 px**: `.sugg-act .btn` 22 px (`transactions.css:26`), `.check` 16 px (`app.css:248`), `.cat-chevron` 20 px. Fix sizes; consider `@media (pointer:coarse) { .btn-xs,.seg-btn,.catchip { min-height:32px } }`.
- **M-6 [P3]** Off-canvas nav not a layer: no Esc, no focus trap (`nav.js:101-107`); status filter clipped with no scroll affordance (`transactions.css:82`); `.floatbar` centred on viewport not content (`app.css:499`); text 9–10 px in bottom nav / account marks / mini badges (`app.css:548,317,572`, `transactions.css:30`).

---

## Phase 6 — Accessibility and contrast

- **A-1 [P1] Rail-mode sidebar: 11 nav links have no accessible name** (`app.css:131` `display:none` on `.label`; `nav.js:44-47`). Fix: `.sr-only` pattern or `aria-label` in `navItemHtml`. Affects every page at 960–1279 px.
- **A-2 [P2] `--text-4` used for real content fails contrast** (2.5:1 light / 3.0:1 dark; `tokens.css:21,113,160`; consumers `.merchant-raw`, `.rv-raw`, `.rt-date`, `.legend-pct`, `.sb-group-label`, `.menu-count`, `.tl-time`, `.is-zero`…). Drives most of the 2,408 axe color-contrast hits. Fix: raise to light `#6f7a8c` / dark `#7f8898`, add `--text-disabled` for decorative uses.
- **A-3 [P2] Light accent `#4f6ef7` < 4.5:1** as text and as `.btn-primary` background (4.28), `.pill`, `.badge-info`, `.catchip--suggested`, links, `.tab.active`. Fix: `#3f5fe0` accent / `#2f4ccc` hover in light.
- **A-4 [P2] `--text-3` on tinted surfaces just under 4.5:1 light** (table headers, seg buttons, page subtitles, kbd): nudge to `#626b7a`.
- **A-5 [P2] `.badge-danger` / `.error-box` text 4.41:1 light**: `--danger-text: #b91c1c`.
- **A-6 [P2] Chart palette slots c3/c4/c5 fail 3:1 on light surfaces** (`tokens.css:44-46`); `.acct-mark` white text on them. Deepen light slots; pick fg by luminance.
- **A-7 [P2] Review non-focused cards dimmed to 72 % opacity** push text below 4.5:1 (`review.css:8,17`). Dim via border/background instead.
- **A-8 [P2] Invalid ARIA in shared widgets**: `ui.multiFilter` (`ui.js:261-314`: buttons/input inside `role=menu`, nested checkbox in button, two All/Clear pairs), `dateRangePicker` (`pickers.js:179-183`: `menuitemradio` without group, unlabeled date inputs), `label[role=button]` dropzone (`import.js:67`), `aside[role=dialog]` drawer (`ui.js:125`), `tr[aria-expanded]` in breakdown (`breakdown.js:67`), `#f-flow` seg without pressed state (`transactions.html:28`), Reports tabs/segs not using `ui.tabs`/`ui.segmented` (`reports.html:20-31`, `reports.js:27,56-59`).
- **A-9 [P2] Controls without names**: `<select>` "Import into" `#upload-account`, `#pref-currency`, mapping skip-rows input, `#txd-notes` textarea; icon-only buttons rely on `title` (`categories.js:64-66`, `settings.js:59,171,311`, `rules.js:86,356`, `transactions.js:224,239,286-287`, `insights.js:58`, `#btn-density`); "Show key" toggle name never flips (`settings.js:118,410`); form labels not bound (`transactions.js:558,626,507`, `rules.js:219`, `categories.js:335-336`, `settings.js:77`, `import.js:296-298`).
- **A-10 [P3]** Sortable `th` not keyboard operable (`transactions.html:37-41`, `reports.js:200,212`); rows acting as links are `div`/`tr` (`dashboard.js:299`, `reports.js:165-168,215,265,313`, `statements.js:61,84-90`); focus lost after re-render (`pickers.js:105` + `transactions.js:242-250`, `review.js:224-230`, `rules.js:134`, `categories.js:249-262`); after Esc from the picker Enter reopens it instead of "Open details" as `?` claims; toast root lacks `aria-live` (`ui.js:372-376`); chatty live regions (`transactions.js:281`, `settings.js:268`, progress bars without `role=progressbar` `review.js:104,188`); empty `<th>` for action columns; `<ol class="rule-list">` gets `<div>` children when empty; login page has no `<main>`; spinners keep spinning under reduced-motion (optional).

---

## Phase 7 — UX polish and copy

- **U-1 [P2]** Merchant column sorts ascending only (`transactions.js:116-122`, `transactions_api.py:28-34` SORTS lacks `-merchant`; cursor helpers test `sort == "merchant"`).
- **U-2 [P3]** "1 transactions" pluralisation (`settings.js:382`, `categories.js:368,385`, `categories_api.py:236`) → `plural()`.
- **U-3 [P3]** Silent actions with no feedback: Archive/Restore account (`settings.js:380`), theme/density radios (`:287-288`), colour/icon popover (`categories.js:269`).
- **U-4 [P3]** "Clear filters" writes `range=this-month` and hides older data behind a filtered empty state (`transactions.js:100,110-115`) — drop default range from URL; offer "Show all time" in the empty state.
- **U-5 [P3]** Login has no password-visibility toggle (`login.html:23`); reuse the Settings › AI eye button.
- **U-6 [P3]** Dark theme `<select>` loses its chevron (`app.css:228-229` `background:` shorthand wipes the SVG → `background-color`). Verified visually.
- **U-7 [P3]** Dashboard KPI sparkline is resized by Chart.js to the whole card and paints over the value (`dashboard.css:4`, `charts.js:32` responsive default, `dashboard.js:152-160`; same for `charts.sparkline()` in reports). Fix: `responsive:false` + explicit size, or a fixed wrapper.
- **U-8 [P3]** Dark-theme hard-coded whites (`app.css:207,262,317,499-505`, `insights.css:38`); duplicate/split CSS rules (`app.css:271,280`, `reports.css:16,29`, `.stat-skel` copied); 22 dead selectors (list in css-a11y-review); missing favicon (`/favicon.ico` 404 on every page in nginx logs), manifest, print stylesheet.

---

## Phase 8 — Performance

- **P-1 [P3]** Chart.js (81 KB gz) loaded on Categories where no chart exists until a category is selected (`categories.html:44-45`): lazy-load via a `loadScript()` helper.
- **P-2 [P3]** Backend: `audit_log` written on nearly every request and never pruned (`util.py:14-20`); `ai_calls` lacks `(user_id, created_at)` index; one non-daemon thread per upload with no cap (`jobs.py:17-31` → `ThreadPoolExecutor(2)`); transfer-candidates self-join on every Review load; `pg_trgm` index for description search.
- **P-3 [P3]** Whole-table re-renders on selection changes and N-per-row refetch after bulk (see F-8, F-12).
Page-load numbers are otherwise excellent (see the perf table in smoke-e2e.md); nothing else to do here.

---

## Phase 9 — Tests, tooling and CI

- **T-1** Make `qa/e2e/test_api.py` green (373/373) after Phase 3 and keep it as the API regression gate (35 s, self-cleaning, creates/deletes `qa_api1/2`).
- **T-2** Make `qa/e2e/test_flows.py` green (12 flows; currently 3 pass, every failure maps to a finding above) and `test_smoke.py` (121 tests; 14 failures map to S-2, F-2, F-11, M-1, M-6, U-1, P-1).
- **T-3** Fix the failing `test_pdf_ocr.OcrEndToEndTest`: fixture renders 27 chars below the 40-chars/page `needs_ocr` floor (`tests/test_pdf_ocr.py:29-44`, `pdf_ocr.py:8,15-26`). Render ≥3 lines with a larger font and assert on extracted text.
- **T-4** Backend test gaps: no `test_auth.py`, `test_merchants_api.py`, `test_ai_api.py`, `test_jobs.py`; no cross-user isolation tests; add an opt-in DB integration suite (`ISPEND_TEST_DSN`) for the trigger, `ON CONFLICT` dedupe and `discard_statement`.
- **T-5** Adopt `ruff.toml` (`select = ["E","F","B","S","PLW"]`, `ignore = ["E501","S101","S608"]`) and fix the current F401/unused findings (`reports.py:12`, `importer/generic.py:3`, `importer/pipeline.py:6`, `maintenance.py:34`, `bank_profiles/__init__.py` `__all__`).
- **T-6** Add `node --check frontend/js/**/*.js` and the a11y scan (`qa/e2e/a11y_scan.py`) to a CI job; run the three Playwright suites nightly against the dev stack.

---

## Suggested execution order for fresh sessions

| Session | Scope | Items | Gate |
|---|---|---|---|
| 1 | Housekeeping + security backend | H-1..H-3, S-1, S-3, S-4, S-5, S-7, S-8, S-10 | `unittest` 289 green, `test_api.py::TestAuth/TestIsolation/TestExport` |
| 2 | Security frontend + robustness sweep | S-2, S-6, S-9, R-1..R-9 | `test_api.py` 373/373, `test_smoke.py -k test_auth` |
| 3 | Data correctness | D-1..D-13 (D-1 and D-4 need a product decision first) | `test_flows.py -k "F3 or F6 or F8"`, new unit tests |
| 4 | Frontend races/leaks | F-1..F-11 | `test_flows.py -k "F4 or F7 or F10"`, `test_smoke.py -k probes` |
| 5 | Mobile + a11y tokens | M-1..M-5, A-1..A-7 | `a11y_scan.py` (0 overflow, contrast table green), `test_smoke.py` mobile matrix |
| 6 | ARIA widgets + polish | A-8..A-10, U-1..U-8, P-1..P-3 | axe violations → 0 serious |
| 7 | Backlog | F-12, M-6, D-14, S P3 nits, T-4..T-6 | all three Playwright suites green |

## Re-running the QA operation

```bash
V=/private/tmp/claude-501/-Users-ruolez-Desktop-Dev-ispend/272cd059-cdbc-40ed-a58b-b1f0ef765b8f/scratchpad/qa-venv/bin
# (recreate if the scratchpad is gone: python3 -m venv qa-venv && pip install playwright pytest requests ruff pyflakes && playwright install chromium)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d          # app on :5559
$V/pytest qa/e2e/test_api.py -v                       # 373 API tests, ~35 s
$V/pytest qa/e2e/test_smoke.py -q -p no:cacheprovider  # 121 smoke tests, ~20 min (use -k to subset)
$V/pytest qa/e2e/test_flows.py -v -p no:cacheprovider  # 12 flows, ~2.5 min
$V/python qa/e2e/a11y_scan.py                          # axe + overflow + contrast → qa/reports/a11y-scan.json
$V/python qa/e2e/css_audit.py                          # token contrast maths + dead CSS
docker compose exec -T backend python -m unittest discover -s tests   # backend unit tests
```
Credentials and conventions for the suites are in `qa/CONTEXT.md`. Test users: `qa_tester` (kept, empty), `qa_flows` (recreated per run), `qa_api1/2` (created and deleted per run). Admin data was never modified.
