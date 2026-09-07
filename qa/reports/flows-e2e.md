# iSpend — end-to-end user flows (F1–F12), Playwright through the real UI

Test file: `qa/e2e/test_flows.py` (pytest, Python Playwright sync API, headless chromium, 1366×900 + a 390×844 mobile context).
Screenshots: `qa/reports/screenshots/flow-<F#>-<step>.png`. Machine-readable observations of the last run: `qa/reports/flows-e2e-notes.json`.

## How to run

```bash
# venv from qa/CONTEXT.md; app must be up at http://localhost:5559 (admin/admin is only used to (re)create the QA user)
V=/private/tmp/claude-501/-Users-ruolez-Desktop-Dev-ispend/272cd059-cdbc-40ed-a58b-b1f0ef765b8f/scratchpad/qa-venv/bin
$V/pytest qa/e2e/test_flows.py -p no:cacheprovider -v --tb=short        # ~2.5 min, 12 ordered tests
```

- The suite is self-contained (own browser fixture, no dependency on `conftest.py` fixtures) and ORDERED: later flows rely on the data created by earlier ones.
- Setup deletes and re-creates the user `qa_flows` (password `qa-flows-pass1`) through the admin API, so every run starts from an empty account with the default category tree. Admin data is never touched; `qa_tester` is only logged into in F11 (read-only).
- Every flow uses soft checks: one defect does not stop the flow; the test fails at the end listing every failed check. A failing check in the last run = a defect below (each was confirmed against the source or the API, not a selector problem).

## Flow results (last run: 9 failed / 3 passed — every failure is a listed defect)

| Flow | Result | Findings hit |
|---|---|---|
| F1 First run (login states, Enter submit, `?next`, empty dashboard + CTA) | PASS | #14 (note) |
| F2 Accounts (create ×4, duplicate name, rename, archive/restore, delete with txns) | FAIL | #13 |
| F3 Import (Chase, Amex, xlsx mapping, headerless, `;` CSV, duplicates, invalid files, 3 files, Statements page: open/download/reparse/delete/rollback) | FAIL | #3 #10 #11 #12 #21 |
| F4 Transactions (totals, every filter → URL, sort, infinite scroll to 130/130, drawer picker/create/note/transfer, bulk + undo, CSV export, `?` sheet + every claimed shortcut) | FAIL | #4 #5 #16 #17 #22 |
| F5 Review (pill = API, groups, keyboard resolve, "always" → rule, skip/unskip, single mode, expand) | PASS | (#2 observed) |
| F6 Rules (contains/starts/equals/regex, invalid regex, preview = API, reorder menu/Alt+↑/drag, edit, inline edit, toggle, test drawer, delete, rule applied on next import) | FAIL | #6 |
| F7 Categories (create parent/child, colour/icon, rename, reorder, merge, delete in use / empty, cache invalidation, second tab) | FAIL | #13 #18 |
| F8 Reports (5 tabs draw pixels, legend isolate, range → numbers, income flow, compare months incl. empty previous, cash flow, recurring, transfer candidates + pair all) | FAIL | #2 #19 #20 |
| F9 Insights (AI-off copy, anomalies new-merchant + duplicate, dismiss + undo, recurring row, month nav) | FAIL | #9 |
| F10 Settings (theme/density persisted + applied in a fresh browser, currency, password wrong/short/mismatch/success, AI tab as user, admin items hidden) | FAIL | #7 #15 |
| F11 Multi-user isolation in one browser (sign out, log in as qa_tester) | FAIL | #1 |
| F12 Mobile 390×844 (bottom nav, import lite, transactions lite, range picker, drawer, floatbar, More menu) | PASS (see #8, observed with a manual probe) | #8 |

Numbers cross-checked and correct: transactions totals bar = `/api/transactions` sums and = sum of visible non-transfer rows; CSV export rows = total; review pill = `/api/review/count`; rule preview counts = `/api/rules/preview`; top merchant/total, compare totals, cash-flow KPIs = their `/api/reports/*` endpoints; `summary?include_transfers=1` for Aug 2024 = independently computed fixture sums (out 1,864.93 / in 9,550.00). Performance: no page load or action over 2 s; upload→"Ready" is ~1.95 s for every file because the import page polls every 1.5 s (`import.js:138`), i.e. the poll interval, not parsing, dominates.

---

### [P1] Signing out does not clear sessionStorage: the next user in the same browser gets the previous user's categories, filters and period
- **Area:** security
- **Where:** `frontend/js/nav.js:132` (Sign out only calls `/api/auth/logout` and navigates); `frontend/js/store.js:8-15` (`ispend.cache.*` in sessionStorage, not keyed by user); `frontend/js/api.js:75-113` (`ispend.q:*` query memory); `frontend/js/pickers.js:212` (`ispend.period`); `review.js:2` (`ispend.review.skipped`)
- **What:** After Sign out the keys `ispend.cache.categories/accounts/settings/institutions`, `ispend.q:/transactions.html`, `ispend.q:/reports.html`, `ispend.period`, `ispend.review.skipped` are all still present (`flow-F11-signed-out.png`). Logging in as `qa_tester` in the same tab: the sidebar Transactions link is `/transactions.html?acct=221` (qa_flows' account id), the account filter button reads "QA Checking" (qa_flows' account name, from the cached accounts list), and the category filter lists qa_flows' private categories "Streaming QA", "QA Parent Renamed", "QA Custom Cat" (`flow-F11-tester-category-filter.png`). `store.get` serves the cached entry for up to 60 s (categories) / 300 s (settings) before revalidating, so another user's reference data is rendered in pickers, filters and chips. Also `localStorage.ispend.recentCats` / `ispend.importAccount` hold the previous user's ids.
- **Repro:** log in as user A, open Transactions with an account filter, open the category filter; user menu → Sign out; log in as user B; open Transactions → filter button shows A's account name, category filter shows A's categories.
- **Fix:** on logout (and on `/api/auth/me` returning a different user id than the one the cache was written for) clear `sessionStorage` keys with the `ispend.` prefix and the per-user `localStorage` keys; namespace `store` keys by user id (`ispend.cache.<uid>.categories`).

### [P1] Built-in "suggested" transfers are excluded from spending/income before anyone confirms them
- **Area:** backend
- **Where:** `backend/importer/pipeline.py:423-424` (`is_transfer = d.is_transfer`, `is_excluded = … or is_transfer` for a `status="suggested"` decision), `backend/categorizer.py:9` (builtin hint → `Decision(status="suggested", source="builtin")`), `backend/builtin_hints.py:9-19`
- **What:** After importing `amex.csv`, the row "AUTOPAY PAYMENT - THANK YOU" has `category_status=suggested, source=builtin, is_transfer=true, is_excluded=true` (API, F8 notes). The same holds for the Chase "Payment Thank You-Mobile" +450 and the checking "Online Payment … To Chase Card" −450 before they were paired: pairing them via Review › Transfers changed nothing in `/api/reports/summary` (1,414.93 / 8,500.00 before and after) because the unconfirmed suggestion had already removed them from every report. Settings › AI says "Suggestions are never applied without your confirmation" and the Review queue still lists these rows as needing a decision, yet the dashboard/reports already treat them as confirmed transfers. A wrong hint (e.g. a merchant containing "THANK YOU") silently removes real spending or income from every number.
- **Repro:** import `backend/tests/fixtures/amex.csv` → `GET /api/transactions?q=AUTOPAY` → `is_excluded:true` while `category_status:"suggested"`; Reports › Cash flow income for Aug 2024 omits the 600.
- **Fix:** only set `is_transfer/is_excluded` at import when the decision is `confirmed` (rule / merchant memory / manual); for `suggested` keep the flags false and let Review's Accept/Transfer set them. Alternatively surface suggested transfers as a separate "(unconfirmed)" bucket in totals.

### [P2] "Skip N duplicates" switch on the import preview is a dead control
- **Area:** frontend-js
- **Where:** `frontend/js/pages/import.js:329` (switch) and `:399` (`setRows(all, only:'dupes', include:!checked)`); `backend/statements_api.py:96` (`included` counts only `duplicate_of IS NULL`); `backend/importer/pipeline.py:397-400` (commit skips fingerprints that already exist regardless of `include`)
- **What:** Re-uploading `chase_card.csv` into the same account flags all 6 rows Duplicate, "0 to import · 6 duplicates", Import disabled (`flow-F3-duplicates-review.png`). Turning "Skip 6 duplicates" off flips every row's `include` flag to true (API), but `summary.included` stays 0, the summary still says "0 to import · 6 duplicates", the footer "Nothing selected to import", and Import stays disabled (`flow-F3-duplicates-unskipped.png`). The row checkboxes for duplicates likewise do nothing visible. The control promises something the backend never does.
- **Repro:** import a CSV twice into the same account → preview → toggle "Skip N duplicates" off.
- **Fix:** either remove the switch and the per-row checkbox for duplicate rows (duplicates are always skipped, say so in the summary), or make `included`/commit honour `include` for duplicates and show "N duplicates will be imported again".

### [P2] Transactions date-range button opens two stacked popovers
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:41` (`addEventListener('click', … dateRangePicker)`) and `:127` (`mountRangeButton` sets `btn.onclick` → a second `dateRangePicker`)
- **What:** One click on the range button creates two `.popover` layers (`ui.layers.length === 2`, `flow-F4-range-double-popover.png`, `flow-F4-debug-double-popover.png`). Picking a preset closes one; the other stays open on top of the reloaded list until Esc/outside click, and while any layer is open every page shortcut (j/k/x/c…) is disabled. Playwright's strict locator caught it as "resolved to 2 elements".
- **Repro:** Transactions → click the range button → two identical menus, one offset behind the other; press Esc once → one remains.
- **Fix:** drop the `addEventListener` at line 41 (mountRangeButton already wires `onclick`), or stop mountRangeButton from assigning `onclick` when a handler exists.

### [P2] Drawer loses History, "Other charges" and "Imported from" after changing the category in the drawer
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:453` (`Object.assign(local, it, { events: undefined, merchant_others: undefined, statement: undefined })`) and `:523` (`const u = tx.byId.get(it.id); if (u) Object.assign(it, u); d.setBody(drawerHtml(it))`)
- **What:** Opening the NETFLIX drawer shows the timeline; after picking a category from the drawer chip the drawer re-renders with "No history recorded." and without the other-charges/statement sections (F4 notes: history `[]` while open; after reopening: 7 events incl. "Note updated", "Marked as transfer"). Line 453 writes `events: undefined` onto the list item, line 523 copies that back onto the drawer item. Notes and transfer edits made afterwards also never appear in the open drawer (`flow-F4-drawer-transfer.png`).
- **Repro:** Transactions → open any row → click the category chip → pick a category → History section is gone until the drawer is reopened.
- **Fix:** in the `pick` handler copy only the category fields (`category_id/status/source/confidence`) onto `it`, or refetch `/api/transactions/:id` before `setBody`.

### [P2] Rule "Apply to existing · Only uncategorized" skips rows with an unconfirmed suggestion, while the preview counts them
- **Area:** backend
- **Where:** `backend/rules_api.py:18-19` (`_scan(only_uncategorized)` = `category_id IS NULL`), `backend/rules_api.py:155` (preview uses `only_uncategorized=False` by default), `backend/review_api.py:17` (queue = `category_id IS NULL OR category_status='suggested'`)
- **What:** New rule "contains UBER" → Dining › Delivery: the modal preview says "Matches 1 existing transaction · showing 1" with both switches on (`flow-F6-contains.png`); after "Create rule" the toast is just "Rule created" and `GET /api/transactions?q=UBER` still has `category_status=suggested, source=builtin`. Review lists the row as needing a category, yet the rule engine considers it categorized. The same mismatch affects "Run all rules" (`rules.html`) and `/api/rules/run`. With built-in hints suggesting a category for most rows (6/6 Chase rows, 5/5 Amex rows), a user's new rule rarely applies to anything.
- **Repro:** import `chase_card.csv`; Rules → New rule → contains `UBER` → any category → Create (Apply + Only uncategorized on) → the UBER row is unchanged.
- **Fix:** treat `category_status='suggested'` as uncategorized in `_scan` (`category_id IS NULL OR category_status = 'suggested'`) so rules override unconfirmed suggestions, and make the preview show the count for the same filter the apply will use.

### [P2] Display currency CAD renders exactly like USD — the preference has no visible effect
- **Area:** frontend-js
- **Where:** `frontend/js/format.js:20` (`currencyDisplay: 'narrowSymbol'`), Settings › Appearance › Display currency (`settings.js:286`)
- **What:** With preference `currency=CAD` the dashboard total is "$1,976.89" and in the same browser `Intl … CAD narrowSymbol` → "$5.00", identical to USD (`flow-F10-currency-cad.png`, F10 notes). A user with a CAD and a USD account cannot tell rows or totals apart; the "Mixed currencies" badge is the only hint. The toast "Totals now shown in CAD" describes a change nothing displays.
- **Repro:** Settings → Appearance → Display currency: CAD → Dashboard.
- **Fix:** use `currencyDisplay:'symbol'` ("CA$") when the currency differs from the locale's default/the account currency, or append the ISO code when more than one currency is in play.

### [P2] Mobile (390 px): dashboard and insights are wider than the viewport, so the page pans horizontally and the bottom nav lands off-screen under mobile emulation
- **Area:** css
- **Where:** `frontend/css/breakdown.css` (`.bd-table` / `.bd-wrap` on the dashboard "Category breakdown"), `frontend/css/pages/insights.css` (`.ins-grid .col`), `frontend/css/app.css:541-561`
- **What:** With a plain 390 px viewport `document.documentElement.scrollWidth` is 409–430 on `/index.html` (`table.tbl.bd-table right=504px`) and 405 on `/insights.html` (`div.col.gap-4 right=405`) — measured with the probe in F12 and a manual script. Under Chromium mobile emulation the layout viewport therefore grows to 430×931 and the fixed `.bottomnav` is rendered outside the 390×844 visual viewport (`flow-F12-debug-bottomnav-mobile.png`: no bottom bar; `flow-F12-debug-bottomnav-desktopvp.png`: same page without emulation shows it). Playwright could not tap "Import" in the bottom nav under emulation because the point resolved to the chart canvas.
- **Repro:** phone or DevTools device mode at 390 px → Dashboard with data → page scrolls sideways; bottom nav not at the bottom until you zoom.
- **Fix:** give `.bd-wrap`/`.tbl-wrap` `overflow-x:auto; max-width:100%` and `min-width:0` on the grid children (`.grid-2-1 > *`, `.ins-grid .col`); add `overflow-x:hidden` on `html,body` as a safety net.

### [P2] Import: mapping-editor role change made no visible change, and a Debit column with negative numbers yields zero readable rows (cause unverified)
- **Area:** frontend-js
- **Where:** `frontend/js/pages/import.js:397` (`scheduleMapping` on `.mapping-table select[data-col]`), `:347-360` (`readMappingFromDom`); `backend/importer` amount parsing for `debit` columns
- **What:** On the generic `generic.xlsx` preview (`flow-F3-xlsx-review.png`) changing column 3 from "Amount" to "Debit" in the mapping table left the preview, the sign summary and the "Column mapping · date, description, amount" line unchanged, and `GET /api/statements/<id>` still had `mapping.amount=2, debit=null` afterwards — the change never reached the server (no re-parse) — `flow-F3-xlsx-mapping-changed.png`. Issuing the same mapping through `PUT /api/statements/<id>/mapping` does re-parse, but then all 3 rows become unreadable: charges 0 / payments 0 (a Debit column containing `-4.5, 3000, -120`). Unverified: whether the UI `change` event is lost only for `.xlsx` or in general; the API-side result is reproducible.
- **Repro:** import `backend/tests/fixtures/generic.xlsx` → Review → Column mapping → set the Amount column's role to Debit.
- **Fix:** confirm the change handler fires for the table selects (log the PUT), show an inline error when a mapping produces 0 valid rows instead of a silent empty preview, and treat negative values in a Debit column as debits (abs) rather than invalid.

### [P2] Failed parse shows a raw Python exception, and no message at all in the upload list
- **Area:** ux
- **Where:** `frontend/js/pages/import.js:97` (status 'error' → only "Failed" + Remove, no `error_message`), `backend/importer/csv_parser.py` (csv module error propagated as-is)
- **What:** Uploading a binary file named `garbage.csv`: the upload list shows "FAILED  Remove" with no explanation (`flow-F3-invalid-files.png`, F3 notes row 2), while the Statements page shows the message `new-line character seen in unquoted field - do you need to open the file with newline=''?` (`flow-F3-statements-list.png`) — Python's `csv` error text, meaningless to a user. `empty.csv` is fine ("The uploaded file is empty"); `.txt` is rejected client-side ("Unsupported file type").
- **Repro:** Import → choose any non-text file renamed to `.csv`.
- **Fix:** render `f.statement.error_message` in the file row (import.js:97) and map parser exceptions to "This file does not look like a CSV statement".

### [P3] "same file was uploaded before" hint disappears as soon as parsing finishes
- **Area:** backend
- **Where:** `backend/statements_api.py:169` (upload response includes `duplicate_of`), `backend/statements_api.py:27` `_statement_json`/`_detail` (GET omits it); `frontend/js/pages/import.js:103,147`
- **What:** `POST /api/statements` returns `duplicate_of: <previous statement id>` and the file row briefly shows "· same file was uploaded before"; the poller then replaces `f.statement` with the GET payload, which has no `duplicate_of`, so the warning vanishes (`flow-F3-chase-dup-uploading.png` vs the Ready state, F3 check "file list keeps the … hint"). API: `GET /api/statements/397` → `duplicate_of: null` right after the upload said 384.
- **Repro:** upload the same CSV twice; watch the file row.
- **Fix:** include `duplicate_of` in `_statement_json`, or keep the upload-time value when merging (`{...f.statement, ...s}` already does, but line 146 replaces the object for terminal states).

### [P3] "1 transactions" — count pluralisation in three confirmations
- **Area:** ux
- **Where:** `frontend/js/pages/settings.js:382` (delete account), `frontend/js/pages/categories.js:368` (merge toast), `frontend/js/pages/categories.js:385` (delete in-use dialog); `backend/categories_api.py:236` ("1 transactions still use…")
- **What:** "This deletes the account and its 1 transactions." (`flow-F2-account-delete-confirm.png`), "Merged into QA Parent Renamed (1 transactions moved)", "1 transactions use this category." (`flow-F7-delete-in-use.png`). `plural()` exists in `format.js` and is used elsewhere.
- **Repro:** delete an account / merge a category / delete a category holding exactly one transaction.
- **Fix:** use `plural(n, 'transaction')` in those three strings and in the API message.

### [P3] Login form has no password-visibility toggle
- **Area:** ux
- **Where:** `frontend/login.html:23`
- **What:** Password field is a plain `type=password` with no show/hide control (`flow-F1-login.png`); the rest of the app has the eye toggle pattern (`settings.js:118` for the API key).
- **Repro:** open `/login.html`.
- **Fix:** add the same `.input-group … .trailing` eye button used in Settings › AI.

### [P3] Silent actions: Archive/Restore account, theme/density change and colour/icon change give no feedback
- **Area:** ux
- **Where:** `settings.js:380` (archive/restore), `settings.js:287-288` (theme/density radios), `categories.js:269` (colour/icon popover)
- **What:** F2/F7/F10 notes: no toast after Archive, Restore, theme/density change or colour change — only the row/radio/icon repaints (`flow-F2-account-archived.png`). Every other mutation in the app toasts ("Account saved", "Renamed", …), so the inconsistency reads as "did it save?".
- **Repro:** Settings → account menu → Archive; Appearance → Dark.
- **Fix:** toast "Account archived · Undo", "Theme: Dark", "Colour updated" like the neighbours.

### [P3] "Clear filters" writes `range=this-month` into the URL and hides older data behind the filtered empty state
- **Area:** ux
- **Where:** `frontend/js/pages/transactions.js:110-115` (`clearFilters` → `writeUrl`), `:100` (`writeUrl` omits the default sort but not the default range)
- **What:** After Clear filters the URL is `/transactions.html?range=this-month` although first load shows no query for the same state (F4 notes). With only historical data the list then shows "No transactions match — Try widening the date range or clearing filters" (`flow-F4-filter-empty.png`), the same empty state as any filter, and "Clear filters" is already hidden because this-month counts as no filter — the user has to know to pick "All time".
- **Repro:** Transactions → All time → Clear filters.
- **Fix:** drop `range` when it equals the default in `writeUrl`; when the unfiltered current month is empty but other periods have rows, offer "Show all time" in the empty state.

### [P3] Merchant column can be sorted only one way
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:120` (`toggleSort`: merchant always `'merchant'`), `backend/transactions_api.py:28-34` (`SORTS` has no `-merchant`)
- **What:** Clicking the Merchant header a second time keeps ascending order (`flow-F4-sort.png`, F4 notes); Date and Amount toggle. `aria-sort` stays "ascending".
- **Repro:** Transactions → click "Merchant" twice.
- **Fix:** add `-merchant` to `SORTS` and toggle like the other columns.

### [P3] A second tab keeps the old category name for up to 60 s after a rename elsewhere
- **Area:** frontend-js
- **Where:** `frontend/js/store.js:19-25` (stale-while-revalidate, TTL 60 s; `invalidate` only clears the current tab's memory and the shared sessionStorage entry)
- **What:** Rename "Streaming" → "Streaming QA" in tab 1; tab 2 (Transactions, opened earlier) still offers "Streaming" in the category picker (`flow-F7-second-tab-stale.png`). Same-tab navigation is fine (transactions and rules pages showed the new name).
- **Repro:** two tabs, rename in one, open the picker in the other.
- **Fix:** listen to the `storage` event (sessionStorage is per tab, so use `BroadcastChannel`/localStorage ping) to invalidate `store` in other tabs, or shorten the TTL for categories.

### [P3] Recurring detection and the Cash-flow tab have hidden time windows; data older than ~2 years never appears and nothing says why
- **Area:** ux
- **Where:** `backend/reports.py:421` (`RECURRING_LOOKBACK_DAYS = 730`), `frontend/js/pages/reports.js:29` (cash flow only 6/12/24 months back from today), `insights.js:49`
- **What:** With the Aug-2024 fixture statements, Insights shows "No recurring charges yet … after a few months of statements" and the Cash-flow tab's 24-month window starts at Oct 2024 (`flow-F8-cashflow-24m.png`), so none of the imported months can be shown; the other report tabs accept any custom range. Seeding a NETFLIX series in the last 4 months is detected fine (monthly ×4). The empty-state copy blames insufficient data instead of the window.
- **Repro:** import only statements older than 24 months → Reports › Cash flow, Insights.
- **Fix:** mention the window in the empty states ("Looking at the last 24 months"), let Cash flow use the shared range picker, or derive the window from the data's date span.

### [P3] Reports › By category: the "Spending by month" chart ignores the range picker while the table below follows it
- **Area:** ux
- **Where:** `frontend/js/pages/reports.js:116-117` (monthly chart uses `state.months`, table uses `state.range`)
- **What:** Range "Last month" → table header "Categories · Last month" and a single row, while the chart above still shows 12 months (`flow-F8-category-empty-range.png`); the two cards can disagree on what period is being looked at. The 6m/12m/24m seg and the range button are both shown at once on this tab.
- **Repro:** Reports → By category → range: Last month.
- **Fix:** highlight the picked range in the chart (dim other bars) or title the chart "Last 12 months" explicitly.

### [P3] A Chase *card* CSV is auto-assigned to the Chase *checking* account by institution alone
- **Area:** ux
- **Where:** `backend/statements_api.py:48` `_suggest_account` (institution match), `frontend/js/pages/import.js:441-449` (`autoAccount`)
- **What:** First import of `chase_card.csv` was auto-assigned to "QA Checking" (institution = Chase) with the toast "Using QA Checking (matched from the statement) — change it above if that is wrong" (`flow-F3-chase-auto-account.png`); Import was immediately enabled. The detected profile is a card profile, the account type is checking — enough signal to not pre-select, or to say "matched by bank" rather than "from the statement".
- **Repro:** create a checking account with institution Chase, import `chase_card.csv`.
- **Fix:** require account-type compatibility (card profile ↔ credit_card) for the suggestion, or keep Import disabled until the user confirms an auto-picked account.

### [P3] After Esc from the category picker, Enter re-opens the picker instead of "Open details" as the `?` sheet claims
- **Area:** a11y
- **Where:** `frontend/js/pickers.js:105` (`onClose` refocuses the anchor chip), `frontend/js/ui.js:392-399` (`activating(e)` suppresses shortcuts when a button is focused)
- **What:** Shortcut sheet lists "↵ Open details" (`flow-F4-shortcuts-sheet.png`). Sequence `c` (picker) → Esc → Enter reopens the picker because focus was returned to the `.catchip` button; the row itself never receives focus (`tr[tabindex=-1]` is only highlighted). Other keys keep working.
- **Repro:** Transactions → j → c → Esc → Enter.
- **Fix:** after closing the picker from a keyboard row action, move focus to the focused `tr` (or blur), not the chip.

## Summary

- **P0:** 0 · **P1:** 2 · **P2:** 8 · **P3:** 10 (20 findings; flows: 3 pass / 9 fail, every failure maps to a finding above; nothing slower than 2 s).
- Most important:
  1. **Sign-out leaves sessionStorage/localStorage intact** — the next user in the same browser sees the previous user's category names, account filter and remembered period (P1, security).
  2. **Unconfirmed built-in "suggested" transfers are already excluded from spending/income** — reports and dashboard drop rows nobody confirmed, contradicting the "never applied without confirmation" promise (P1, wrong numbers).
  3. **"Skip N duplicates" switch is a dead control** — toggling it changes nothing; Import stays disabled (P2).
  4. **Range button opens two popovers** — stacked layers, shortcuts disabled until both are closed (P2).
  5. **Rules "Only uncategorized" skips suggested rows the preview counted**, so new rules rarely apply to anything after import; plus drawer History disappears after a category change and CAD renders as "$" (P2).
