# iSpend — CSS, design-system and accessibility review

Scope: `frontend/css/tokens.css`, `app.css`, `breakdown.css`, `css/pages/*.css`, all 11 `frontend/*.html`, generated markup in `js/ui.js`, `js/nav.js`, `js/pickers.js`, page scripts (read-only). Live verification with Playwright/Chromium headless against http://localhost:5559 as `qa_tester` (empty data) and `admin` (read-only, populated), both themes (`localStorage ispend.theme`), viewports 1440x900 / 1024x768 / 390x844, axe-core 4.10.2 (tags wcag2a/aa, wcag21a/aa, wcag22aa, best-practice). 132 page/state runs, 200 screenshots.

Artifacts
- Scripts: `qa/e2e/css_audit.py` (static: dead selectors, duplicates, WCAG ratios from token hex), `qa/e2e/a11y_scan.py` (live scan → `qa/reports/a11y-scan.json`), `qa/e2e/a11y_probe.py` (targeted computed-style probes), `qa/e2e/vendor/axe.min.js` (offline fallback).
- Screenshots: `qa/reports/screenshots/a11y-<page>-<theme>-<w>.png`, `…-focus.png` (after 15 Tabs), `a11y-admin-<page>[-<state>]-<theme>-<w>.png`.

---

## Findings

### [P1] Sidebar rail mode leaves all 11 nav links without an accessible name
- **Area:** a11y
- **Where:** `frontend/css/app.css:131` (`html[data-sidebar="rail"] .nav-item .label { display:none }`), `frontend/js/nav.js:44-47` (`navItemHtml`), `app.css:138-143` (CSS-only `::after` tooltip from `data-label`)
- **What:** In rail mode (default for 960–1279px, or whenever the user collapses the sidebar with `[`) the visible label is `display:none`, the SVG is `aria-hidden`, and the tooltip is a CSS pseudo-element. Result: the link has no text at all. axe `link-name` [serious]: 10 nodes on every page at 1024px (14 on settings), both themes. Probe: `{'text': 'Dashboard', 'labelDisplay': 'none', 'ariaLabel': None, 'title': None}` for each `.nav-item`. Screen-reader and voice-control users get "link, link, link…" for the whole primary navigation.
- **Repro:** Load any page at 1024px wide (or press `[` at 1440) → axe `link-name` on `.sidebar .nav-item`; VoiceOver rotor lists unnamed links.
- **Fix:** Keep the label in the accessibility tree: replace `display:none` with the `.sr-only` clip pattern for `.nav-item .label` in rail mode, or add `aria-label="${label}"` to every `.nav-item` in `navItemHtml`. Same for `.bn-item` if its label is ever hidden.

### [P2] Page-action buttons lose their name at ≤640px (`.label` hidden)
- **Area:** a11y
- **Where:** `frontend/css/app.css:566` (`.page-actions .btn span.label { display:none }`); `frontend/transactions.html:18-19` (`#btn-add`, `#btn-export`), `statements.html:17` (`#btn-import`), `index.html:26` (`#month-btn`)
- **What:** axe `button-name`/`link-name` [critical/serious] at 390px on transactions (`#btn-add`, `#btn-export`), statements (`#btn-import`) and dashboard (`#month-btn`), both themes and both users. The text exists in the DOM but is `display:none`, so it is removed from the accessibility tree; the buttons become icon-only with no `aria-label`. Screenshot `a11y-transactions-light-390.png` shows the three icon-only buttons.
- **Repro:** transactions.html at 390px → axe `button-name` on `#btn-add`.
- **Fix:** Use `.sr-only` instead of `display:none` for the hidden label, or add `aria-label` to those buttons in the HTML (`aria-label="Add transaction"`, `"Export CSV"`, `"Import statement"`, and set `aria-label` on `#month-btn` from JS when the label collapses).

### [P2] Native `<select>` loses its chevron in dark theme
- **Area:** css
- **Where:** `frontend/css/app.css:228-229` (`:root[data-theme="dark"] .select { background: var(--surface-2) }`) vs `app.css:236` (`.select { background-image: url(data:svg…) }`)
- **What:** The dark-theme rule uses the `background` shorthand, which resets `background-image` to `none`, so every `.select` renders as a plain box with no dropdown affordance in dark mode. Probe on `#upload-account`: light `bgImage: url("data:image/svg+xml…")`, dark `bgImage: none`. Visible in `a11y-import-dark-1440.png` ("Choose during review"), `a11y-admin-settings-tab-appearance-dark-1440.png` ("Auto (from your accounts)"), rule-row selects on rules.html dark. The chevron colour is also hard-coded (`stroke='%236b7280'`) so it cannot follow the theme.
- **Repro:** Set theme dark, open import.html → the "Import into" select has no arrow.
- **Fix:** Change lines 228-229 to `background-color: var(--surface-2)`. Optionally make the arrow themeable by moving the SVG to a mask (`mask-image` + `background-color: var(--text-3)`) or by using a wrapper `.select-wrap::after`.

### [P2] KPI sparkline canvas is resized by Chart.js to the whole card and paints over the value
- **Area:** css
- **Where:** `frontend/css/pages/dashboard.css:4` (`.stat .stat-spark { width:64px; height:26px; top:14px; right:16px }`), `frontend/js/charts.js:32` (`d.responsive = true` global default), `frontend/js/pages/dashboard.js:152-160`
- **What:** Chart.js `responsive:true` sets inline `width/height` on the canvas to match its positioned parent (`.stat`), overriding the 64x26 CSS. Probe: every `.stat-spark` is `234x78` in a 276px card and its box overlaps the `.stat-value` box (`overlap: True`). Visually the trend line and gradient fill run straight through "$2,729.07" / "$6,900.00" (`a11y-admin-index-light-1440.png`, `a11y-admin-index-light-390.png`, and for empty data a flat line strikes through "$0.00" in `a11y-index-light-390.png`).
- **Repro:** Log in as admin, open index.html; look at the four KPI cards.
- **Fix:** In the sparkline builder set `options.responsive = false` (and pass explicit `canvas.width/height` × DPR), or wrap the canvas in a `<div class="stat-spark-wrap">` with the 64x26 box so Chart.js resizes to that instead of the card. Same applies to `charts.sparkline()` (reports "6 months" column, `.spark-cell canvas`).

### [P2] Horizontal page overflow on mobile: dashboard "Recent transactions" and insights cards
- **Area:** css
- **Where:** `frontend/css/app.css:83-88` (`.grid`, `.grid-2-1`, `.grid-1-1`), `app.css:273` (`.card-head` flex, no wrap), `css/pages/dashboard.css:15-24` (`.recent .list-item`), `css/pages/insights.css:5` (`.ins-grid`), `index.html:63-76`, `insights.html:25-31`
- **What:** `documentElement.scrollWidth > clientWidth` at 390px with admin data: dashboard 434 > 390, insights 490 > 390 (both themes). Culprits (elements outside any scroll container): dashboard `section.card` "Recent transactions" 418px wide — the `.grid-1-1` child cannot shrink below its content (`min-width:auto`), driven by `.recent .list-item` (392px) with `.rt-meta .truncate { max-width:160px }` + amount column; insights `div.col.gap-4` 474px — `.card-head` is `display:flex` with `h2` + `.card-actions .hint` ("Detected from repeating amounts and dates") that never wraps. Screenshot `a11y-admin-insights-light-390.png` shows the card and table cut off at the right edge.
- **Repro:** admin → index.html or insights.html at 390px; swipe horizontally.
- **Fix:** Add `.grid > * { min-width: 0 }` and `.col > * { min-width: 0 }` in app.css; let `.card-head { flex-wrap: wrap }` at ≤768 or hide `.card-actions .hint` on small screens (as reports.css already does for `.filter-row .hint`); give `.recent .rt-main` a real `flex: 1 1 0` and `.recent .list-item { min-width:0 }`.

### [P2] Transactions table empty/loading state is squeezed into the 28px checkbox column on mobile
- **Area:** css
- **Where:** `frontend/css/pages/transactions.css:66-68` (`.tbl-tx tr { display:grid; grid-template-columns: 28px 1fr auto 28px }`), `frontend/js/pages/transactions.js` (empty state and skeleton rows rendered as `<tr><td colspan="7">`)
- **What:** At ≤768 every `tr` becomes a 4-column grid. A single-cell row (`colspan=7`) is placed in the first 28px track: probe `tdWidth: 28, colspan: '7'`. The empty state renders as a 28px column with wrapped text and a clipped "Import a statement" button (`a11y-transactions-dark-390.png`). The same happens to `ui.skeletonRows` cells (each skeleton `td` lands in a track) and `#tx-foot` messages inside the table.
- **Repro:** qa_tester → transactions.html at 390px.
- **Fix:** `.tbl-tx td[colspan] { grid-column: 1 / -1; }` (and exclude `.skel-row`/`.empty` rows from the grid: `.tbl-tx tr:has(td[colspan]) { display:block }`), or render the empty state outside the `<table>` (in `#tx-foot`).

### [P2] Statements row actions are invisible on touch/mobile (hover-only affordance)
- **Area:** ux
- **Where:** `frontend/css/app.css:344-345` (`.row-actions { opacity:0 }` shown on `tr:hover`/`:focus-within`), `frontend/css/pages/statements.css:11-17` (mobile card layout has no `.row-actions { opacity:1 }` override)
- **What:** transactions.css, rules.css, categories.css, settings.css and insights.css (≤640 only) override the hover-only pattern on small screens; statements.css does not, so at 390px the per-statement actions (`td.col-actions`, grid column 2) render fully transparent. `a11y-admin-statements-light-390.png` shows blank space where the actions should be; a touch user has no way to reach them (no hover, no focus-within before the first tap). Insights `.rec-dismiss` has the same gap between 641–768px.
- **Repro:** admin → statements.html at 390px; try to open a statement's menu.
- **Fix:** In the ≤768 block of statements.css add `.tbl-statements .row-actions { opacity: 1 }`; in insights.css move the `.rec-dismiss { opacity:1 }` override to the 768 breakpoint. Longer term use `@media (hover: none) { .row-actions, .cat-actions, .rule-actions, .rec-dismiss { opacity: 1 } }` in app.css so one rule covers every table.

### [P2] Review: non-focused cards are dimmed to 72% opacity, pushing their text and buttons below 4.5:1
- **Area:** a11y
- **Where:** `frontend/css/pages/review.css:8` (`.rv-list.has-focus .rv-card:not(.is-focused) { opacity: .72 }`), `review.css:17` (`.rv-conf` = `--text-3` on `--info-soft`)
- **What:** Live walker on admin/review light 1440: dimmed `.btn-primary` "Accept" white on blended blue 2.99:1 (n=25), `.rv-meta .amt` and `.btn-ghost` "Skip"/"Show charges" 3.73:1 (n=80), `.rv-conf` "60% · built-in hints" 2.70:1 even undimmed (light) / 3.41 (dark). Screenshot `a11y-admin-review-light-1440.png` (2nd/3rd cards).
- **Repro:** admin → review.html; the first card gets focus automatically and every other card is dimmed.
- **Fix:** Dim with a background/border change instead of `opacity` (e.g. `.rv-card:not(.is-focused) { border-color: var(--border) }` and keep text at full opacity), or reduce to `opacity: .9`. Set `.rv-conf` to `--text-2`.

### [P2] `--text-4` is used for real content and fails contrast in both themes (2.5:1 light, 3.0:1 dark)
- **Area:** a11y
- **Where:** `frontend/css/tokens.css:21,113,160`; consumers: `app.css:119` `.sb-group-label`, `:353` `.merchant-raw`, `:364` `.stat-delta-vs`, `:402` `.menu-count`, `:404` `.menu-label`, `:436` `.palette-group`, `:527` `.tl-time`; `pages/categories.css:23,28` `.cat-count.is-zero`, `.cat-total.is-zero`, `.cat-sub-count`; `dashboard.css:12,22` `.legend-pct`, `.rt-date`; `review.css:14` `.rv-raw`; `rules.css:19,30,35` `.rule-prio`, `.rule-hits.is-zero`, `.rule-name`; `statements.css:7,10`; `insights.css:12,18,32`; `login.css:11`
- **What:** axe `color-contrast` [serious] is the top rule: 2,408 node hits over 78 runs; the majority are `--text-4` text. Static ratios: `#9aa3b2` on `#ffffff` 2.54, on `#f5f6f8` 2.35, on `#f2f4f7` 2.31; dark `#5d6678` on `#161a22` 3.02, on `#1c212b` 2.79. Live walker confirms the same numbers on `.sb-group-label` (n=212/theme), `.cat-total.is-zero` "—" (n=295), `.cat-count.is-zero` "0" (161), `.merchant-raw` (65), `.menu-count` (61), `.stat-delta-vs` (51), `.rt-date`, `.rule-name`, `.st-file-meta`, `.rv-raw` (1.89 light because it is also 11px mono). This is not decorative text: raw statement descriptions, dates, rule names, counts, sidebar group headings.
- **Repro:** Any page; e.g. categories.html → "0" and "—" columns; transactions.html → grey raw description under each merchant.
- **Fix:** Raise the token to pass 4.5:1 on `--surface` (light `#6f7a8c` ≈ 4.6:1, dark `#7f8898` ≈ 4.7:1), and reserve a separate `--text-disabled` for genuinely decorative uses. Alternatively stop using `.text-4` for content (`.merchant-raw`, `.rv-raw`, `.rule-name`, `.is-zero`, dates) and use `--text-3`.

### [P2] Light accent `#4f6ef7` is below 4.5:1 as text and as button background
- **Area:** a11y
- **Where:** `frontend/css/tokens.css:23-26`; `app.css:29` (`a`), `:199` `.btn-primary`, `:292` `.pill`, `:388` `.tab.active`, `:173` `.avatar`, `:289-293` `.badge-info`, `.badge-accent`, `.pill-soft`, `:310` `.catchip--suggested`, `:127` `.nav-item[aria-current]`
- **What:** Light theme ratios: white on `#4f6ef7` 4.28 (`.btn-primary`, `.pill`, login "Sign in"), `#4f6ef7` on white 4.28 (links, `.text-accent`, `.tab.active` on `--bg` 3.96), on `--accent-soft` 3.74 (`.avatar` "AD", `.pill-soft` count "9", `.badge-accent`, current nav item), on `--info-soft` 3.93 (`.badge-info` "admin", `.catchip--suggested` labels "Rent / Mortgage" n=45). axe flags `#login-btn`, `#import-btn`, `.pill` "293", `#tb-avatar` in every light run. Dark theme passes everywhere (6.25–6.93).
- **Repro:** Light theme, any page: primary buttons, review-count pill, suggested category chips.
- **Fix:** Darken the light accent to ≈`#3f5fe0` (white 5.0:1, on white 5.0:1) and `--accent-hover` to `#2f4ccc`; or keep `#4f6ef7` for fills but set text-on-soft to `--accent-hover` (`#3d5ce6`, 5.4:1 on white).

### [P2] `ui.multiFilter` menu: interactive controls nested and unlabeled, invalid `role=menu` children
- **Area:** a11y
- **Where:** `frontend/js/ui.js:261-314` (`multiFilter`), line 268 (`All`/`None` buttons + search input inside `role=menu`), line 271-272 (`<button role="menuitemcheckbox"><input type="checkbox" tabindex="-1">`)
- **What:** axe on transactions/cat-filter (both themes): `nested-interactive` [serious] 61 nodes (checkbox inside button), `label` [critical] 61 nodes (checkbox has no label), `aria-required-children` [critical] (`role=menu` contains `input[tabindex]` and `button[tabindex]` that are not menu items), plus the search `<input>` has only a placeholder. Screenshot `a11y-admin-transactions-cat-filter-light-1440.png`.
- **Repro:** transactions.html → click "All categories".
- **Fix:** Render the checkbox as a purely visual `<span class="check" aria-hidden="true">` (the button already carries `aria-checked`), give the search input `aria-label="Filter categories"`, and move the header (title, All/None) and footer buttons outside the element that has `role="menu"` (wrap the options list only in `role="menu"`, or use `role="group"` + `aria-label` for the whole popover and `role="checkbox"` on options).

### [P2] `dateRangePicker`: `menuitemradio` without a menu/group parent, date inputs without labels
- **Area:** a11y
- **Where:** `frontend/js/pickers.js:179` (`role="menuitemradio"`), `pickers.js:182-183` (`<input type="date" data-f="from|to">`)
- **What:** axe on transactions/range-picker: `aria-required-parent` [critical] 14 nodes ("Required ARIA parents role not present: menu, menubar, group"), `label` [critical] on both date inputs (placeholder-less, no `<label>`/`aria-label`). Screenshot `a11y-admin-transactions-range-picker-light-1440.png`.
- **Repro:** transactions.html → click "This month".
- **Fix:** Wrap the preset buttons in `<div role="group" aria-label="Presets">` (or use `role="radiogroup"`/`radio`), add `aria-label="From"` / `aria-label="To"` (or visible `<label for>`) to the two date inputs.

### [P2] Touch targets below the 24px WCAG 2.2 minimum in the transactions table
- **Area:** a11y
- **Where:** `frontend/css/pages/transactions.css:26` (`.catcell .sugg-act .btn { width:22px; height:22px }`), `app.css:248` (`.check` 16px, standalone in `td.col-check` — not inside a label), `categories.css:29` (`.cat-chevron` 20x24)
- **What:** axe `target-size` [serious] 144 nodes on transactions (accept/reject suggestion buttons 22x22 with 0px spacing). Walker at 390px: `input.check` 16x16 (row selection checkboxes, no wrapping label), `.cat-chevron` 20x24, `button.legend-item` 21px tall. Everything else is ≥24 but the app's own targets are mostly 26–36px (`.btn-xs` 26, `.seg-btn` 26, `.nav-item` 34, `.catchip` 26, `.tb-*` 36) — below the 44px recommendation on mobile (79 distinct selectors <44 at 1440; 60 at 390).
- **Repro:** admin → transactions.html; rows with a suggested category show ✓ ✕ at 22px.
- **Fix:** `.sugg-act .btn { width:26px; height:26px }` (or `min-width/height: 24px` + 2px gap), give `.check` a 24px hit area (`td.col-check label { display:grid; place-items:center; width:24px; height:24px }`), `.cat-chevron { width:24px }`. Consider `@media (pointer: coarse) { .btn-xs, .seg-btn, .catchip { min-height: 32px } }`.

### [P2] `--text-3` on tinted surfaces is just under 4.5:1 in light theme (table headers, segmented controls, page subtitles)
- **Area:** a11y
- **Where:** `frontend/css/tokens.css:20` (`--text-3: #6b7280`), `app.css:328` (`.tbl th` on `--surface-2`), `:380` (`.seg-btn` on `--surface-2`), `:181` (`.page-sub` on `--bg`), `:256` (`kbd` on `--surface-3`), `:489` (`.step-num`), `pages/review.css:17`
- **What:** Ratios: `#6b7280` on `--surface-2 #f2f4f7` 4.39 (all `th`, `.seg-btn`, `.rules-head`, `.cat-tree-head`), on `--bg #f5f6f8` 4.47 (`.page-sub`, `.hint` on page background, `.tab`, `.step`), on `--surface-3 #e9ecf1` 4.08 (`kbd` ⌘K/↓/S, `.step-num`). axe reports these in every light run (e.g. `#dash-sub` 4.47, `kbd` 4.08). Dark passes (5.2–5.6).
- **Repro:** Light theme, any table header or the dashboard "This month" segmented control.
- **Fix:** Nudge light `--text-3` to `#626b7a` (4.9 on surface-2, 4.7 on surface-3) or `#5f6773`; `--text-2` remains for labels.

### [P2] `.badge-danger` / `.error-box` text 4.41:1 in light theme
- **Area:** a11y
- **Where:** `frontend/css/tokens.css:31-32` (`--danger #dc2626`, `--danger-soft #fef2f2`), `app.css:287,469`
- **What:** `#dc2626` on `#fef2f2` = 4.41 (11px uppercase badge text, error-box copy). Dark passes (6.09).
- **Repro:** Trigger any error box (login with a wrong password) in light theme.
- **Fix:** Use `#b91c1c` for text on `--danger-soft` (5.9:1) via a `--danger-text` token, keep `#dc2626` for solid fills.

### [P2] `<select>` controls without an accessible name (import "Import into", settings display currency, mapping "skip rows")
- **Area:** a11y
- **Where:** `frontend/js/pages/import.js` (`#upload-account`, preceded by `<span class="text-3 fs-base">Import into</span>`; `input[type=number][data-map=skip_rows]` line 298), `frontend/js/pages/settings.js` (`#pref-currency`, preceded by `<div class="label">Display currency</div>`), `frontend/js/pages/transactions.js` (`#txd-notes` textarea labelled only by a `.section-label` div)
- **What:** axe `select-name` [critical] on `#upload-account` in all 12 import runs and on `#pref-currency` in settings/tab-appearance. The visible captions are `<span>`/`<div>`, not `<label for>`, and the controls have no `aria-label`.
- **Repro:** import.html → axe; settings.html → Appearance tab.
- **Fix:** Convert the captions to `<label for="upload-account">`, `<label for="pref-currency">`, `<label for="txd-notes">`, and add `aria-label="Rows to skip"` to the mapping number input.

### [P2] Chart palette slots c3/c4/c5 fail 3:1 against light surfaces
- **Area:** css
- **Where:** `frontend/css/tokens.css:44-46` (`--c3 #1baf7a`, `--c4 #eda100`, `--c5 #e87ba4`), consumers `.dot`, `.cat-icon`, donut/bar fills, `.share-bar`, `.acct-mark`
- **What:** Non-text contrast (WCAG 1.4.11) of the palette against `--surface #fff`: c3 2.82, c4 2.17, c5 2.69 (c2 3.20 borderline). As `.cat-icon` glyph on its 16% tint: c2 2.69, c3 2.40, c4 1.92, c5 2.32. `.acct-mark` prints white initials on the slot colour: on c4 that is 1.9:1. Dark palette passes every slot (3.06–5.68).
- **Repro:** categories.html light: yellow (c4) and pink (c5) category icons; any account using c4 shows an unreadable initial.
- **Fix:** Deepen the light slots (`--c3 #0f8f63` 3.9:1, `--c4 #c47f00` 3.1:1, `--c5 #d4587f` 3.6:1); for `.acct-mark` choose text colour by luminance (`color-mix` or a `--c-fg` per slot) instead of `#fff`.

### [P3] `<tr aria-expanded>` inside `role=table` (category breakdown)
- **Area:** a11y
- **Where:** `frontend/js/breakdown.js:67` area (`<tr class="bd-row bd-parent has-kids" aria-expanded tabindex="0">`), `breakdown.css:7,10`
- **What:** axe `aria-conditional-attr` [serious] 60 nodes on index/reports (both themes): `aria-expanded` is only valid on rows of a `treegrid`. The expand affordance is also a whole clickable row with no button.
- **Repro:** admin → index.html "Category breakdown".
- **Fix:** Put the toggle on a real `<button aria-expanded aria-controls>` inside `.bd-name` (the `.bd-chev` span), or set `role="treegrid"` on `.bd-table` with `role="row"`/`aria-level`.

### [P3] Login page has no `<main>` landmark; layer content sits outside landmarks
- **Area:** a11y
- **Where:** `frontend/login.html:14-33`; `frontend/js/ui.js` (modal/drawer/popover roots appended to `<body>`), `js/nav.js:56` (`.skip` link)
- **What:** axe `landmark-one-main` [moderate] on login (6 runs); `region` [moderate] 230 nodes: login content, the skip link, and every popover/menu/palette is outside `main/nav/header`. Mostly benign for dialogs, but login should be inside `<main>`.
- **Repro:** login.html → axe.
- **Fix:** Wrap `.login-card` in `<main class="login-wrap">`; move the skip link inside `.topbar` (it is still first in DOM order because the sidebar is `aside`).

### [P3] `<ol class="rule-list">` contains non-`<li>` children when empty/loading
- **Area:** a11y
- **Where:** `frontend/rules.html:32`, `frontend/js/pages/rules.js` (empty state / skeleton injected as `<div>` into `#rule-list`)
- **What:** axe `list` [serious] on rules for qa_tester (empty) in all 6 runs: "List element has direct children that are not allowed: div".
- **Repro:** qa_tester → rules.html.
- **Fix:** Render the empty/skeleton state into a sibling container (e.g. `#rules-error` / a `.rules-empty` div outside the `<ol>`), or wrap it in `<li role="presentation">`.

### [P3] Invalid ARIA roles on native elements (`label[role=button]`, `aside[role=dialog]`)
- **Area:** a11y
- **Where:** `frontend/js/pages/import.js:67` (`<label class="dropzone" role="button" tabindex="0">`), `frontend/js/ui.js:125` (`<aside class="drawer" role="dialog">`)
- **What:** axe `aria-allowed-role` [minor] on import (12 runs) and transactions/tx-drawer. `label` does not permit `role=button`; `aside` does not permit `role=dialog`.
- **Fix:** Make the dropzone a `<div role="button">` that forwards click/Enter/Space to the hidden file input (keep a real `<label for="file-input" class="sr-only">`), and render the drawer as `<div class="drawer" role="dialog">`.

### [P3] Empty `<th>` for action columns
- **Area:** a11y
- **Where:** `frontend/transactions.html:42`, `js/pages/statements.js:22,46`, `settings.js:42,304`, `insights.js:50`, `rules.html:26`, `import.js:331` (`<th class="col-check"></th>`)
- **What:** axe `empty-table-header` [minor] 20 nodes; screen readers announce blank column headers. None of the `<th>` use `scope="col"` (0 occurrences in html/js) — harmless for simple tables but recommended.
- **Fix:** `<th class="col-actions"><span class="sr-only">Actions</span></th>`, `<th class="col-check"><span class="sr-only">Select</span></th>`; add `scope="col"`.

### [P3] Icon-only buttons rely on `title` for their name
- **Area:** a11y
- **Where:** `frontend/js/pages/categories.js:64-66`, `settings.js:59,171,311`, `rules.js:86,356`, `transactions.js:224,239,286-287`, `insights.js:58`, `transactions.html:20` (`#btn-density`)
- **What:** ~20 `.btn-icon` templates carry only `title="More"`/`"Rename"`/`"Details"`. axe accepts `title` (no violation), but tooltips do not show on touch, and `title` is inconsistently read by screen readers; `transactions.js:286` (`data-bulk="more"`) has neither.
- **Fix:** Add `aria-label` alongside `title` (the `icon()` helper could accept a label), and give the bulk "more" button `aria-label="More actions"`.

### [P3] Text below 12px is widespread; 9–10px in the bottom nav, account marks and mini badges
- **Area:** ux
- **Where:** `frontend/css/app.css:548` (`.bn-item` 10px), `:317` (`.acct-mark` 10px), `:135` (rail `.pill` 10px), `:572` (`.avatar-xs` 9px), `pages/transactions.css:30` (`.badge-mini` 10px), `tokens.css:62` (`--fs-xs: 11px` used by `th`, `.badge`, `kbd`, `.section-label`, `.merchant-raw`, `.hint`-style meta on 69 distinct selectors)
- **What:** Walker found 69 distinct selectors rendering <12px; the biggest groups are `.sb-group-label` (11px), `kbd` (11px), `th` (11px uppercase), `.merchant-raw`/`.rv-raw` (11px monospace, also low contrast), bottom-nav labels (10px). Combined with `--text-4` colour this compounds legibility issues on the most data-dense screens.
- **Fix:** Raise `--fs-xs` to 12px, bottom-nav label to 11–12px, `.acct-mark` to 11px; keep 10px only for the rail pill (which is a count).

### [P3] No `prefers-reduced-motion` exemption for spinners; everything else is covered
- **Area:** a11y
- **Where:** `frontend/css/tokens.css:194-196` (durations → 0ms), `app.css:461` (skeleton shimmer off), `app.css:215,219` (`spin 0.7s` on `.spinner`, `.btn.is-loading::after`)
- **What:** Reduced-motion handling is good: all transitions/animations use `--dur-*` (become 0ms) and the shimmer is disabled. The only remaining infinite animation is the spinner, which is generally acceptable as an essential progress indicator. No finding beyond noting it; `html.theme-switching * { transition:none !important }` is a sensible use of `!important`.
- **Fix:** Optional: `@media (prefers-reduced-motion: reduce) { .spinner, .btn.is-loading::after { animation-duration: 1.5s } }`.

### [P3] Dark-theme details: floatbar hover, hard-coded whites, AI mark gradient
- **Area:** css
- **Where:** `frontend/css/app.css:499-505` (`.floatbar` bg `--text-1`, `.btn-ghost:hover rgba(255,255,255,.12)`, `.sep rgba(128,128,128,.4)`), `app.css:207` (`.btn-danger-solid color:#fff`), `:262` (`.switch-track::after #fff`), `:317` (`.acct-mark color:#fff`), `pages/insights.css:38` (`#b07bff`, `#fff`)
- **What:** In dark mode the floatbar background becomes near-white (`rgb(241,243,247)`, probe) so the white 12% hover overlay is invisible; the other hard-coded colours are the only non-token colours in the CSS (6 occurrences) and behave acceptably. Shadows, `color-scheme: dark` (native scrollbars, date/month pickers verified `colorScheme: dark`), `::selection`, `accent-color`, chart tooltip/grid colours all follow tokens. Both `[data-theme="dark"]` and `prefers-color-scheme` blocks are complete (identical 45 tokens; `--c6 #008300` is intentionally shared).
- **Fix:** `.floatbar .btn-ghost:hover { background: color-mix(in srgb, var(--bg) 12%, transparent) }`; replace `#fff` with `var(--surface)`/`var(--accent-fg)` and add `--ai-accent` for the gradient stop.

### [P3] Missing favicon, web manifest and print stylesheet
- **Area:** frontend-js
- **Where:** `frontend/*.html` `<head>`; nginx serves `/favicon.ico` → 404, `/manifest.json` → 404
- **What:** No `<link rel="icon">`/`manifest` on any page (browser requests 404 on every load; tabs show the default icon). No `@media print` rules: printing reports.html prints the fixed sidebar/topbar over the content and keeps the `.shell` margin-left. `meta[name=theme-color]` is present on all 11 pages and is updated by theme.js (verified `#f5f6f8` / `#0c0e13`).
- **Fix:** Add an SVG favicon (`<link rel="icon" href="/favicon.svg">` + `apple-touch-icon`) and `<link rel="manifest">`; add `@media print { .sidebar, .topbar, .bottomnav, .floatbar, #toast-root { display:none } .shell { margin-left:0 } .tbl-wrap { overflow: visible } }`.

### [P3] Dead CSS: 22 selectors never used in HTML or JS
- **Area:** css
- **Where:** see list
- **What:** `css_audit.py` tokenised every class in `frontend/*.html` and `frontend/js/**/*.js` (including `prefix-${}` dynamic patterns) and found these rules unused (verified with a word-boundary grep; `README.md` mentions are documentation only):

  | File:line | Selector |
  |---|---|
  | app.css:42 | `.tnum` |
  | app.css:49 | `.ico-lg` |
  | app.css:64 | `.fs-lg` |
  | app.css:78 | `.gap-6` |
  | app.css:79 | `.mt-6`, `.mt-8` |
  | app.css:80 | `.mb-1` |
  | app.css:82 | `.w-full` |
  | app.css:84-85, 88 | `.grid-2`, `.grid-3` |
  | app.css:148 | `html[data-sidebar="rail"] .sb-collapse-row` |
  | app.css:174 | `.avatar-lg` |
  | app.css:276 | `.card-foot` |
  | app.css:277 | `.card-flat` |
  | app.css:294 | `.pill-warning` |
  | app.css:301 | `.chip-warn` |
  | app.css:303 | `.dot-lg` |
  | app.css:349 | `.tbl-foot` |
  | app.css:476-477 | `.notice-success`, `.notice-success .ico` |
  | app.css:519, 559 | `.two-col` |
  | app.css:528-530, 558 | `.kv`, `.kv dt`, `.kv dd` |
  | pages/review.css:32 | `.rv-done` |

  Several are documented in `js/README.md` as vocabulary (`.grid-2/3`, `.card-foot`, `.kv`, `.two-col`, `.tbl-foot`, `.pill-warning`, `.ico-lg`) so they may be intentional API; the rest (`.tnum`, `.sb-collapse-row`, `.avatar-lg`, `.card-flat`, `.chip-warn`, `.dot-lg`, `.notice-success`, `.rv-done`, `.w-full`, `.mt-6/8`, `.mb-1`, `.gap-6`, `.fs-lg`) can be deleted.
- **Fix:** Remove the second group; either use or drop the documented ones.

### [P3] Duplicate / split rules and cross-file overrides
- **Area:** css
- **Where:** `frontend/css/app.css:271,280` (`.card-head` declared twice), `pages/reports.css:16,29` (`.compare-row` declared twice with conflicting `align-items`), `pages/dashboard.css:2-3` = `pages/insights.css:2-3` (`.stat-skel` copied), `pages/import.css:86` overrides `app.css:310` `.catchip--suggested` (dashed border only on the import page), `pages/transactions.css:56` vs `pages/rules.css:43` (`.rule-preview` styled differently in two page files for the same modal), `pages/dashboard.css:47` and `pages/insights.css:58` (`.stat-grid` 480px rule copied), `pages/transactions.css:61,81` (two adjacent `@media (max-width:768px)` blocks), `pages/dashboard.css:56-58` and `insights.css:4`, `reports.css:37` (ids `#month-btn`, `#month`, `#panel-cashflow` in stylesheets), `pages/settings.css:24-43` (`#settings-panels .tbl td…` 4-level chains).
- **What:** 6 `!important`s (all defensible: `[hidden]`, theme-switching, `.is-loading`, `.pt-0`, `.cat-side`, `.rv-card.is-leaving`). z-index scale is coherent (`th` 2 < `.commit-foot` 5 < topbar 200 < bottomnav 250 < sidebar 290/300 < rail tooltip 400 < floatbar 450 < drawer 550/560 < modal 600 < popover 650 < palette 700 < toast 800 < skip 1000); popovers correctly stack above modals/drawers. Universal `box-sizing: border-box` reset present; vendor prefixes limited to `-webkit-backdrop-filter`, `-webkit-appearance`, `::-webkit-details-marker` (fine).
- **Fix:** Merge the split declarations, move `.stat-skel`/`.stat-grid@480` into app.css, move `.rule-preview` and `.catchip--suggested` variants into app.css (or a `components/rule-form.css`), prefer classes over ids in page CSS.

### [P3] `.floatbar` is centred on the viewport, not on the content area
- **Area:** ux
- **Where:** `frontend/css/app.css:499` (`left: 50%; transform: translateX(-50%)`)
- **What:** With the 240px sidebar the bulk-action bar sits 120px left of the content's centre (unverified visually — derived from the CSS; the sidebar is `position:fixed` and `.floatbar` is fixed to the viewport).
- **Fix:** `left: calc(var(--sidebar-cur, 0px) + (100vw - var(--sidebar-cur, 0px)) / 2)`.

### [P3] Segmented control `#f-flow` (Debits/Credits) is `role=group` without pressed/checked state
- **Area:** a11y
- **Where:** `frontend/transactions.html:28`
- **What:** The two `.seg-btn` buttons use `title` for description and have no `aria-pressed`; unlike `#range-seg` they are not initialised through `ui.segmented()` in the markup (unverified whether transactions.js upgrades them at runtime — axe raised nothing, so the visual `.active` state may be the only indicator).
- **Fix:** Run `ui.segmented($('#f-flow'))` or add `aria-pressed` toggling.

---

## Contrast table (token maths, `css_audit.py`; live walker agrees within ±0.02)

Requirement: 4.5:1 text, 3:1 large text / UI. ✗ = fails.

| Pair | Light | Dark |
|---|---|---|
| `--text-1` on `--bg` / `--surface` | 16.4 / 17.7 | 17.4 / 15.7 |
| `--text-2` on `--surface` (labels, `p`) | 7.56 | 9.01 |
| `--text-3` on `--surface` (`.hint`, `.text-3`) | 4.83 | 5.63 |
| `--text-3` on `--bg` (`.page-sub`, `.tab`) | **4.47 ✗** | 5.9 |
| `--text-3` on `--surface-2` (`th`, `.seg-btn`) | **4.39 ✗** | 5.21 |
| `--text-3` on `--surface-3` (`kbd`, `.step-num`) | **4.08 ✗** | 4.65 |
| `--text-4` on `--surface` (`.merchant-raw`, `.sb-group-label`, `.stat-delta-vs`, `.rt-date`, `.is-zero`) | **2.54 ✗** | **3.02 ✗** |
| `--text-4` on `--bg` | **2.35 ✗** | **3.34 ✗** |
| `--text-4` on `--surface-2` (`.menu-count`, `.menu-label`) | **2.31 ✗** | **2.79 ✗** |
| Placeholder (`--text-4` on input bg) | **2.54 ✗** | **2.79 ✗** |
| Link / `.text-accent` / `.tab.active` (`--accent` on `--surface`) | **4.28 ✗** | 6.25 |
| Link on `--bg` | **3.96 ✗** | 6.93 |
| `.btn-primary` / `.pill` (`--accent-fg` on `--accent`) | **4.28 ✗** | 6.93 |
| `.btn-primary:hover` | 5.42 | 8.47 |
| `.badge-accent` / `.pill-soft` / `.avatar` / current nav (`--accent` on `--accent-soft`) | **3.74 ✗** | 4.93 |
| `.badge-info` / `.catchip--suggested` (`--accent` on `--info-soft`) | **3.93 ✗** | 5.84 |
| `.amt--income` / `.text-success` on `--surface` | 5.02 | 10.0 |
| `.badge-success` / `.chip-ok` (`--success` on `--success-soft`) | 4.76 | 8.95 |
| `.badge-danger` / `.error-box` (`--danger` on `--danger-soft`) | **4.41 ✗** | 6.09 |
| `.text-danger` / `.btn-danger` on `--surface` | 4.83 | 6.30 |
| `.badge-warning` (`--warning` on `--warning-soft`) | 4.84 | 9.23 |
| `.text-warning` on `--surface` | 5.02 | 10.4 |
| `.badge` default (`--text-2` on `--surface-3`) | 6.38 | 7.44 |
| `.floatbar` / rail tooltip (`--bg` on `--text-1`) | 16.4 | 17.4 |
| `::selection` (`--text-1` on `--accent-soft`) | 15.5 | 12.4 |
| `.rv-conf` (`--text-3` on `--info-soft`) (live) | **2.70 ✗** | **3.41 ✗** |
| Review dimmed card `.btn-primary` text (live, opacity .72) | **2.99 ✗** | **4.37 ✗** |
| Review dimmed card `--text-2` (live) | **3.73 ✗** | pass |
| `.acct-mark` white on `--chart-muted` | **2.54 ✗** | 4.83 |
| `.btn-secondary:disabled` text (opacity .5) | **3.38 ✗** (exempt) | 4.79 |
| `.btn-primary:disabled` text (opacity .5) | **1.96 ✗** (exempt) | **2.68 ✗** (exempt) |
| `.input:disabled` text (opacity .6) | 4.68 | 6.33 |
| Focus ring `--accent` vs `--surface` / `--bg` (3:1) | 4.28 / 3.96 | 6.25 / 6.93 |
| Input border `--border-strong` vs `--surface` (3:1) | **1.48 ✗** | **1.60 ✗** |
| Card border `--border` vs `--surface` (3:1) | **1.23 ✗** (decorative) | **1.24 ✗** (decorative) |
| Switch off-track `--border-strong` vs `--surface` (3:1) | **1.48 ✗** | **1.60 ✗** |
| Sort caret (`--text-3` @35%) vs `--surface-2` (3:1) | **1.55 ✗** | **1.78 ✗** |
| Palette `--c1..c12` fill vs `--surface` (3:1) | c3 **2.82 ✗**, c4 **2.17 ✗**, c5 **2.69 ✗**, c2 3.20, others 3.7–8.6 | all pass (3.5–5.7) |
| `.cat-icon` glyph on 16% tint (3:1) | c2 **2.69 ✗**, c3 **2.40 ✗**, c4 **1.92 ✗**, c5 **2.32 ✗**, others 3.1–6.5 | all pass (3.06–4.55) |

Focus visibility: global `:focus-visible` (2px `--accent` outline, offset 2) plus custom rings on inputs (`box-shadow --accent-ring`), switches (ring on `.switch-track`), `.rv-card.is-focused`, `.cat-row`, `.bd-row`, `.catchip`. Live tab trail (15 Tabs on every page, both themes, 3 viewports, plus 6 on login): **0 focused elements without a visible ring**; order is sidebar → skip link → topbar → page. Screenshots `*-focus.png`. Four `outline:none` declarations all supply a replacement ring (`.input:focus`, `.cat-name-input`, `.palette-input input`, `.rv-card` via `.is-focused`).

## axe-core violation table (132 runs: 11 pages × 2 themes × 3 viewports as qa_tester/anon + admin read-only at 1440/390 incl. 18 layer states)

| Rule | Impact | Node hits | Runs | Pages / states |
|---|---|---|---|---|
| color-contrast | serious | 2408 | 78 | all pages, all light runs; dark: `--text-4` text, `.rv-conf`, dimmed review cards |
| region | moderate | 230 | 92 | all (skip link, popover/menu/palette content, login) |
| link-name | serious | 216 | 24 | every page at 1024 (rail nav-items); `#btn-export`, `#btn-import` at 390 |
| target-size | serious | 144 | 8 | transactions (22px accept/reject), cat-filter, range-picker |
| label | critical | 130 | 4 | transactions/cat-filter (checkboxes), range-picker (date inputs) |
| nested-interactive | serious | 122 | 2 | transactions/cat-filter |
| aria-conditional-attr | serious | 60 | 12 | index, reports (`tr[aria-expanded]`), index/palette, user-menu |
| aria-required-parent | critical | 28 | 2 | transactions/range-picker (`menuitemradio`) |
| empty-table-header | minor | 20 | 18 | transactions, statements, settings, insights |
| aria-allowed-role | minor | 12 | 8 | import (`label[role=button]`), tx-drawer (`aside[role=dialog]`) |
| select-name | critical | 12 | 8 | import `#upload-account`, settings/appearance `#pref-currency` |
| button-name | critical | 8 | 4 | index `#month-btn`, transactions `#btn-add` at 390 |
| list | serious | 6 | 6 | rules (empty `<ol>` with `<div>` child) |
| landmark-one-main | moderate | 6 | 6 | login |
| aria-required-children | critical | 2 | 2 | transactions/cat-filter (`role=menu`) |

No axe injection errors; no console errors or page errors on any run.

## Overflow / responsive / touch-target table

| Check | Result |
|---|---|
| Horizontal overflow (`scrollWidth > clientWidth`) | 4 of 132 runs: admin index 390 (434px, "Recent transactions" card), admin insights 390 (490px, `.col.gap-4` cards with non-wrapping `.card-head`), both themes. All qa_tester runs and all 1024/1440 runs: none. |
| Tables without `.tbl-wrap` | `.mapping-table` (import) has its own `overflow-x:auto`; transactions/rules/categories switch to card grids ≤768/960; dashboard breakdown table (517px) scrolls inside `.tbl-wrap.bd-wrap` at 390 (contained). Insights `.rec-table` is inside `.tbl-wrap` but the card itself overflows (above). |
| Fixed widths | `.tbl-tx` min-width 760 (reset ≤768), `.cat-layout` 320px column (collapses ≤1100), `.settings-layout` 200px (collapses ≤960), `.rules-search` 220px (100% ≤960), inline `style="width:90px|max-width:…"` in 11 JS templates (harmless). |
| `.floatbar` vs bottom nav | ok: `bottom:72px` ≤768 clears the 60px bottom nav; `#toast-root` moves to `bottom:72px`; `.commit-foot` sticky `bottom:64px`. |
| Modal / drawer on mobile | ok: `.modal max-height calc(100vh - 16px)`, body scrolls; `.drawer` 100vw ≤768; palette padding 12vh. |
| Touch targets < 24px (WCAG 2.2 min) at 390 | `.sugg-act .btn` 22×22 (transactions), `input.check` 16×16 (row select, no label), `.cat-chevron` 20×24, `button.legend-item` 21px tall (reports). |
| Touch targets < 44px at 390 (distinct) | 60 selectors: `.btn-xs`/row actions 26, `.seg-btn` 26, `.catchip` 26, `.cat-icon` 24–28, `.sb-collapse` 28, `.select-sm`/`.input-sm` 30, `.nav-item` 34, `.btn`/`.tb-*` 36, `.tab` 38. Bottom nav items ≥ 52px (ok). |
| Hover-only affordances | `.row-actions` (generic, statements — no mobile override), `.rec-dismiss` (insights, override only ≤640), `.merch-name .ico` (rename pencil, has `:focus-visible` fallback), rail `::after` tooltip (hover only). `.cat-actions`, `.rule-actions`, `.tbl-tx .row-actions`, settings rows all have `:focus-within` + touch overrides. |
| Text < 12px | 69 distinct selectors; 9px `.avatar-xs`, 10px `.bn-item`, `.acct-mark`, `.badge-mini`, rail `.pill`; 11px `--fs-xs` everywhere else. |

## Dead-CSS list

See finding "Dead CSS: 22 selectors never used" above (table). Additional duplicates/overrides in "Duplicate / split rules".

## What is in good shape

- Token system: every colour token has light and dark values in both the `[data-theme]` and `prefers-color-scheme` paths; `c1..c12` resolved per theme; `prefers-contrast: more` swaps borders; only 6 hard-coded colours outside tokens.css. Spacing/radius/duration tokens are used consistently (page CSS uses a handful of literal px for micro-spacing only).
- Fonts: `@font-face` for InterVariable (100–900, `font-display: swap`), file present and served as `font/woff2` (352 KB); sensible system fallbacks; mono stack is system-only.
- HTML: `lang="en"`, viewport meta, unique `<title>` per page, exactly one `h1` per page, sensible h1→h2 order, `main`/`nav`/`header`/`aside` landmarks with labels, no duplicate ids on any page, labels bound on login (`autocomplete="username"/"current-password"`), every `<button>` has `type`, no `<a href="#">` buttons, `download` links are real links, script order matches the README skeleton on all 10 authenticated pages, `theme-color` updated per theme.
- Layers (ui.js): dialogs have `role=dialog aria-modal aria-labelledby`, focus trap + restore, Esc handling, `inert` on the shell; tabs/segmented controls implement roving tabindex; palette is a proper combobox/listbox with live region; toasts use `role=status/alert`.
- Motion: all transitions/animations keyed to `--dur-*` which collapse to 0 under reduced motion; shimmer disabled.
- Focus: visible ring on every focused element in 792 recorded tab stops.

## Summary

Counts: **P0 0 · P1 1 · P2 16 · P3 14** (31 findings).

Top 5:
1. **[P1] Rail-mode sidebar links have no accessible name** — `display:none` label + CSS-only tooltip; every page at 960–1279px (and any user who collapses the sidebar) exposes 11 unnamed links (`app.css:131`, `nav.js:44`). Fix with `.sr-only` or `aria-label`.
2. **[P2] `--text-4` (2.5:1 light / 3.0:1 dark) is used for real content** — raw descriptions, dates, counts, sidebar group labels, menu counts; drives most of the 2,408 axe `color-contrast` hits. Raise the token or stop using it for content.
3. **[P2] Light accent `#4f6ef7` fails 4.5:1** as button background, link, pill, avatar and suggested-chip text (4.28 / 3.74 / 3.93); dark passes. Darken the light accent or use `--accent-hover` for text.
4. **[P2] Dark theme `<select>` loses its chevron** (`background:` shorthand at `app.css:228-229` wipes the SVG) and **KPI sparklines are resized by Chart.js to the whole card, painting over the KPI values** (`charts.js:32` responsive default vs `dashboard.css:4`).
5. **[P2] Mobile layout defects with data**: horizontal page overflow on dashboard/insights at 390 (grid children lack `min-width:0`, `.card-head` never wraps), transactions empty/skeleton rows squeezed into the 28px checkbox track, statements row actions invisible on touch, page-action buttons lose their names when `.label` is hidden ≤640.

Also notable: `ui.multiFilter` and `dateRangePicker` generate invalid ARIA (nested checkbox in button, unlabeled inputs, `menuitemradio` without a menu parent); 22 dead selectors; no favicon/manifest/print styles. Focus visibility, landmarks, dialog semantics, motion handling and the two-theme token coverage are solid.
