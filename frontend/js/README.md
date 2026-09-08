# iSpend frontend — shared layer reference

Vanilla JS, no build step, classic `<script>` globals. Every authenticated page uses this skeleton:

```html
<head>
  <script src="/js/theme.js"></script>            <!-- synchronous, before CSS: no theme flash -->
  <link rel="stylesheet" href="/css/tokens.css">
  <link rel="stylesheet" href="/css/app.css">
  <link rel="stylesheet" href="/css/pages/<page>.css">
</head>
<body>
  <main id="main" class="main">…page content…</main>   <!-- nav.js wraps this in the shell -->
  <script src="/js/api.js"></script>
  <script src="/js/format.js"></script>
  <script src="/js/icons.js"></script>
  <script src="/js/store.js"></script>
  <script src="/js/ui.js"></script>
  <script src="/js/pickers.js"></script>
  <script src="/vendor/chart.umd.js"></script>   <!-- only pages with charts -->
  <script src="/js/charts.js"></script>           <!-- only pages with charts -->
  <script src="/js/nav.js"></script>
  <script src="/js/pages/<page>.js"></script>
</body>
```

`main` variants: `.main` (1200px), `.main--narrow` (880px), `.main--wide` (fluid). Page script shape:

```js
initNav('transactions').then(async (me) => { /* read qs(), hydrate, load() */ });
```

`initNav(page)` builds sidebar/topbar/bottom-nav around `#main`, resolves with the user (`window.currentUser`), never resolves when not authenticated (api() already redirected to `/login.html?next=…`), reveals `[data-admin-only]` elements for admins and removes them for everyone else (admin markup ships with `hidden` so it never flashes before `/api/auth/me` resolves), applies `preferences.theme/density` when the browser has no stored choice, and refreshes the Review count pill on `window` event `ispend:transactions-changed` (dispatch it after any categorization change: `window.dispatchEvent(new Event('ispend:transactions-changed'))`).

Pages known to nav: dashboard (`/index.html`), transactions, review, import, statements, categories, rules, reports, budgets, insights, settings, admin (admins only). Keyboard: `⌘K` / `/` palette, `g d|t|r|i|c|p|b|a|s` go-to, `[`/`]` sidebar, `?` shortcuts sheet (set `window.PAGE_SHORTCUTS = [{title, items:[[keys, desc], …]}]` to add page rows).

## theme.js — `window.Theme`
| Call | Purpose |
|---|---|
| `Theme.get()` → `'system'|'light'|'dark'` | stored mode (localStorage `ispend.theme`) |
| `Theme.effective()` → `'light'|'dark'` | what is painted |
| `Theme.set(mode)`, `Theme.toggle()` | apply + persist + dispatch `ispend:theme` |
| `Theme.density()`, `Theme.setDensity('compact'|'comfortable')` | `html[data-density]`, event `ispend:density` |

Theme is `html[data-theme="light|dark"]` (absent = follow system). Persist to the server with `api('/api/auth/me/preferences', {method:'PUT', body:{theme}})` — `setThemePref(mode)` in nav.js does both.

## api.js
| Helper | Purpose |
|---|---|
| `await api(path, {method, body, headers})` | JSON fetch; objects auto-stringified; `FormData` passes through; 401 → login redirect; non-2xx throws `Error(message)` with `.status`, `.data` |
| `await apiUpload(path, formData, {onProgress(0..1)})` | XHR multipart upload with progress |
| `esc(s)` | HTML-escape (use on EVERY interpolation) |
| `qs()` → `{k:v}`, `setQs({k:v}, {replace, merge})`, `toQuery(obj)` → `?a=1&b=2` | URL state helpers; empty/null/false keys are dropped, arrays joined with `,` |
| `$(sel, root)`, `$$(sel, root)`, `debounce(fn, ms)`, `uid()` | DOM/util |

## format.js
`fmtMoney(n, currency='USD', {compact, sign:'auto'|'always', abs, decimals})` (real minus U+2212, `+` for income with `sign:'always'`) · `fmtNumber(n, {decimals, compact})` · `fmtPct(0.12, {decimals, sign})` · `fmtDate('2026-09-02', {year})` → `Sep 2` · `fmtDateLong` · `fmtDateTime` · `fmtMonth('2026-09', {long})` → `Sep ’26` · `fmtRelative(iso)` → `2 days ago` · `toISODate(d)` · `fmtDelta(cur, prev)` → `{pct, dir:'up'|'down'|'flat', text}` · `fmtBytes(n)` · `initials(name)` · `plural(n, 'charge')`.

## icons.js
`ICONS[name]` raw svg string, `icon(name, extraClass)` (unknown names fall back to `tag`), `CATEGORY_ICONS` ordered names for the icon picker. Size via `.ico` (18px), `.ico-sm` (14), `.ico-lg` (24). Nav/UI names: layout-dashboard list inbox upload file-text tags sliders bar-chart lightbulb settings search sun moon monitor log-out chevron-(up|down|left|right) chevrons-(left|right) x check check-circle plus minus trash pencil more-horizontal more-vertical alert-triangle alert-circle info sparkles repeat arrow-(up|down|left|right|up-right|down-right|left-right) filter download eye eye-off grip-vertical menu user users lock unlock shield calendar clock refresh external-link copy undo play zap-off help keyboard split circle tag star trending-up trending-down pie-chart activity database file file-spreadsheet inbox-check, plus every category icon in `backend/seed_categories.py`.

## store.js — `store`
| Call | Notes |
|---|---|
| `await store.categories()` | tree `[{id,parent_id,name,slug,kind,color,icon,txn_count,children:[…]}]` |
| `await store.categoriesFlat()` | flattened, adds `parent_name`, `parent_color`, `path` (`Dining › Coffee`), `depth` |
| `store.accounts()` (all incl. archived), `store.tags()` (`[{id,name,color,txn_count}]`), `store.settings()` (`/api/settings/client`) | |
| `store.get(key, url, {ttl, force})`, `store.invalidate(key)` | generic cache; keys `categories`, `accounts`, `settings` |
| `store.on(event, fn)`, `store.emit(event, detail)` | `${key}-changed` fires after a revalidate changes data; also dispatched as `window` event `ispend:<event>` |

Cache is memory + sessionStorage, stale-while-revalidate. After mutating categories/accounts call `store.invalidate('categories')` (or `'accounts'`).

## ui.js — `ui` (+ global `toast`)
```js
const m = ui.modal({ title, html, size:'lg'|'xl', width, dismissible, actions:[{ label, primary, danger, onClick: async (m) => {…} /* throw → error toast; return false → keep open */ }], onClose });
m.close(); m.el; m.body; m.setTitle(t);
const ok = await ui.confirm({ title, body, confirmText, cancelText, danger }); // body is escaped; pass html: for markup
const d = ui.drawer({ title, html, foot, width, onClose }); d.setBody(html); d.setFoot(html); d.setTitle(t); d.setDirty(true); await d.close(); d.forceClose();   // one drawer at a time; with setDirty(true) Esc, X and backdrop ask before discarding
const p = ui.popover(anchorEl, contentEl, { placement:'bottom-start'|'bottom-end', matchWidth, onClose }); p.position(); p.close();
ui.menu(anchorEl, [{ label, icon, onClick, href, checked, danger, disabled, shortcut }, { divider:true }, { label, header:true }]);
ui.multiFilter(anchorEl, { title, options:[{value,label,count,color}], selected:new Set(), onChange(set), searchable });
// WAI-ARIA tabs: container holds role="tab" buttons; aria-controls/data-panel → panels get role=tabpanel + aria-labelledby.
// Arrow/Home/End move + select (roving tabindex); click selects. Re-apply after re-rendering the container.
const t = ui.tabs(tablistEl, { onChange: (tabEl, index) => showPanel(tabEl.dataset.panel) }); t.select(2, { focus:false, silent:true });
// Segmented control (.seg with .seg-btn children): role=radiogroup/radio + aria-checked, same keyboard model.
const seg = ui.segmented(segEl, { onChange: (btn) => setRange(btn.dataset.range) }); seg.current(); seg.select(i, { silent:true });
// Menus and multiFilter lists support ArrowUp/Down (wrapping), Home/End and first-letter type-ahead; focus returns to the anchor on close.
toast('Saved', { type:'success'|'error'|'info', action:{ label:'Undo', fn }, duration });   // window.toast; ≤3 visible, the rest queue; repeats bump a ×n counter
ui.undoable('3 excluded', async () => restore(before));   // success toast with a single-use Undo → 'Undone' (or an error toast)
// Undo contract: POST /api/transactions/bulk, /api/review/resolve, /pair and /auto-pair return `before` (util.SNAPSHOT_FIELDS per row);
// POST /bulk {action:'restore', ids, items: before} writes it back. Every reversible action gets Undo, confirms stay for delete/security.
await ui.busy(btn, async () => api(...));                  // disables + spins the button while fn runs; errors toast and resolve undefined ({ silent, rethrow })
ui.fieldError(inputEl, 'Name is required' | null);         // inline .field-error + aria-invalid + aria-describedby under the control's .field
ui.validate(formEl, [{ sel:'#name', message:'Name is required' }, { sel:'#amt', test:(v) => Number(v) > 0 || 'Enter an amount' }]) // paints errors, focuses the first; false when anything failed → `return false` from a modal action
ui.linkHints(rootEl);                                      // .field .hint → aria-describedby (modal() does it for its body)
// Tooltips: put data-tip="…" on any focusable control (never title=""); hover/focus show a role=tooltip bubble, Escape hides.
setPageTitle('Sep 2026 · Chase');                          // document.title = "<Page> · Sep 2026 · Chase · iSpend" (defined by initNav)
ui.skeleton(width, height) · ui.skeletonRows(n, cols) (tbody html) · ui.skeletonList(n)
ui.emptyState({ icon, title, body, action:{ label, href } | { label, act:'data-act-name' } })
ui.errorBox(message, { retry:'data-act-name' })
ui.shortcuts.register('c', fn, { when: () => bool, description }) · two-key chords 'g d' · auto-disabled while typing · ui.shortcuts.isTyping()
ui.shortcutsSheet(extraGroups) · ui.trapFocus(el) → untrap · ui.focusFirst(el) · ui.closeTop() · ui.layers (Esc closes topmost)
```
Segmented controls accept `allowNone: true` (clicking the active button clears it, `onChange(null, -1)`); calling `ui.tabs`/`ui.segmented` again on the same container replaces the old listeners.

Unhandled errors and rejected promises surface as an error toast (deduped for 5 s) and stay in the console. A 401 sends the browser to `/login.html?next=…&reason=expired` when this browser had signed in before.

Layers (modal/drawer/popover/palette) trap focus, close on Esc, and restore focus to the opener. Set `handle.allowShortcuts = true` on a layer if page shortcuts should keep working while it is open.

## pickers.js
```js
categoryPicker({ anchor, value: currentCategoryId, onPick: (cat|null) => {}, allowCreate:true, suggestedId, allowNone:false });
// ARIA combobox popover; groups Suggested / Recent (localStorage ispend.recentCats) / tree; typing ranks prefix > word-start > contains;
// "Create “x”" POSTs /api/categories and invalidates the store. `cat` has {id,name,color,parent_name,path,…}.
tagPicker({ anchor, selected:new Set(ids), onChange(set), allowCreate:true });   // multi-select with inline create; rows are role=checkbox
dateRangePicker({ anchor, value:{preset:'this-month'} | {from:'2026-01-01', to:'2026-01-31'}, onChange(value), allowAll });
rangeLabel(value) → 'Last month' | 'Jan 1 – Jan 31'; rangeToQuery(value) → {range} | {from,to}; rangeFromQuery(qs(), fallback); rangeDates(value) → {from,to} ISO
mountRangeButton(btnEl, value, onChange)   // renders a .btn-secondary trigger and wires the picker
mountPeriodNav(groupEl, value, onChange)   // ‹ [range] › — the trigger between two step buttons; the group holds
                                           // [data-period="prev"|"pick"|"next"] (markup class .month-nav)
stepPeriod(groupEl, value, dir, onChange)  // same step from a keyboard shortcut (← / →); false when there is no step
shiftRange(value, dir)                     // the window before/after this one, or null (all time, open-ended, or the
                                           // future). Whole months step by month, whole years by year, anything else
                                           // by its own span; the result normalises back to a preset when it matches one
```
Presets: this-week, last-week (week start from the account preference `week_start`, 0 = Sunday), this-month, last-month, last-30, last-90, this-year, last-year, all.

## charts.js — `charts` (+ globals `makeChart`, `destroyChart`, `catColor`)
```js
const chart = makeChart(canvas, (t) => ({ type:'bar', data:{…}, options: charts.barOptions(t, { currency, stacked }) }));
// registry re-runs the builder and chart.update('none') on ispend:theme, so resolve colors INSIDE the builder.
catColor('c4') / catColor(category) → hex for the current theme (fallback --chart-muted)
charts.withAlpha(hex, .2) · charts.gradientFill(ctx, hex, {from,to}) · charts.currencyTicks(cur) · charts.currencyTooltip(cur)
charts.barOptions(t, {currency, stacked, horizontal}) · charts.lineOptions(t, {currency}) · charts.sparkline(canvas, values, hex)
charts.htmlLegend(containerEl, chart, { values, currency, list:true, onClick(item) })   // .legend-item buttons; default toggles visibility
plugins: [charts.donutCenterPlugin(() => '$2,418', 'this month')]
```
Built-in legend is disabled globally; use HTML legends (`.chart-legend` or `.legend-list`). Chart canvases go inside `.chart-body` with `style="--h:260px"`.

## CSS vocabulary (app.css)
- Layout: `.page-head` (`h1` + `.page-sub` + `.page-actions`), `.card` (`.card-head` h2 + `.card-actions`, `.card-body`, `.card-foot`), `.grid.grid-2|grid-3|grid-2-1|grid-1-1`, `.row`, `.row-between`, `.col`, `.grow`, spacing `.mt-*`, `.mb-*`, `.gap-*`, text `.text-1..4`, `.fs-xs|sm|base|lg`, `.fw-500|600`, `.truncate`, `.mono`, `.num`, `.section-label`, `.hint`, `.divider`.
- Buttons: `.btn` + `.btn-primary|secondary|ghost|danger|danger-solid`, sizes `.btn-sm|xs`, `.btn-icon`, `.btn-block`, state `.is-loading`.
- Inputs: `.input`, `.select`, `.textarea`, `.input-sm`, `.input-group` (leading `.ico`, `.trailing` button + `.has-trailing`), `.field` (`label[data-required]` + control + `.hint` + `.field-error`, `.is-invalid`), `.field-row`, `.check`, `.radio-list > .radio-item.is-checked`, `.switch` (`input` + `.switch-track`, `.switch-sm`), `kbd`/`.kbd`.
- Badges/chips: `.badge.badge-success|danger|warning|info|neutral|accent`, `.pill` (`.pill-soft`, `.pill-warning`), `.chip` (`.active`, `.chip-ok|err|warn`), `.dot` (color via `style="--c:var(--c4)"`), `.catchip` (+ `.catchip--empty`, `.catchip--suggested`), `.cat-icon` (`.cat-icon-lg`), `.acct` + `.acct-mark`, amounts `.amt.amt--expense|income|transfer`.
- Tables: `.tbl-wrap > table.tbl` (sticky `th`, `th.sortable[aria-sort]`, `.col-check`, `.col-actions` + `.row-actions`, `tr.is-focused`, `tr[aria-selected=true]`, `tr.is-clickable`), `.tbl-toolbar`, `.tbl-summary`, `.tbl-sentinel`, `.tbl-wrap--flush` (inside a card), `.merchant > .merchant-name + .merchant-raw`. Density via `html[data-density=compact]`.
- Stats/charts: `.stat-grid > .stat` (`.stat-label`, `.stat-value`, `.stat-delta.stat-delta--good|bad` + `.stat-delta-vs`, `canvas.stat-spark`, `.is-loading`), `.card.chart-card > .chart-body[style=--h] > canvas` + `.chart-legend`/`.legend-list`, `.seg > .seg-btn.active`.
- Navigation: `.tabs > .tab.active` + `.tab-panel.active`, `.stepper > .step.active|done` (`.step-num`, `.step-line`).
- Layers (created by ui.js): `.modal`, `.drawer`, `.popover`, `.menu > .menu-item`, `.palette`, `.toast`, `.floatbar` (bulk/save bars: dark pill fixed bottom-center; put `.btn-ghost`/`.btn-primary` inside, `.sep` between groups).
- States: `.skel`, `.empty` (`.empty-icon`, `.empty-title`, `.empty-body`), `.error-box`, `.notice.notice-warning|info`, `.spinner`, `.progress > span[style=width]`.
- Import: `.dropzone.is-dragover` (`.dropzone-icon`, `.dropzone-title`, `.dropzone-sub`).
- Misc: `.list > .list-item`, `.swatches > .swatch.active[style=--c]`, `.icon-grid > button.active`, `.timeline > .tl-item` (`.tl-dot`, `.tl-text`, `.tl-time`), `.avatar`, `.m-0`.
- Breakpoints (the only widths allowed, checked by `css_audit.py --check`): 1280 (sidebar full), 960 (rail / off-canvas, `.btn.tb-menu` appears), 768 (bottom nav, drawer full-screen, card-mode tables), 640 (2-col stat grid, stacked headers), 480 (compact stats). Type sizes are rem tokens (`--fs-*`); radii `--r-xs|sm|md|lg|xl|pill`; never px literals for either.

Conventions: no inline `onclick`; delegate `click` on a container and dispatch on `data-act` (and `data-id`). Every interpolated string goes through `esc()`. Money is neutral for expenses and green for income; red is for errors/warnings only.

## Splits
UI: row chip `.catchip--split` (`data-split-edit`), `openSplitEditor(id)` (`ui.modal({ sheet:true })`, lines with `categoryPicker`, live remaining, "Put in last line"), drawer section with `[data-dact=split|unsplit]`, row menu "Split…/Edit split…/Remove split", filter chip `#f-split` (`?split=1`), palette "Show split transactions". `ui.modal({ sheet:true })` docks to the bottom on phones with a sticky footer.

`PUT /api/transactions/:id/splits {lines:[{category_id, amount, note}]}` (>= 2 lines, same sign, sum = amount; first line becomes the primary category), `DELETE` unsplits. Rows carry `split_count`; detail carries `splits[]`; `?split=1` filters. Category breakdowns (`by-category`, `monthly`, `month-over-month`) attribute by line; summary/trends/merchants keep the parent amount. Recategorising, rejecting or marking a transfer drops the split (event `split {removed}`).

## Tags
`/api/tags` CRUD; rows carry `tag_ids`; `PUT /api/transactions/:id {tag_ids}` replaces, bulk `tag`/`untag {tag_ids}` add or remove; list filter `?tag=1,2|none[&tag_mode=all]`, `facets.tags`. Chips: `.tagchip` (neutral surface + coloured dot). Tag manager lives on the Categories page.

## Budgets
`/api/budgets` (one row per category and month, `POST` upserts, `POST /copy {from,to}`), `/api/budgets/progress?month=` (`items[{category_id, budget, spent, remaining, pct, projected, pace_status}]`, `totals`, `unbudgeted`). `budgetsForRange(start, end)` (breakdown.js) returns a Map for a single-month range so the breakdown table can show a Budget column; the dashboard card and the category side panel call the same endpoints.

## Saved views
Transactions keeps `saved_views` in the account preferences (`[{id, name, query, pinned, page}]`, query = the filter string). `views.apply(v)` rewrites the URL and reloads; `?view=<id>` marks the active view and is dropped as soon as a filter changes. Pinned views paint under the Transactions link (`nav.js paintPinnedViews`, event `ispend:views-changed`).

## Query memory
`setQs()` also calls `rememberQuery()`: each page's last query (minus transient keys such as `open`, `statement`) is kept in sessionStorage. `initNav()` calls `restoreQuery()` before page scripts read `qs()`, and sidebar links carry `savedQuery(href)`, so filters, sorts, ranges and tabs survive navigating away and back within the session. Pages read state from `qs()` as before; nothing else to do.

## Admin
`/admin.html` (nav group "Admin", `g a`) is admin-only and mounts the same tabbed layout as Settings
(`.settings-layout` / `.settings-nav` / `.settings-section` / `.setting-row`, now in **app.css** so both
pages share them). Tabs are hash-routed: Overview, Users, Activity.

- **Guarding.** Nav items and groups marked `adminOnly` render `hidden` and are revealed only for admins;
  the ⌘K palette list and the `g <key>` registration filter on `role` too, because both are built before
  `/api/auth/me` resolves. `/admin.html` is a static file anyone can fetch, so the page also self-guards
  with an "Admins only" empty state — every endpoint is `@admin_required` regardless.
- **API** (`/api/admin/*`, all admin-only): `GET|POST /users`, `GET|PUT|DELETE /users/:id`,
  `PUT /users/:id/password`, `POST /users/:id/lock|unlock|restore`,
  `DELETE /users/:id?permanent=true&confirm=<username>`, `GET /stats/overview?days=`,
  `GET /audit` (keyset `cursor`, `format=csv`), `GET /audit/actions`, `GET|PUT /settings`.
  The user endpoints moved here from `/api/users` because DELETE changed meaning.
- **User lifecycle.** `active → locked → active`, `active|locked → deleted` (soft, hidden, restorable),
  `deleted → purged` (irreversible). Purge is reachable **only** from the trash and needs the username
  typed; the server re-validates the phrase. Restore returns a user to `locked` if they were locked
  before deletion. Locked and deleted produce the same 403 message on login so a password holder cannot
  tell them apart; a wrong password stays a generic 401.
- **Composition.** A sibling feature adds a tab with
  `window.AdminPanels.register(tab, { label, icon, load(hostEl) })` plus one `<script>` in `admin.html` —
  no edits to `admin.js`.
- **Charts** on Overview are counts, not money: `countScales()` replaces the currency ticks/tooltips from
  `charts.barOptions` / `charts.lineOptions`. All four series are gap-filled server-side so both charts
  share one x-axis.
- Storage is reported twice on purpose: `disk_bytes` (a volume scan — exact, includes OCR output and
  orphans) and `source_bytes` (the DB's view, de-duplicated by `file_sha256`). AI usage is reported in
  tokens, never dollars.
