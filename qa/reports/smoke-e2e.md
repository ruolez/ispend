# iSpend — Playwright smoke & diagnostics (frontend E2E)

Run date: 2026-09-06/07 against the live dev stack at http://localhost:5559 (chromium headless, Playwright Python sync API).
Personas: `qa_tester` (empty data) and `admin` (populated; strictly read-only — every modal/drawer/menu the probes opened was cancelled with Escape, nothing was saved, deleted, committed or uploaded; the density and theme toggles were pressed twice so admin's preferences are unchanged).

## How to run

```bash
VENV=/private/tmp/claude-501/-Users-ruolez-Desktop-Dev-ispend/272cd059-cdbc-40ed-a58b-b1f0ef765b8f/scratchpad/qa-venv
cd /Users/ruolez/Desktop/Dev/ispend
$VENV/bin/pytest qa/e2e/test_smoke.py -q -p no:cacheprovider          # full suite, 121 tests, ~20 min
$VENV/bin/pytest qa/e2e/test_smoke.py -q -k "test_page_matrix"        # 80 page loads only (~4 min)
$VENV/bin/pytest qa/e2e/test_smoke.py -q -k "test_probes_admin"       # 20 interactive probe runs (~5 min)
$VENV/bin/pytest qa/e2e/test_smoke.py -q -k "test_auth or test_theme" # auth + theme behaviours (~1 min)
$VENV/bin/python qa/e2e/make_report.py [matrix|errors|slow|perf|api|probes|others|text]   # tables from the JSONL
```

- Files: `qa/e2e/test_smoke.py` (tests), `qa/e2e/helpers.py` (recorder, waits, DOM scans, probe JS), `qa/e2e/smoke_fixtures.py` (browser/context/login fixtures — `qa/e2e/conftest.py` belongs to the API suite), `qa/e2e/make_report.py` (table renderer).
- Every test appends one JSON record to `qa/reports/smoke-results.jsonl` (the file is reset at session start unless `SMOKE_APPEND=1`). Screenshots: `qa/reports/screenshots/smoke-<page>-<persona>-<theme>-<w>.png` (84) and `probe-<page>-admin-<theme>-<w>.png` (20, taken after all probes on that page).
- Each test gets a fresh browser context (isolated cookies/storage), logs in through `POST /api/auth/login` on the context's request client (cookie shared with the page), and seeds `localStorage.ispend.theme` through an init script *before* `theme.js` runs. An init script also converts `unhandledrejection` into a console error so it is captured.
- "Data loaded" = `window.currentUser` set → network idle → no visible `.skel`/`.skel-row`/`.is-loading` (except `.btn`) inside `#main` (8 s budget; leftovers are recorded as "stuck") → 450 ms for chart animation. No retries anywhere: a failing step stays failed.
- The run reported here was executed as three foreground invocations (matrix; probes; auth+theme) with `SMOKE_APPEND=1`; the matrix ran while another QA suite was exercising the same backend, yet no request exceeded the 1.5 s threshold.

## Pass/fail matrix (page × persona × theme × viewport)

A cell passes only if: zero console errors/warnings, zero `pageerror`, zero failed requests, zero HTTP ≥ 400, shell rendered (h1 + sidebar or bottom nav), no visible `.error-box`, no `undefined`/`NaN`/`null`/`[object Object]`/`$NaN`/`Invalid Date` in body text or `title`/`aria-label`/`placeholder`, skeletons cleared within 5 s, `html[data-theme]` and body background match the requested theme, no horizontal page scroll, and Chart.js not loaded on a page without charts.

| page | anon<br>light/1440 | anon<br>dark/1440 | anon<br>light/390 | anon<br>dark/390 | qa_tester<br>light/1440 | qa_tester<br>light/390 | qa_tester<br>dark/1440 | qa_tester<br>dark/390 | admin<br>light/1440 | admin<br>light/390 | admin<br>dark/1440 | admin<br>dark/390 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| login | PASS | PASS | PASS | PASS | — | — | — | — | — | — | — | — |
| index | — | — | — | — | PASS | PASS | PASS | PASS | PASS | **FAIL** (1) | PASS | **FAIL** (1) |
| transactions | — | — | — | — | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| review | — | — | — | — | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| import | — | — | — | — | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| statements | — | — | — | — | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| categories | — | — | — | — | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| rules | — | — | — | — | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| reports | — | — | — | — | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| insights | — | — | — | — | PASS | PASS | PASS | PASS | PASS | **FAIL** (1) | PASS | **FAIL** (1) |
| settings | — | — | — | — | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS |

80/84 page loads clean. All four failures are the same defect (F3: horizontal page overflow at 390 px on Dashboard and Insights with populated data). Empty states (qa_tester) rendered cleanly on every page: dashboard "Import your first statement", transactions "No transactions yet", review "All caught up", statements "No statements yet", rules "No rules yet", reports "No spending yet", insights "No recurring charges yet"/"Nothing unusual"/"AI insights are off", settings "No accounts yet".

Interactive probes (admin, read-only; 331 steps over 20 page runs):

| page | light/1440 | dark/390 | failing steps → finding |
|---|---|---|---|
| index | PASS (12 steps) | FAIL (14) | Open menu / More: sidebar not closable with Esc → F5 |
| transactions | FAIL (28) | FAIL (27) | 7× range preset: two popovers per click → F2; merchant sort one-way → F6; mobile: F5 |
| review | PASS (9) | FAIL (11) | F5 |
| import | PASS (9) | FAIL (11) | F5 |
| statements | PASS (8) | FAIL (10) | F5 |
| categories | PASS (9) | FAIL (11) | F5 |
| rules | PASS (9) | FAIL (11) | F5 |
| reports | PASS (25) | FAIL (25) | F5 (all 5 tabs, 7 range presets, 4 sortable headers both directions, flow/months/% segs clean) |
| insights | PASS (10) | FAIL (12) | F5 |
| settings | PASS (35) | FAIL (45) | F5 (all 5 settings tabs and their buttons clean; *Test connection* with an empty key returns the designed 400 and shows an inline error chip) |

Probe steps that passed everywhere: ⌘K palette (`rep` → "Go to Reports", Esc closes), `?` shortcuts sheet (10 rows, Esc closes), `[`/`]` sidebar (full→rail→full at 1440; stays hidden at 390), `g t`/`g d` navigation, every `.seg-btn`/`.tab` switch, transactions search "amazon" (29 rows, summary "Spent $1,690.49"), first-row detail drawer (opens "Rent Lakeside Apts", Esc closes, no unsaved-note write), all non-destructive top-level buttons (each layer they opened closed with Esc: Add transaction, Add account, Add user, Add category, New rule, New account (import), account/category multi-filters, month picker menu, user menu, Export CSV, Refresh AI status, Show key, Browse models). Buttons skipped by the deny list: Reset defaults, Run all rules, Re-detect merchant names, Learn from history, Pair all confident, Generate insights, and every row/card action inside lists.

## Console / network error table

| page | persona | theme | vp | phase | kind | message | source |
|---|---|---|---|---|---|---|---|
| settings | admin | light | 1440 | probe: Test connection (empty key) | http 400 + browser console line | `POST /api/settings/openrouter/test` → 400 "Failed to load resource" | designed outcome; UI shows inline `.chip-err` |
| settings | admin | dark | 390 | probe: Test connection (empty key) | http 400 + browser console line | same | same |
| (logged-out redirects, all 10 pages) | anon | light | 1440 | load | http 401 | `GET /api/auth/me` → redirect to `/login.html?next=…` | expected |
| any other | — | — | — | — | — | **none**: 0 console errors/warnings, 0 pageerrors, 0 unhandled rejections, 0 requestfailed, 0 unexpected 4xx/5xx across 84 loads and 331 probe steps | |

Bad-text scan (`undefined|NaN|null|[object Object]|$NaN|−NaN|Invalid Date` in text nodes, `title`, `aria-label`, `placeholder`): 0 occurrences on any page/persona/theme/viewport, including inside the transaction drawer.

Slow responses (> 1.5 s): none. Skeletons stuck after 8 s: none.

## Performance table (light theme, 1440×900, one cold context per row)

"Skeletons clear" includes a fixed harness floor of ~1.0 s (150 ms + 500 ms network-idle quiet + 450 ms animation settle); actual data render is well under 1 s everywhere. Bytes are transfer sizes (nginx gzip). DCL/load are the navigation-timing marks.

| page | persona | DCL ms | load ms | skeletons clear ms | requests | KB | API calls | dup API | largest JS (gz) | largest CSS (gz) | Chart.js | canvases |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| categories | admin | 19 | 26 | 986 | 21 | 494 | 6 | — | chart.umd.js 81KB | app.css 11KB | yes | 0 |
| categories | qa_tester | 18 | 25 | 985 | 21 | 493 | 6 | — | chart.umd.js 81KB | app.css 11KB | yes | 0 |
| import | admin | 19 | 26 | 987 | 18 | 416 | 5 | — | import.js 16KB | app.css 11KB | no | 0 |
| import | qa_tester | 17 | 24 | 984 | 18 | 415 | 5 | — | import.js 16KB | app.css 11KB | no | 0 |
| index | admin | 22 | 27 | 1047 | 24 | 500 | 7 | — | chart.umd.js 81KB | app.css 11KB | yes | 6 |
| index | qa_tester | 21 | 28 | 996 | 22 | 496 | 5 | — | chart.umd.js 81KB | app.css 11KB | yes | 6 |
| insights | admin | 21 | 28 | 1002 | 18 | 405 | 5 | — | ui.js 9KB | app.css 11KB | no | 0 |
| insights | qa_tester | 21 | 28 | 1000 | 18 | 403 | 5 | — | ui.js 9KB | app.css 11KB | no | 0 |
| login | anon | 17 | 26 | 0 | 10 | 381 | 0 | — | ui.js 9KB | app.css 11KB | no | 0 |
| reports | admin | 20 | 27 | 1018 | 24 | 500 | 7 | — | chart.umd.js 81KB | app.css 11KB | yes | 1 |
| reports | qa_tester | 21 | 27 | 999 | 24 | 499 | 7 | — | chart.umd.js 81KB | app.css 11KB | yes | 0 |
| review | admin | 16 | 24 | 994 | 20 | 411 | 7 | — | ui.js 9KB | app.css 11KB | no | 0 |
| review | qa_tester | 16 | 23 | 990 | 20 | 408 | 7 | — | ui.js 9KB | app.css 11KB | no | 0 |
| rules | admin | 20 | 27 | 987 | 18 | 411 | 5 | — | rules.js 11KB | app.css 11KB | no | 0 |
| rules | qa_tester | 19 | 27 | 981 | 18 | 410 | 5 | — | rules.js 11KB | app.css 11KB | no | 0 |
| settings | admin | 16 | 23 | 975 | 17 | 408 | 4 | — | settings.js 11KB | app.css 11KB | no | 0 |
| settings | qa_tester | 16 | 23 | 975 | 17 | 408 | 4 | — | settings.js 11KB | app.css 11KB | no | 0 |
| statements | admin | 15 | 23 | 977 | 17 | 401 | 4 | — | ui.js 9KB | app.css 11KB | no | 0 |
| statements | qa_tester | 15 | 23 | 976 | 17 | 399 | 4 | — | ui.js 9KB | app.css 11KB | no | 0 |
| transactions | admin | 18 | 25 | 984 | 18 | 420 | 5 | — | transactions.js 18KB | app.css 11KB | no | 0 |
| transactions | qa_tester | 18 | 26 | 987 | 18 | 419 | 5 | — | transactions.js 18KB | app.css 11KB | no | 0 |

~380 KB of every page is the Inter variable font (352 KB, immutable-cached by nginx); the JS/CSS payload per page is 40–130 KB gzipped. No duplicate API URL on any page load. API calls per page load (admin):

- **index** (7): `/api/auth/me`, `/api/review/count`, `/api/accounts?all=1`, `/api/reports/dashboard?range=this-month`, `/api/categories`, `/api/reports/by-category?from=…&level=sub` ×2 (current + previous period, distinct URLs)
- **transactions** (5): `/api/auth/me`, `/api/review/count`, `/api/categories`, `/api/accounts?all=1`, `/api/transactions?range=this-month&sort=-date&limit=100`
- **review** (7): `/api/auth/me`, `/api/review/count`, `/api/categories`, `/api/accounts?all=1`, `/api/settings/client`, `/api/transactions/transfer-candidates`, `/api/review?mode=merchant&limit=100`
- **import** (5): `/api/auth/me`, `/api/review/count`, `/api/settings/client`, `/api/accounts?all=1`, `/api/categories`
- **statements** (4): `/api/auth/me`, `/api/accounts/institutions`, `/api/review/count`, `/api/statements`
- **categories** (6): `/api/auth/me`, `/api/review/count`, `/api/accounts?all=1`, `/api/categories`, `/api/reports/by-category?range=this-month&level=sub`, `…&level=top`
- **rules** (5): `/api/auth/me`, `/api/review/count`, `/api/accounts?all=1`, `/api/rules`, `/api/categories`
- **reports** (7): `/api/auth/me`, `/api/review/count`, `/api/accounts?all=1`, `/api/reports/by-category?range=this-month&level=sub`, `/api/reports/monthly?months=12`, `/api/categories`, `/api/reports/by-category?from=…prev…&level=sub`
- **insights** (5): `/api/auth/me`, `/api/review/count`, `/api/accounts?all=1`, `/api/categories`, `/api/insights?month=2026-09`
- **settings** (4): `/api/auth/me`, `/api/accounts/institutions`, `/api/review/count`, `/api/accounts?all=1`

## Auth and theme behaviours (observed)

- Logged-out visit to each of the 10 pages (with `?x=1`) → `/login.html?next=<path incl. query>` after a single 401 on `/api/auth/me`; no page script proceeds (F: none).
- Wrong password: inline `#login-error` (`role="alert"`) shows "Invalid username or password", stays visible (checked after 3 s), no toast, focus and selection return to the password field. Correct password then honours `next=/reports.html?tab=trend`.
- Sign out (user menu) → `POST /api/auth/logout` → `/api/auth/me` returns 401 → Back button lands on `/login.html?next=…` with 0 transaction rows rendered (bfcache is disabled by Playwright, so this only shows the non-bfcache path). **sessionStorage is not cleared** — see F1.
- Theme: with `localStorage.ispend.theme=dark` the first paint (DOMContentLoaded) is already `rgb(12,14,19)` with `html[data-theme=dark]` and identical after load (no flash). Toggling via `#tb-theme` persists across reload and across pages. With nothing stored and `prefers-color-scheme: dark|light` emulated, `html` has no `data-theme`, `data-theme-mode=system`, and the body background follows the OS setting. **Saved server preference on a fresh browser flashes** — see F4.

## Findings

### [P1] Cached reference data of the previous user survives logout and is served to the next user (cross-user data leak in the browser)
- **Area:** security
- **Where:** `frontend/js/store.js:14-33` (sessionStorage cache `ispend.cache.*`, fresh-within-TTL entries are returned without revalidation), `frontend/js/nav.js:156` (Sign out only calls `/api/auth/logout` and redirects), `frontend/js/pages/login.js:22-24` (login does not clear storage)
- **What:** Storage keys are not namespaced by user and nothing clears them on sign-out or sign-in. Test `test_auth_logout_back_and_storage_residue`: admin logs in, opens Transactions, signs out (`/api/auth/me` → 401 afterwards) — sessionStorage still holds `ispend.cache.accounts`, `ispend.cache.categories`, `ispend.period`. qa_tester (who owns **no** accounts) then logs in in the same tab and lands on Transactions: `sessionStorage.ispend.cache.accounts` still contains admin's `["Amex Gold","Chase Checking","Chase Sapphire","Demo Checking","RBC Chequing"]`, and the **Accounts filter popover lists those five admin accounts** (`qa_filter_accounts` in `smoke-results.jsonl`, record `auth:logout-back-storage`). Because `store.get` returns entries younger than the TTL (60 s categories/accounts, 300 s settings) without revalidating, the wrong user's categories, account names/colours/currencies and AI-settings flags stay in the UI for up to 1–5 minutes, and `ispend.period`/`ispend.q:*` carry one user's filters into the next session. Shared computers / support staff switching accounts are the realistic exposure.
- **Repro:** Log in as admin → `/transactions.html` → user menu → Sign out → log in as qa_tester → `/transactions.html` → click "All accounts": admin's accounts are listed. `JSON.parse(sessionStorage.getItem('ispend.cache.accounts')).data` shows admin's rows.
- **Fix:** On successful login (`login.js`) and in the Sign out handler (`nav.js`) remove every `ispend.*` key from sessionStorage (cache, `q:*`, `period`, `review.skipped`) and the per-user localStorage keys (`ispend.importAccount`, `ispend.recentCats`, `ispend.catCollapsed`, `ispend.breakdown.*`). Additionally namespace the store cache by user id (`ispend.cache.<userId>.<key>`) and have `initNav` invalidate entries whose user id differs from `/api/auth/me`, so a stale tab can never serve another user's data even without a clean logout.

### [P2] One click on the Transactions date-range button opens two stacked date pickers
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:41` (`$('#f-range').addEventListener('click', () => dateRangePicker(...))`) and `:127` (`mountRangeButton($('#f-range'), …)` inside `paintToolbar()`, which also sets `btn.onclick` to open a picker)
- **What:** Both handlers fire on every click, so two identical `.popover` menus are created on top of each other and `ui.layers` has two entries. The visible (top) picker works, but the lower one stays open after a preset is picked in the top one only if it was dismissed first; Escape has to be pressed twice; with real pointer hit-testing the lower copy intercepts events (Playwright's strict click on the first copy timed out after 30 s for exactly that reason). Probe `range:*` on transactions recorded "one click on #f-range opened 2 popovers" for all 7 presets on both viewports (`probe-transactions-admin-light-1440.png` shows the final state; the API and the selected range are otherwise correct). Reports (`#range-btn`, only `mountRangeButton`) behaves correctly — one popover.
- **Repro:** `/transactions.html` → click "This month" → `document.querySelectorAll('.popover').length === 2`, `ui.layers.length === 2`; press Escape once → one picker remains.
- **Fix:** Delete line 41; `mountRangeButton` in `paintToolbar()` already wires the trigger (its `onChange` calls `periodSet` + `applyFilters`).

### [P2] Dashboard and Insights scroll horizontally on a 390 px viewport (populated data)
- **Area:** css
- **Where:** `frontend/css/app.css:83-88` (`.grid…{ grid-template-columns: 1fr … }`, collapsing to `1fr` at ≤960 px) with children whose min-content is wider than the column: `#recent-list`/`#attention` cards on `/index.html` (`.grid-1-1`), `#recurring .tbl-wrap` inside `.ins-grid` on `/insights.html` (`.grid-2-1`)
- **What:** `1fr` = `minmax(auto, 1fr)`, so the track grows to the child's min-content width: `document.documentElement.scrollWidth` is 434 px on the dashboard and 490 px on insights at a 390 px viewport (admin data; qa_tester's empty state is fine). Whole page pans sideways, cards are cut at the right edge. Screenshots: `smoke-index-admin-light-390.png`, `smoke-index-admin-dark-390.png`, `smoke-insights-admin-light-390.png`, `smoke-insights-admin-dark-390.png` (image width 434/490 instead of 390). Injecting either rule below brought both pages back to exactly 390 px (verified in-browser).
- **Repro:** Log in as admin, viewport 390×844, open `/index.html` → swipe horizontally / `document.documentElement.scrollWidth`.
- **Fix:** `app.css`: `.grid > * { min-width: 0; }` (or use `minmax(0, 1fr)` in every `.grid-*` rule, including the ≤960 px fallback).

### [P2] Light→dark flash on first paint for users whose saved preference is dark (new browser / cleared storage)
- **Area:** ux
- **Where:** `frontend/js/nav.js:143` (`if (prefs.theme && !Theme.hasStored()) Theme.set(prefs.theme)` after `await api('/api/auth/me')`), `frontend/js/pages/login.js:22-24`
- **What:** `theme.js` can only honour `localStorage`; the server preference is applied after the auth round-trip, so the page paints light first. Test `test_theme_server_preference_first_paint` (qa_tester, preference `theme=dark`, no stored choice, OS light): at DOMContentLoaded `body` is `rgb(245,246,248)` with no `data-theme`; after load it is `rgb(12,14,19)`/`dark`. Every page on a fresh browser flashes once until `Theme.set` writes localStorage. (Preference restored to `system` afterwards.)
- **Repro:** Settings › Appearance → Dark as any user, then open the app in a private window and log in: the first page paints light, then switches.
- **Fix:** The login response already carries `preferences` (`auth.py:_me_payload`): in `login.js`, after a successful `POST /api/auth/login`, call `Theme.set(res.preferences.theme)` / `Theme.setDensity(res.preferences.density)` when present *before* redirecting, so the stored choice exists for the next page's synchronous `theme.js`. Keep the `nav.js` fallback for sessions restored from cookies.

### [P3] Mobile off-canvas navigation cannot be closed with Escape and is not part of the layer stack
- **Area:** a11y
- **Where:** `frontend/js/nav.js:101-107` (`openMobile` adds `body.sidebar-open`; only the backdrop, a nav link or the collapse button close it), `frontend/js/ui.js:31-36` (Esc closes `ui.layers[0]` only)
- **What:** After tapping the topbar menu button (`#tb-menu`, "Open menu") or the bottom-nav "More" (`#bn-more`) at 390 px, Escape does nothing; focus is not moved into the drawer and the rest of the page is not made inert, unlike every `ui.modal`/`ui.drawer`. Probes `button:Open menu` / `button:More` on all 10 pages at dark/390: `{'layers': 0, 'dom': 0, 'sidebarOpen': True}` after 4× Escape.
- **Repro:** Viewport ≤ 768 px → tap ☰ → press Esc.
- **Fix:** In `openMobile` push a handle onto `ui.layers` (`{ close: closeMobile, allowShortcuts: false }`), call `ui.focusFirst(sidebar)`/`ui.trapFocus`, set `inert` on `.shell`/`.bottomnav`, and pop it in `closeMobile`; restore focus to the opener.

### [P3] "Merchant" column header is marked sortable but only sorts ascending
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:116-122` (`toggleSort`: `merchant` always → `'merchant'`), `backend/transactions_api.py:28-34` (`SORTS` has no `-merchant`)
- **What:** `th.col-merchant.sortable` toggles Date and Amount between ascending/descending but a second click on Merchant leaves `aria-sort="ascending"` (probe `sort:1`: `['none', 'ascending', 'ascending']`). Inconsistent affordance; screen readers announce a sortable header that never changes direction.
- **Repro:** `/transactions.html` → click "Merchant" twice.
- **Fix:** Add `"-merchant": ("t.merchant_name DESC, t.id DESC", "<")` to `SORTS` (and handle it in `encode_cursor`/`decode_cursor`/`cursor_clause`, which currently test `sort == "merchant"`), then `tx.filters.sort = cur === 'merchant' ? '-merchant' : 'merchant'` in `toggleSort`.

### [P3] Chart.js (208 KB, 81 KB gz) is downloaded and parsed on Categories although no chart exists until a category is selected
- **Area:** perf
- **Where:** `frontend/categories.html:44-45` (`/vendor/chart.umd.js`, `/js/charts.js`), `frontend/js/pages/categories.js:198-224` (`loadSideTrend` is the only consumer)
- **What:** Performance table: categories loads Chart.js with `canvases = 0` for both personas; it is the largest asset after the font on that page (81 KB of the 494 KB total). Also loaded on Reports for an empty user with 0 canvases (acceptable — the chart appears on data).
- **Repro:** Open `/categories.html` → Network: `chart.umd.js` transferred; `document.querelectorAll('canvas').length === 0`.
- **Fix:** Lazy-load: in `renderSide()` inject `<script src="/vendor/chart.umd.js">` + `/js/charts.js` on first selection (a small `loadScript()` helper in `api.js`), or drop the side sparkline and link to Reports. `charts.js` already guards `typeof Chart === 'undefined'`.

### [P3] Transactions status filter is clipped at the right edge on mobile with no scroll affordance
- **Area:** ux
- **Where:** `frontend/css/pages/transactions.css:82` (`.tbl-toolbar .seg { max-width: 100%; overflow-x: auto; }`), `frontend/transactions.html` (`#f-status`)
- **What:** At 390 px the segmented control reads "All · Uncategorized · Suggested 9 · Transfers · Exc" — the "Excluded" option is cut mid-word and there is no visual hint that the row scrolls (scrollbars are hidden on iOS). Screenshot: `smoke-transactions-admin-dark-390.png`. Not a functional defect (the row does scroll), but the last filter is undiscoverable.
- **Repro:** Viewport 390 → `/transactions.html`.
- **Fix:** Either wrap the seg (`flex-wrap: wrap` for `.tbl-toolbar .seg` ≤ 480 px), or add an edge fade (`mask-image: linear-gradient(90deg, #000 90%, transparent)`) plus `scroll-snap-type: x proximity` so the overflow is perceivable.

## Summary

- **Counts:** P0 0 · P1 1 · P2 3 · P3 4 (8 findings). 80/84 page loads clean, 22/36 behaviour/probe tests clean; the 14 failing tests all trace to F1–F6 above. Zero console errors, page errors, unhandled rejections, failed requests, unexpected 4xx/5xx, slow (>1.5 s) responses, stuck skeletons or `undefined/NaN/null` text across 84 loads × 2 personas × 2 themes × 2 viewports and 331 read-only probe steps.
- **Top 5:**
  1. **[P1] Cross-user cache leak** — sessionStorage `ispend.cache.*`/`ispend.period` survive logout; qa_tester's account filter lists admin's five accounts right after signing in (`store.js`, `nav.js` Sign out, `login.js`). Clear `ispend.*` on login/logout and namespace the cache by user id.
  2. **[P2] Double date-range popover on Transactions** — `transactions.js:41` duplicates the `mountRangeButton` handler at `:127`; two pickers open per click, Escape needed twice, lower copy intercepts clicks.
  3. **[P2] Horizontal page overflow at 390 px on Dashboard (434 px) and Insights (490 px)** — `.grid` `1fr` tracks inherit min-content; `.grid > * { min-width: 0 }` verified to fix both.
  4. **[P2] Theme flash for saved dark preference on a fresh browser** — first paint light, switches after `/api/auth/me`; apply `preferences.theme` from the login response before redirecting.
  5. **[P3] Mobile off-canvas nav ignores Escape / not a layer** (all pages) — register it in `ui.layers` with focus trap and inert.
- Suite: `qa/e2e/test_smoke.py` + `helpers.py` + `smoke_fixtures.py` + `make_report.py`; results `qa/reports/smoke-results.jsonl`; 104 screenshots in `qa/reports/screenshots/` (`smoke-*`, `probe-*`).
