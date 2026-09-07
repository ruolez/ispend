# iSpend frontend JS review (static audit)

Scope: every file under `frontend/js/`, `frontend/js/pages/`, every `frontend/*.html`, cross-checked against the backend blueprints (`transactions_api.py`, `review_api.py`, `statements_api.py`, `reports_api.py`/`reports.py`, `settings_api.py`, `rules_api.py`/`rules.py`, `merchants_api.py`, `auth.py`, `accounts_api.py`, `ai_api.py`, `categories_api.py`, `transfers.py`, `recurring.py`, `importer/pipeline.py`). Method: full read of each file, `node --check` on all 21 JS files (all pass), grep passes for `==`/`var`/`console.`/`TODO`/empty `catch`/inline handlers/un-awaited `api(`, an icon-name cross-check (all 122 `icon()` names and all `CATEGORY_ICONS` resolve), a JS-emitted-CSS-class cross-check, and a global-name collision scan (all collisions are page-to-page only; one page script per HTML, so none are live). Nothing was executed in the browser; items that could not be proven statically say "unverified".

Lint pass results: no `var` outside `theme.js` (intentional, pre-ES6 head script), no `==`/`!=` other than `== null` idioms, no `console.log` (one `console.error` in `store.js:50`), no TODO/FIXME, no inline `onclick=` attributes, no empty `catch {}` blocks (21 `catch { /* comment */ }` blocks, all intentional). Direct `el.onclick = …` property assignments exist in 17 places (listed under P3 "conventions") — they are re-assigned on each render so they do not leak, but they deviate from the README's delegate-on-`data-act` rule.

## Per-page table

| File | Lines | P0 | P1 | P2 | P3 | data-act emitted → handled | Orphans |
|---|---|---|---|---|---|---|---|
| pages/transactions.js | 733 | 0 | 1 | 6 | 12 | `reload`, `clear-filters`; `data-cat-pick/accept/reject/open/menu/select/bulk/dact/dflag/open-other` all handled | none |
| pages/import.js | 602 | 0 | 0 | 2 | 7 | new-account, remove-file, go-review, restart, reparse, discard, confirm-suggestions, flip-signs, ai-extract, rows-all, pick-cat, commit, undo-import | handler `pick-file` (import.js:413) never emitted |
| pages/settings.js | 419 | 0 | 1 | 2 | 6 | add-account, renormalize, learn-history, add-user, reload-accounts, edit-account, account-menu, reload-ai, toggle-key, open-models, test-ai, ai-refresh, ai-suggest, refresh-models, reload-users, user-menu, save-ai, discard-ai | none |
| pages/rules.js | 413 | 0 | 1 | 1 | 8 | reload, new-rule, run-all, test, menu, pick-cat, clear-cat-filter, reload-merchants; `data-mact` rename/pick/menu; `data-drawer-act` apply-unc/apply-all | none |
| pages/categories.js | 400 | 0 | 0 | 1 | 6 | reload, reset-defaults, add-category, add-sub, rename, color, menu, side-icon, side-color, side-merge, side-delete, grip, toggle | none |
| pages/review.js | 371 | 0 | 0 | 2 | 5 | reload, unskip; `data-cact` accept/pick/transfer/skip/expand/pattern; `data-pact` pair/skip; `data-cfield` always/ptype/pattern; `data-crow` | none |
| pages/dashboard.js | 358 | 0 | 0 | 1 | 5 | reload, drill-up | none |
| pages/reports.js | 317 | 0 | 0 | 2 | 8 | reload, export, pct, cmp-prev, cmp-next, vs-swap, vs-prev, vs-next, vs-previous, vs-year | handler `compare-go` (reports.js:84) never emitted |
| pages/statements.js | 146 | 0 | 0 | 0 | 2 | reload-statements, menu | none |
| pages/insights.js | 140 | 0 | 0 | 1 | 2 | reload, prev-month, next-month, generate, regenerate, dismiss-rec, dismiss-anom | none |
| pages/login.js | 33 | 0 | 0 | 1 | 1 | n/a | n/a |
| ui.js | 456 | 0 | 0 | 1 | 6 | `all`/`clear` (multiFilter, internal); `empty-action` default | none |
| nav.js | 281 | 0 | 1 | 0 | 5 | reload | none |
| pickers.js | 230 | 0 | 0 | 0 | 4 | `apply` (internal) | none |
| charts.js | 152 | 0 | 0 | 1 | 1 | n/a | n/a |
| api.js | 126 | 0 | 1 | 0 | 3 | n/a | n/a |
| format.js | 121 | 0 | 0 | 1 | 0 | n/a | n/a |
| breakdown.js | 100 | 0 | 0 | 1 | 1 | `data-bd=toggle-all` | none |
| store.js | 86 | 0 | (shared with api.js P1) | 0 | 1 | n/a | n/a |
| theme.js | 64 | 0 | 0 | 0 | 1 | n/a | n/a |
| icons.js | 149 | 0 | 0 | 0 | 1 | n/a | n/a |
| *.html (11) | 566 | 0 | 0 | 0 | 5 | n/a | n/a |

---

## P1

### [P1] Cross-user data leakage on a shared browser: caches and query memory are never cleared on logout/login
- **Area:** security
- **Where:** `frontend/js/nav.js:172` (Sign out), `frontend/js/pages/login.js:22-24` (login success), `frontend/js/store.js:20-26` (fresh cache is returned without revalidation), `frontend/js/api.js:81-93` (`rememberQuery`/`savedQuery`), `frontend/js/pages/review.js:9-10` (`ispend.review.skipped`), `frontend/js/pickers.js:3-8,212-214` (`ispend.recentCats`, `ispend.period`), `frontend/js/pages/import.js:8-10` (`ispend.importAccount`)
- **What:** Sign out only calls `POST /api/auth/logout` and navigates to `/login.html`; nothing removes `sessionStorage`/`localStorage` state. Because `store.get()` returns a cache entry immediately whenever `now - entry.at < ttl` (categories/accounts TTL 60 s, `settings/client` TTL 300 s) *without* revalidating, user B logging in within that window in the same tab sees user A's categories, account names/last4/colors and `ai_configured` flag (category pickers, account filter, import account dropdown, Review "Ask AI" button) until the TTL expires. Independently of TTL, `ispend.q:/transactions.html` etc. carry A's free-text `q=` searches, category/account ids and merchant filters into B's sidebar links (`nav.js:45,93`), `ispend.review.skipped` carries A's merchant keys, and `ispend.recentCats` carries A's category ids. Multi-user isolation is a stated product requirement (CLAUDE.md), so this is a data-isolation defect even though the backend is correctly scoped.
- **Repro:** As admin, open Transactions and search `q=Costco`; open Categories (cache warms). Sign out. Within 60 s log in as `qa_tester` in the same tab: the sidebar "Transactions" link contains `?q=Costco`; opening Categories shows admin's tree until revalidation; the category picker's "Recent" group lists admin's category ids that happen to exist.
- **Fix:** In `nav.js` Sign out (before `location.href`) and in `login.js` on success: remove every `sessionStorage`/`localStorage` key with prefix `ispend.` except `ispend.theme`, `ispend.density`, `ispend.sidebar` (add `clearUserState()` to `api.js`). Additionally stamp `ispend.uid = me.id` in `initNav` and clear when it differs from the stored value (covers session expiry + relogin as someone else).

### [P1] Transactions list: filter change while a page is loading skips the new load and renders the stale response
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:175-207` (`reload`, `loadMore`)
- **What:** `reload()` clears `tx.items`, paints skeletons and calls `loadMore(true)`, but `loadMore` starts with `if (tx.loading || tx.done) return;` and only increments `tx.seq` *after* that guard. If an infinite-scroll page or a previous filter's request is still in flight, the new first load is dropped: the table stays on skeletons, and when the old request resolves its `seq` still equals `tx.seq`, so it passes the stale check at L190 and paints the *old* filter's rows (with `first=false` it uses `insertAdjacentHTML` into the freshly emptied `tbody`, and sets `tx.cursor`/`tx.total`/summary from the old query). URL and toolbar show the new filter; the data is from the old one.
- **Repro:** On Transactions with `range=all` (1238 rows), scroll fast so a "load more" is in flight, then immediately click a status chip (or type in search). Table shows rows from the previous query and the footer count does not match `#tx-summary`. Easier with devtools network throttling.
- **Fix:** In `reload()` bump `tx.seq` and reset `tx.loading = false` before calling `loadMore(true)` (or keep an `AbortController` per request and abort on reload). Also guard `loadMore(true)` so a `first` load always proceeds.

### [P1] Rules page: unescaped rule pattern injected as HTML into the delete confirmation (stored XSS reachable from imported statements)
- **Area:** security
- **Where:** `frontend/js/pages/rules.js:177` — `` body: `“${r.pattern}” will no longer categorize…` `` passed to `ui.confirm`, whose body is inserted raw (`frontend/js/ui.js:107` ``html: `<p>${body}</p>` ``)
- **What:** `ui.confirm` treats `body` as HTML (other callers rely on `<b>`), so `body` must be escaped by the caller. Rule patterns are user data and can be derived from statement text: `POST /api/transactions/<id>/rule-draft` returns `pattern = merchant_key` and `alt_pattern = description_clean` (`backend/transactions_api.py:540-549`), and the transactions "Create rule" modal lets the user pick `description_raw` as the match field (`transactions.js:554`). A CSV/PDF line such as `<img src=x onerror=alert(document.cookie)>` becomes a rule pattern; the payload fires when the user later clicks Delete on that rule. Statement files are the app's untrusted input, so this is a stored XSS in the victim's own session (multi-user isolation limits blast radius to that account). Every other `ui.confirm`/`ui.modal` body in the codebase escapes correctly (`statements.js:114,122,136`, `import.js:505,524`, `categories.js:362`).
- **Repro:** Create a rule with pattern `<img src=x onerror=alert(1)>` (any match type). Rules page → ⋯ → Delete. Alert fires.
- **Fix:** `body: \`“${esc(r.pattern)}” will no longer…\``. Consider making `ui.confirm` escape `body` by default and accept `{ html }` for the rare rich case.

### [P1] Settings → AI: "Share this key with all users" toggle is a no-op (frontend/backend contract mismatch)
- **Area:** api
- **Where:** `frontend/js/pages/settings.js:141` sends `PUT /api/settings {shared:true}`; `backend/settings_api.py:53-64`
- **What:** The backend only publishes a key/model to the shared settings when `shared=true` is sent *together with* `openrouter_api_key`/`openrouter_model` values in the same payload (the loop at L54 iterates `data.items()` and skips keys not in `SETTING_KEYS`; masked keys are skipped at L57 before the `shared` branch). The frontend sends `{shared:true}` alone, so nothing is written, the success toast "Key shared with all users" appears, `loadAI()` re-fetches and the switch snaps back to off. `clear_shared` works. `saveAI()` (L257) never includes `shared` either, so there is no path that actually shares a key from the UI.
- **Repro:** As admin with a saved key/model: Settings → AI → toggle "Share this key with all users". Toast says shared; after re-render the switch is unchecked and `GET /api/settings` still has `shared_available:false`.
- **Fix:** Backend: when `shared` is true and the payload lacks the key/model, copy the caller's stored `user_setting` values into the global settings. Frontend alternative: send `{shared:true, openrouter_api_key: <plain key>, openrouter_model}` — not possible because the key is masked; so fix the backend and have the frontend show the resulting `shared_available` state instead of an optimistic toast.

---

## P2

### [P2] `fmtMoney({compact:true})` caches the formatter with the first value's fraction-digit rule → wrong/duplicate compact labels on chart axes
- **Area:** frontend-js
- **Where:** `frontend/js/format.js:20-24`; consumers `frontend/js/charts.js:53-55` (`currencyTicks` → every y-axis on Dashboard, Reports, Categories), `dashboard.js:270`, `insights.js:40`
- **What:** The compact options depend on the value (`maximumFractionDigits: a < 1000 ? 0 : 1`) but the memo key is `${cur}|${compact}|${decimals}`. The first compact call for a currency fixes the formatter for all later calls. Axis ticks are generated bottom-up starting at 0, so the cached formatter has `maximumFractionDigits: 0`; ticks 1500 / 2500 render as "$2K" / "$3K" (rounded), producing repeated or misleading axis labels. Conversely if the first call is ≥1000, values under 1000 get a spurious decimal.
- **Repro:** Dashboard with monthly spend around $1–3K: y-axis shows e.g. "$0, $1K, $2K, $2K, $3K".
- **Fix:** Include the branch in the key (`${cur}|${compact}|${decimals}|${a < 1000}`) or drop the value-dependent option.

### [P2] Transactions range button opens two date pickers (double binding)
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:41` (`addEventListener('click', dateRangePicker…)`) and `:127` (`mountRangeButton` sets `btn.onclick = dateRangePicker…`)
- **What:** Both handlers run on every click, creating two stacked popovers (two `document` mousedown/scroll listeners, two layer entries). Visually they overlap so the user sees one, but Esc must be pressed twice, the capture-phase outside-mousedown of one closes it while the other consumes the click, and the shortcut layer stack is left with a phantom popover if the user tabs away.
- **Repro:** Transactions → click the range button → press Esc once → the button's `aria`/picker state still shows a layer (`ui.layers.length === 1` in console).
- **Fix:** Delete line 41; `paintToolbar()` already mounts the button.

### [P2] Import review: preview shrinks to the first 500 rows after any account/mapping/category/AI change
- **Area:** frontend-js
- **Where:** `frontend/js/pages/import.js:368-376` (`applyMapping`: `f.statement = res`), `:431-432` (`changeAccount`), `:469` (`aiExtract`), `:479` (`confirmSuggestions`), `:495` (`pickRowCategory`); backend `statements_api.py:14-15,70` (`ROW_LIMIT_DEFAULT = 500`)
- **What:** Polling and reopen fetch `GET /api/statements/<id>?limit=2000` (L145, L156, L164), but every mutating endpoint returns `_detail()` with the default 500-row page, and the code overwrites `f.statement` (including `rows`) with that response. The guard `if (!f.statement.rows) await reloadStatement(...)` never triggers because `rows` is present. For statements over 500 rows the preview silently drops rows after the first change (no "Showing the first N rows" notice since `rows.length >= 2000` is false), while the summary counts and the commit still cover all rows.
- **Repro:** Upload a CSV with >500 rows, wait for preview (all rows listed), change the account in the dropdown → table now ends at row 500.
- **Fix:** After any of those calls, `await reloadStatement(f, {silent:true})` unconditionally (or have the backend honour `?limit=` on PUT/POST, and pass `limit=2000`). Cheaper: merge `{...res, rows: undefined}` then reload.

### [P2] Bulk actions refetch each selected transaction individually (N requests)
- **Area:** perf
- **Where:** `frontend/js/pages/transactions.js:413-417` (`refreshItems`), called from `bulk()` at `:407`
- **What:** After Transfer/Exclude/Accept/Reject/Include/Flip on a selection, the page issues one `GET /api/transactions/<id>` per selected id in parallel. "Select all loaded" on a long list (up to 1238 rows for admin) → up to 1238 concurrent requests, each also loading `events`/`merchant_others` server-side (`transactions_api.py:383-401`).
- **Repro:** Transactions, `range=all`, scroll to load ~500 rows, check the header checkbox, click Exclude; watch the network tab.
- **Fix:** Have `POST /api/transactions/bulk` return the updated rows (or `ids`) and patch locally, or call `reload()` once when `ids.length > ~20`.

### [P2] Chart instances leak on re-render (merchant sparklines, category side chart, donut error branch)
- **Area:** perf
- **Where:** `frontend/js/pages/reports.js:200,215,220-221` (new `<canvas data-spark>` per render; `charts.sparkline` → `makeChart`), `frontend/js/pages/categories.js:180,219` (`#side-chart` recreated per `renderSide`), `frontend/js/pages/dashboard.js:251` (error branch replaces canvas without `destroyChart`), `frontend/js/charts.js:61-72` (registry keyed by canvas element, never pruned)
- **What:** `makeChart` only destroys a previous chart bound to the *same* canvas element. Merchants table re-renders on every sort click / range change with fresh canvases, so 50 Chart.js instances (plus registry entries and Chart.js's own instance map) accumulate per render and are all re-built on every theme change (`rerenderAll`). Same pattern for the categories side panel (one per selection). Long sessions degrade.
- **Repro:** Reports → Merchants, click the "Total" header 10 times; `performance.memory` grows and `Chart.instances` shows 500+ entries.
- **Fix:** In `charts.makeChart`/`rerenderAll`, prune registry entries whose `canvas.isConnected` is false and `destroy()` them; or destroy the previous set before `tb.innerHTML = …` (`$$('canvas[data-spark]', tb).forEach(destroyChart)`).

### [P2] Categories: unescaped parent name in `placeholder` attribute (attribute breakout / convention violation)
- **Area:** security
- **Where:** `frontend/js/pages/categories.js:333` — `` placeholder="${isSub ? `e.g. ${parent.name} › Something` : 'e.g. Kids'}" ``
- **What:** `parent.name` is user data interpolated into an attribute without `esc()`. A name containing `"` breaks the attribute; `" autofocus onfocus="alert(1)` executes when the modal opens (the input has `autofocus` and `ui.modal` focuses it). Self-XSS only (categories are per user), but it is the one `esc()` miss in the category page and violates the README's "every interpolation" rule.
- **Repro:** Rename a top-level category to `x" autofocus onfocus="alert(1)`; open its ⋯ menu → Add subcategory.
- **Fix:** `esc(parent.name)`.

### [P2] Login `next` redirect allows `/\evil.com` (open redirect)
- **Area:** security
- **Where:** `frontend/js/pages/login.js:24`
- **What:** The check `next.startsWith('/') && !next.startsWith('//')` misses backslash variants. Browsers normalise `/\evil.com` to `//evil.com`, so `login.html?next=/%5Cevil.com` redirects to an external host after a successful login (classic phishing vector: legitimate login page, attacker-controlled landing page).
- **Repro:** Visit `/login.html?next=/%5Cexample.com`, sign in → lands on example.com.
- **Fix:** `const u = new URL(next, location.origin); location.href = u.origin === location.origin ? u.pathname + u.search + u.hash : '/index.html';`

### [P2] Page initialisation has no error handling: a failed reference load leaves a blank page
- **Area:** ux
- **Where:** `frontend/js/pages/transactions.js:27-29` (`await loadRefs()`, `await store.displayCurrency()`), `frontend/js/pages/review.js:16-18` (`await loadRefs()`), `frontend/js/pages/rules.js:44` (inside try — fine), `frontend/js/pages/categories.js:11` (fine), `frontend/js/pages/reports.js:9` (fine)
- **What:** `store.categoriesFlat()`/`store.accounts()` rejections in the `initNav().then(async …)` callback are unhandled: the promise rejects, nothing renders (Transactions shows an empty table with no skeleton, Review shows an empty list), and the user has no retry. `api()` errors are `Error` objects with messages meant for `ui.errorBox`.
- **Repro:** Block `/api/categories` in devtools, open Transactions.
- **Fix:** Wrap the init body in `try { … } catch (err) { $('#main').insertAdjacentHTML('afterbegin', ui.errorBox(err.message, { retry: 'reload' })); }` (the `reload` handler already exists on both pages).

### [P2] Settings: menu actions call `api()` without try/catch → failures are silent
- **Area:** ux
- **Where:** `frontend/js/pages/settings.js:332` (Make admin/regular), `:338` (Deactivate/Activate), `:341` (Delete permanently), `:380` (Archive/Restore account), `:384` (Delete account)
- **What:** `ui.menu` item `onClick` handlers are not awaited by the menu (`ui.js:234`), so a rejected `api()` becomes an unhandled promise rejection: no toast, table not refreshed, user believes the action worked. Contrast with `deleteRule`/`forgetMerchant` which handle errors.
- **Repro:** Settings → Users → ⋯ on a user → "Make admin" while offline (or force a 500): nothing happens.
- **Fix:** Wrap each handler body in `try { … } catch (err) { toast(err.message, { type: 'error' }); }`, or make `ui.menu` await `onClick` and toast on rejection like `ui.modal` does (`ui.js:81-85`).

### [P2] Review: mode switch race parses the wrong response shape (TypeError, list stuck on skeleton)
- **Area:** frontend-js
- **Where:** `frontend/js/pages/review.js:65-78` (`load` reads `rv.mode` *after* `await api(…)`), `:24` (mode buttons call `load()` without cancelling)
- **What:** Clicking "One by one" then quickly "By merchant" starts two requests. When the `mode=single` response resolves, `rv.mode` is already `'merchant'`, so the code does `r.groups.map(...)` on a payload that only has `items` → `TypeError: Cannot read properties of undefined (reading 'map')` — uncaught (it is outside the `try`'s api call? no: it is inside the try, so it lands in the catch and paints an `errorBox` with the TypeError text). Either way the user sees a JS error message or stale groups, and the later response may render the other mode's cards.
- **Repro:** Review page, click the two mode buttons in quick succession with network throttled.
- **Fix:** Capture `const mode = rv.mode; const seq = ++rv.seq;` before the await and bail if `seq !== rv.seq`; parse using the captured `mode`.

### [P2] No request sequencing on Dashboard, Insights and Reports → stale data can overwrite newer selections
- **Area:** frontend-js
- **Where:** `frontend/js/pages/dashboard.js:80-118` (`load`), `:346-358` (`loadBreakdown`), `:229-252` (`renderDonut` drill fetch); `frontend/js/pages/insights.js:22-31` (`load`); `frontend/js/pages/reports.js:67-77,105-122,171-178,198-206,226-242,270-279`
- **What:** All period/month/tab changes fire a new fetch and whichever response arrives last wins. The Dashboard `‹ ›` month arrows, Insights `‹ ›` arrows and Reports month/segment controls invite rapid clicking; the URL (`setQs`) and the header label reflect the last click while KPIs, charts, breakdown and AI text can come from an earlier month. The transactions page and the command palette already use a `seq` guard (`transactions.js:186-190`, `nav.js:230-246`).
- **Repro:** Dashboard → click "‹" three times quickly with network throttling; `#dash-sub` says one month, KPIs show another.
- **Fix:** Add a per-page `seq` counter captured before each fetch and checked before rendering (as in `transactions.js`), including inside `loadBreakdown`/`renderDonut`.

### [P2] Breakdown table: keyboard Enter on a category link toggles the row instead of following the link
- **Area:** a11y
- **Where:** `frontend/js/breakdown.js:95` (`host.onkeydown`) vs `:92` (`host.onclick` has `if (e.target.closest('a')) return;`)
- **What:** The keydown handler checks `e.target.closest('.bd-parent.has-kids')` and `preventDefault()`s Enter/Space. When focus is on the `<a class="bd-link">` inside such a row, Enter is swallowed and the row expands instead of navigating to Transactions. Keyboard users cannot open a parent category with children (Dashboard "Category breakdown" and Reports "Categories" table).
- **Repro:** Tab to "Dining" link in the breakdown, press Enter.
- **Fix:** Mirror the click guard: `if (e.target.closest('a')) return;` at the top of `onkeydown`.

### [P2] Review: "Always do this" is on for every merchant group (`count >= 1` is a tautology) → a rule is created for every decision
- **Area:** ux
- **Where:** `frontend/js/pages/review.js:72` (`always: g.count >= 1`), `:316-320` (`ruleFor`), `:346,351,353`
- **What:** `g.count` is always ≥1, so the "Always do this" switch defaults to checked for every card, and every Accept/Choose/Transfer posts `create_rule` → `review_api.resolve` inserts a rule per merchant. Merchant memory already remembers the decision (`categorizer.apply_manual` with `learn_memory=True`), so the rule is redundant, and the Rules list grows unboundedly (admin has 209 rules). The single-transaction mode sets `always:false`, which suggests the merchant-mode default was meant to be conditional (e.g. `count >= 2`).
- **Repro:** Review → categorize any 1-charge merchant → toast "… · rule created"; Rules page gains an `equals “<merchant_key>”` rule.
- **Fix:** Default `always` to `g.count >= 2` (or `false`) and let the user opt in; consider a settings preference.

### [P2] Modal primary actions can be double-submitted via keyboard/form submit (duplicate creates)
- **Area:** ux
- **Where:** `frontend/js/ui.js:78-86` (button gets `.is-loading` but not `disabled`); submit relays that call `.click()`: `transactions.js:661` (Add transaction → `POST /api/transactions`), `transactions.js:588` (Create rule), `rules.js:292`, `settings.js:91,327`, `import.js:601`, `nav.js:197`
- **What:** `.btn.is-loading` only sets `pointer-events:none` (`css/app.css:214`), which blocks mouse clicks but not keyboard activation or programmatic `.click()`. Pressing Enter twice in the "Add transaction" form (or Enter on the focused primary button) runs `onClick` twice concurrently → two `POST /api/transactions` → duplicate manual transaction; same for Create rule / Create account / Create user (the latter two fail on uniqueness, but rules and transactions duplicate).
- **Repro:** Transactions → Add transaction → fill amount/description → press Enter twice quickly → two identical rows appear after reload.
- **Fix:** In `ui.modal` set `b.disabled = true` while the action runs (and re-enable in `finally`); in the submit relays check `if (btn.classList.contains('is-loading')) return;`.

---

## P3

### [P3] `apiUpload` 401 redirect drops the `next` return URL
- **Area:** frontend-js
- **Where:** `frontend/js/api.js:40` vs `:14-17`
- **What:** `api()` redirects to `/login.html?next=<current>`; the XHR upload path redirects to bare `/login.html`, so a session that expires mid-upload loses the return-to-import flow.
- **Repro:** Let the session expire, drop a file on Import.
- **Fix:** Reuse the same `next` construction.

### [P3] Query memory remembers `rules.html?new=1`, so the "New rule" modal would reopen on every return (latent)
- **Area:** frontend-js
- **Where:** `frontend/js/api.js:79` (`TRANSIENT_QS` lists only `cat` for `/rules.html`), `frontend/js/pages/rules.js:35` (`if (q.new === '1') openRuleModal({})`)
- **What:** `new` is not transient, so once a `?new=1` URL is visited it is stored and re-appended to the sidebar link. No current page emits `?new=1` (grep), so this is latent, but the deep link exists in code.
- **Fix:** Add `'new'` to the `/rules.html` transient list (and `'filter_cat'` if the filter is not meant to stick).

### [P3] `rangeFromQuery` trusts any `range=` string → label "Until —"
- **Area:** frontend-js
- **Where:** `frontend/js/pickers.js:166-170,151-158`; consumers `transactions.js:87`, `dashboard.js:26`, `reports.js:12`
- **What:** `?range=garbage` yields `{preset:'garbage'}`; `rangeLabel` finds no preset and falls through to the custom branch with `from/to` undefined → button text "Until —". Backend ignores unknown ranges (`transactions_api.py:107`) so the list shows *all time* while the button says "Until —".
- **Fix:** Validate against `RANGE_PRESETS` + `/^month:\d{4}-\d{2}$/`, else fall back.

### [P3] Command palette transaction result opens a row that is filtered out of the list
- **Area:** ux
- **Where:** `frontend/js/nav.js:247` (`/transactions.html?q=…&open=<id>` without `range=all`) vs `dashboard.js:306` and `insights.js:68` (which add `range:'all'`)
- **What:** The palette searches all time, but the target page applies the shared period (default this month), so the drawer opens over an empty or unrelated list.
- **Fix:** Append `&range=all` like the other deep links.

### [P3] Topbar theme button and palette "Toggle theme" do not persist the preference to the server
- **Area:** ux
- **Where:** `frontend/js/nav.js:112` (`Theme.toggle()`), `:224`; compare `:175-178` (`setThemePref`) used by the user menu and Settings
- **What:** Inconsistent persistence: a toggle from the topbar is browser-only and is overridden on another device by the server pref.
- **Fix:** Call `setThemePref(Theme.effective() === 'dark' ? 'light' : 'dark')`.

### [P3] `ui.menu` ignores `opts.onClose`; dashboard month button `aria-expanded` sticks at `true`
- **Area:** a11y
- **Where:** `frontend/js/ui.js:213-227` (only `placement`/`noRefocus` read), `frontend/js/pages/dashboard.js:58-59`
- **Fix:** Forward `opts.onClose` into the popover's `onClose` (call both).

### [P3] Dead / duplicate code and unused exports
- **Area:** frontend-js
- **Where / What:**
  - `frontend/js/pages/reports.js:252-253` — identical consecutive lines.
  - `frontend/js/pages/reports.js:84` — `compare-go` handler; no element emits it (reads `#cmp-month`/`#cmp-vs` which are wired via `onchange` at `:238-239`).
  - `frontend/js/pages/import.js:413` — `pick-file` handler; tabs are driven by `ui.tabs` (`:188`).
  - `frontend/js/pages/transactions.js:24,450` — `tx.drawer` is write-only. `:560` — `it.account_id === a.id && draft.account_id` can never be true (`rule-draft` always returns `account_id: null`, `transactions_api.py:547`).
  - `frontend/js/pages/rules.js:5,340-343` — `state.mloading` write-only. `:348` — `const key = encodeURIComponent(...)` unused.
  - `frontend/js/ui.js:153,156-159` — drawer `setDirty`/dirty-confirm has no caller (`setDirty(` only appears in settings.js as a different function). `:456` — `snackbar` alias unused. `:452` — `closeTop` unused. `shortcuts.list/unregister` unused.
  - `frontend/js/api.js:125` — `sleep` unused; `:63-73` `replace:false`/`pushState` branch never used, so the `popstate` handlers in `transactions.js:51`, `dashboard.js:44`, `reports.js:37` never fire for in-page filter changes (back/forward does not step through filters — acceptable design, but then the handlers are dead weight).
  - `frontend/js/store.js:65,67` — `categoryById`/`accountById` unused.
  - `frontend/js/nav.js:45,143` — `adminOnly` is never set on any nav item. `:155` — comment "endpoint may not exist yet" is stale (`/api/review/count` exists).
  - `frontend/login.html:35-37` — loads `format.js` and `ui.js` which `login.js` never uses.
- **Fix:** Remove or wire up.

### [P3] Double-escaping: `esc()` applied before passing to `toast()` / modal `title` (which escape again)
- **Area:** ux
- **Where:** `frontend/js/pages/import.js:435,436` (`esc(accountName(id))`), `:497` (`esc(cat.name)`, `esc(row.merchant_name)`), `frontend/js/pages/categories.js:385` (`title: \`${esc(cat.name)} is in use\``)
- **What:** `toast` (`ui.js:377`) and `ui.modal` (`ui.js:50`) already escape; names containing `&`, `<` or quotes render as `&amp;` etc.
- **Fix:** Pass raw strings to `toast`/`title`.

### [P3] Auto-generated rule name check does not match the backend format for `starts_with`
- **Area:** frontend-js
- **Where:** `frontend/js/pages/rules.js:78` compares against `` `${r.match_type} “${r.pattern}”` `` but the backend default is `match_type.replace('_',' ')` (`backend/rules.py:29`) → "starts with “X”".
- **What:** For `starts_with` rules the redundant auto-name is displayed next to the pattern.
- **Fix:** Compare against `r.match_type.replace('_', ' ')`.

### [P3] Sortable table headers are not keyboard operable
- **Area:** a11y
- **Where:** `frontend/transactions.html:37-41` + `frontend/js/pages/transactions.js:46` (click only), `frontend/js/pages/reports.js:200,212` (`th.onclick`)
- **What:** `th.sortable[aria-sort]` has no `tabindex`/`role=button`/key handler; sorting is mouse-only.
- **Fix:** Render a `<button>` inside the `th` (or add `tabindex="0"` + Enter/Space handling).

### [P3] Rows acting as links are `div`/`tr` instead of anchors
- **Area:** a11y
- **Where:** `frontend/js/pages/dashboard.js:299` (`<div role="link" tabindex="0">` recent list), `frontend/js/pages/reports.js:165-168,215,265,313` (`tr[data-href]`, Enter only, no Space, no context-menu/open-in-new-tab), `frontend/js/pages/statements.js:61,84-90` (`tr.is-clickable` with no `tabindex` — keyboard users must use the ⋯ menu)
- **Fix:** Put a real `<a href>` on the primary cell and let the row click delegate to it.

### [P3] Form labels not associated with their controls
- **Area:** a11y
- **Where:** `transactions.js:558` ("Then set category" → button `#rf-cat`), `:626` ("Category" → `#ad-cat`), `rules.js:219`, `categories.js:335-336` ("Color"/"Icon" grids), `settings.js:77` ("Color"), `import.js:296-298` ("Date format", "Amount sign", "Skip rows above header" — no `for`/`id`), `transactions.js:507` (Notes textarea labelled only by a `div.section-label`), `frontend/transactions.html:29` (`#f-q` has only a placeholder), `frontend/reports.html:30-31` (`#flow-seg`, `#months-seg` have no `role`/`aria-label`)
- **Fix:** Use `for`/`id`, `aria-labelledby`, or `aria-label`; add `role="group" aria-label` to the segs.

### [P3] Toast host is created lazily, so the live region is not present before the first announcement
- **Area:** a11y
- **Where:** `frontend/js/ui.js:372-376` (`root('toast-root')` has no `aria-live`; each toast carries `role=status/alert`), `nav.js:96` creates `#toast-root` on authenticated pages but not on login
- **What:** Screen readers announce dynamically inserted `role=status` elements inconsistently; the recommended pattern is a pre-existing `aria-live` container.
- **Fix:** Give `#toast-root` `aria-live="polite" aria-relevant="additions"` when created and keep `role=alert` for errors.

### [P3] Focus is lost after actions that replace the focused element
- **Area:** a11y
- **Where:** `frontend/js/pickers.js:105` (`onClose` refocuses `anchor`, but `transactions.js:242-250 rerenderRow` has already replaced the `<tr>`, so the anchor is detached), `frontend/js/pages/review.js:224-230` (`rerenderCard` replaces the focused card on expand/pattern toggle), `frontend/js/pages/rules.js:134` (`render()` after pattern commit), `frontend/js/pages/categories.js:249-262` (style popover opens without moving focus into it; its buttons are at the end of the tab order)
- **Fix:** Re-query the replacement element by `data-id`/`data-key` and focus it; `ui.focusFirst(el)` in `openStylePopover`.

### [P3] Reports tabs and segmented controls do not use `ui.tabs`/`ui.segmented`
- **Area:** a11y
- **Where:** `frontend/reports.html:20-26` (`role=tablist`/`role=tab` markup) + `frontend/js/pages/reports.js:27,56-59` (manual `aria-selected`, no roving tabindex, no `aria-controls`, panels lack `role=tabpanel`); `reports.html:30-31`, `review.html:20-24`, `rules.html:18`, `transactions.html:27-28` (`.seg` groups with `aria-pressed` buttons — fine but inconsistent with the Dashboard's `role=radiogroup` + `ui.segmented`)
- **Fix:** `ui.tabs($('#report-tabs'), { onChange: (t) => switchTab(t.dataset.tab) })` and `ui.segmented` for the segs, as the README prescribes.

### [P3] Chatty/incomplete live regions
- **Area:** a11y
- **Where:** `frontend/js/pages/transactions.js:281` (bulk floatbar `role="status"` re-rendered with all button labels on every selection change), `frontend/js/pages/settings.js:268` (same for the save bar), `frontend/js/pages/review.js:104,188` (`.progress > span[style=width]` without `role=progressbar`/`aria-valuenow`)
- **Fix:** Put `role=status` only on the count `<span>`; add progressbar semantics.

### [P3] Summary totals go stale after local mutations; mixed-currency summary picks the alphabetically first currency
- **Area:** ux
- **Where:** `frontend/js/pages/transactions.js:406-407` (delete updates `tx.total` only; `sumIn/sumOut/skipped` unchanged), `:403-412` (transfer/exclude/include change what counts as spent, summary not refreshed), `:209-210` (`cur = curs[0]` → "CAD" symbol when a mix of CAD/USD, ignoring `tx.displayCurrency`)
- **Fix:** Re-fetch the first page (`reload()`) after bulk actions that affect sums, or have `/bulk` return new totals; prefer `tx.displayCurrency` when `curs.length > 1`.

### [P3] Unsequenced secondary requests (stale preview/search results)
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:576-585` (rule preview: two POSTs per change, no seq), `frontend/js/pages/rules.js:277-289` (preview), `:336-345` (`loadMerchants` search), `frontend/js/pages/import.js:362-380` (`applyMapping` — a flip-signs click during a pending mapping PUT can be overwritten), `frontend/js/pages/settings.js:222-226` (model list — synchronous filter, fine)
- **Fix:** Capture a sequence id before `await` and ignore late responses.

### [P3] `updateItem` rethrows after toasting → unhandled promise rejections
- **Area:** frontend-js
- **Where:** `frontend/js/pages/transactions.js:401` (`throw err`) with un-caught callers at `:314` (shortcut `t`), `:349-350` (row menu), `:527` (`.then` without `.catch`)
- **Fix:** Either do not rethrow, or `.catch(() => {})` at those call sites.

### [P3] Search: Enter plus the pending debounce triggers two reloads
- **Area:** perf
- **Where:** `frontend/js/pages/transactions.js:38-39`
- **Fix:** Cancel the debounce on Enter (expose a `.cancel()` on `debounce`, or track the timer).

### [P3] Whole-table re-render for selection changes
- **Area:** perf
- **Where:** `frontend/js/pages/transactions.js:317` (Esc clears selection → `rerenderAll()`), `:336` (shift-click range → `rerenderAll()`), `:431` (bulk Clear → `rerenderAll()`)
- **What:** `paintSelection()` already syncs `aria-selected` and checkboxes for every row; rebuilding the entire `tbody` innerHTML for 1000+ rows is unnecessary and drops row focus.
- **Fix:** Call `paintSelection()` only.

### [P3] Review: `resolve()` renders immediately and again after the leave animation
- **Area:** ux
- **Where:** `frontend/js/pages/review.js:328-332` (`leave(g)` then `render()` on the next line) vs `:335-342`
- **What:** The immediate `render()` removes the card before `is-leaving` can animate; the 260 ms timeout renders again. The exit animation is effectively dead and the list re-renders twice.
- **Fix:** Skip the immediate `render()` when `leave()` was called (update only the progress header).

### [P3] `icon()` resolves prototype keys; unvalidated icon names can crash rendering
- **Area:** frontend-js
- **Where:** `frontend/js/icons.js:137` (`ICONS[name] || ICONS.tag`), backend `categories_api.py:85,128` (icon stored unvalidated)
- **What:** `icon('constructor', 'ico-sm')` returns `Object` (a function) → `.replace` is not a function → TypeError wherever that category renders (`breakdown.js:67`, `dashboard.js:183,300`, `categories.js:56`). Only reachable via direct API calls (the UI picker only offers `CATEGORY_ICONS`).
- **Fix:** `Object.hasOwn(ICONS, name) ? ICONS[name] : ICONS.tag`; validate `icon` server-side against the known list.

### [P3] Dashboard donut adds a new `ispend:theme` listener on every render
- **Area:** perf
- **Where:** `frontend/js/pages/dashboard.js:279`
- **What:** Each `renderDonut` call (every period change, every drill) registers another window listener holding `cats`/`legend` closures; they all run on theme toggle. Harmless functionally, grows over a session.
- **Fix:** Recolour dots inside the `makeChart` builder (which the registry re-runs on theme change) or register once at init.

### [P3] Reports trend selection is keyed by series index
- **Area:** ux
- **Where:** `frontend/js/pages/reports.js:184-189`
- **What:** `state.trendSel` stores `'0','1'` etc.; switching months (6→24) or flow changes which category sits at each index, so the "selected" lines silently swap to different categories while chips stay highlighted.
- **Fix:** Key by `category_id` (use `'none'`/`'other'` for null ids).

### [P3] Stacked chart "Other" bucket click links to all transactions
- **Area:** ux
- **Where:** `frontend/js/pages/reports.js:149` (`cat: s.category_id == null ? (s.name === 'Other' ? null : 'none') : …`)
- **What:** Clicking the "Other" segment opens the month's full transaction list, not the categories bucketed into "Other".
- **Fix:** Link to `/reports.html?tab=category&…` (as the dashboard does at `dashboard.js:288`) or pass the excluded ids.

### [P3] Reports CSV export for "By category" uses `level=top` while the table shows subcategories
- **Area:** ux
- **Where:** `frontend/js/pages/reports.js:94` vs `:117`
- **Fix:** Export `level:'sub'` to match the visible table.

### [P3] Transfer-only remembered merchants cannot be renamed
- **Area:** ux
- **Where:** `frontend/js/pages/rules.js:400-402` sends `category_id: m.category_id` (null for transfer-only memory); backend `merchants_api.py:37-39` rejects `category_id is required`
- **What:** "Rename…" on such a row yields an error toast.
- **Fix:** Allow rename without category server-side, or hide Rename when `category_id == null`.

### [P3] Settings model list: a failed fetch leaves `state.models = []` and never retries
- **Area:** ux
- **Where:** `frontend/js/pages/settings.js:218-221`
- **What:** The guard `if (!state.models)` sets `[]` before the await; on error the empty array persists, so every later focus shows "No models match · 0 models" until Refresh is clicked.
- **Fix:** Reset `state.models = null` in the catch.

### [P3] "Show key" toggle does not update its accessible name
- **Area:** a11y
- **Where:** `frontend/js/pages/settings.js:118,410`
- **Fix:** Toggle `aria-label` between "Show key"/"Hide key".

### [P3] Import: dropping a file outside the dropzone navigates away; hidden file input is a second tab stop
- **Area:** ux
- **Where:** `frontend/js/pages/import.js:81-83` (drag handlers only on `#dropzone`), `:67-68` (`label[tabindex=0][role=button]` wrapping a focusable `<input type=file>`)
- **Fix:** `document.addEventListener('dragover'/'drop', e => e.preventDefault())` while on the upload step; give the input `tabindex="-1"`.

### [P3] Import `autoAccount` suppresses the review render; on failure the step stays on the parsing card
- **Area:** ux
- **Where:** `frontend/js/pages/import.js:176,441-450` (`renderReview` returns early when `autoAccount` kicks off `changeAccount`; on error `changeAccount` only toasts and nothing re-renders)
- **Fix:** Render first, then apply the automatic account (or call `renderReview()` in `changeAccount`'s catch).

### [P3] Theme: server preference applied after first paint; stored theme is shared by all users of the browser
- **Area:** ux
- **Where:** `frontend/js/nav.js:139-140` (`Theme.set(prefs.theme)` after `/api/auth/me`), `frontend/js/theme.js:4-5` (single `ispend.theme` key)
- **What:** First visit in a browser: system theme paints, then the server pref flips it (one-time flash). On a shared browser user B's server preference is ignored once user A has a stored choice (`hasStored()`).
- **Fix:** Accept as-is, or key `ispend.theme` per user id once `me` is known.

### [P3] README/markup inconsistencies
- **Area:** docs
- **Where:** `frontend/js/README.md:33` says non-admin elements are `[data-admin-only]`; `frontend/settings.html:21` uses `data-admin` and `settings.js:18` handles it separately. README `mountRangeButton` note does not warn that the button must not also get a click listener (see P2 above). README lists `store.on` return value; no page uses the unsubscribe.
- **Fix:** Use `data-admin-only` in settings.html (nav.js already removes it) and delete the special case; update README.

### [P3] Convention deviations: direct `on*` property assignment instead of delegated `data-act`
- **Area:** frontend-js
- **Where:** `frontend/js/breakdown.js:89,95`, `frontend/js/charts.js:92`, `frontend/js/pickers.js:205`, `frontend/js/pages/dashboard.js:276-278,307-308`, `frontend/js/pages/reports.js:153,166-167,188,212,238-239`
- **What:** Safe (re-assignment replaces the previous handler), but it is the pattern the README forbids and it makes `reports.js:212` re-bind 5 header handlers per render.
- **Fix:** Delegate from the panel/card container with `data-act`.

### [P3] `ui.multiFilter` shows two "All"/"Clear" control pairs
- **Area:** ux
- **Where:** `frontend/js/ui.js:268` (head: All / None) and `:277` (foot: Clear / Select all)
- **Fix:** Keep one pair.

### [P3] Review page fetches transfer candidates on every load, even outside Transfers mode
- **Area:** perf
- **Where:** `frontend/js/pages/review.js:25` (`refreshPairCount` → `GET /api/transactions/transfer-candidates`, a self-join over all transactions `transfers.py:17-30`)
- **Fix:** Use `/api/review/count`-style cached count or fetch lazily when the Transfers tab is first shown.

### [P3] `breakdown.js` rows without children are focusable but inert
- **Area:** a11y
- **Where:** `frontend/js/breakdown.js:66` (`tabindex="0"` on every `.bd-parent`, `aria-expanded` only when `hasKids`)
- **Fix:** Only add `tabindex="0"` (and `role="button"`) when `hasKids`.

### [P3] Categories side panel shows spending-only data for income categories
- **Area:** ux
- **Where:** `frontend/js/pages/categories.js:29-30` (`by-category?range=this-month` without `flow`), `:205,212` (`/reports/monthly` and `by-category` without `flow=income`)
- **What:** Income categories (Salary, Interest…) show "—" for "This month" and an all-zero 6-month trend.
- **Fix:** Pass `flow: cat.kind === 'income' ? 'income' : 'spending'`.

---

## Summary

**Counts:** P0: 0 · P1: 4 · P2: 14 · P3: 33 (several P3 entries bundle multiple locations).

**Top 5:**
1. **Cross-user leakage via never-cleared `sessionStorage`/`localStorage`** (`nav.js:172`, `login.js:22-24`, `store.js:20-26`, `api.js:81-93`): a fresh-TTL cache hit returns the previous user's categories/accounts/settings, and remembered `?q=`/filter queries follow the next user into their sidebar links. Clear all `ispend.*` user state on logout and login.
2. **Transactions `reload`/`loadMore` race** (`transactions.js:175-207`): the `tx.loading` gate drops the new first load and lets the in-flight stale response render under the new filters — wrong rows and totals shown for the URL state. Bump `tx.seq` and reset `loading` in `reload()`.
3. **Stored XSS in the rule delete confirm** (`rules.js:177`): the pattern (which can come straight from imported statement text via `rule-draft`) is injected as raw HTML. Wrap in `esc()`; consider escaping `ui.confirm` bodies by default.
4. **"Share this key with all users" does nothing** (`settings.js:141` vs `settings_api.py:53-64`): `PUT {shared:true}` alone never publishes the key; the UI toasts success and the switch snaps back.
5. **Compact money formatter cache bug → wrong/duplicate chart axis labels** (`format.js:20-24`, used by `charts.currencyTicks`): the value-dependent `maximumFractionDigits` is not part of the memo key, so ticks render as "$2K, $2K, $3K".

Also worth scheduling soon among the P2s: the double-bound date picker on Transactions (`transactions.js:41` + `:127`), import previews silently truncating to 500 rows after any edit, N-per-row refetch after bulk actions, chart instance leaks in Reports/Categories, the `/\evil.com` open redirect on login, silent failures in Settings menu actions, and the tautological `always: g.count >= 1` that creates a rule for every Review decision.
