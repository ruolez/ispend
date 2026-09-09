# iSpend QA operation — shared context for audit agents

- Repo: /Users/ruolez/Desktop/Dev/ispend (Flask backend in backend/, vanilla JS frontend in frontend/, nginx). Read frontend/js/README.md for frontend conventions. Read backend/app.py for blueprint list. Blueprint prefixes: settings/ai use `/api` (settings `/api/settings`), admin uses `/api/admin` (users endpoint is `/api/admin/users`), others `/api/<name>`.
- Live app (dev, docker compose, already running, DO NOT restart/rebuild containers): http://localhost:5559
  - admin / admin  (admin role, REAL personal data: 8 accounts, ~1238 transactions, 12 statements, 209 rules. NEVER delete/modify admin's transactions, accounts, statements, rules, categories or settings. Read-only use of admin is fine, plus creating/deleting QA users named qa_*.)
  - qa_tester / qa-tester-pass1 (user id 4, role user, EMPTY data, categories seeded) — use freely, mutate freely.
  - To get an additional isolated user: login as admin, POST /api/users {"username":"qa_<x>","password":"...(>=10)","role":"user"}.
- Login: POST /api/auth/login {"username","password"} → session cookie. Logout POST /api/auth/logout. Me: GET /api/auth/me.
- Python venv with playwright (chromium installed), pytest, requests, ruff, pyflakes:
  /private/tmp/claude-501/-Users-ruolez-Desktop-Dev-ispend/272cd059-cdbc-40ed-a58b-b1f0ef765b8f/scratchpad/qa-venv/bin/python  (and .../bin/pytest, .../bin/ruff, .../bin/pyflakes)
- Sample statement fixtures: backend/tests/fixtures/*.csv, *.xlsx, *.xls (chase_card.csv, amex.csv, generic.xlsx, ...). make_fixtures.py shows how they were made.
- Backend unit tests run inside the container: `docker compose exec -T backend python -m unittest discover -s tests` (289 tests; 1 known OCR e2e failure).
- Put all test code under qa/e2e/, screenshots under qa/reports/screenshots/, and your findings report at qa/reports/<your-area>.md.

## Findings report format (strict — it gets merged)
Markdown, one section per finding:

### [P0|P1|P2|P3] <short title>
- **Area:** backend|api|frontend-js|css|ux|a11y|security|perf|tests|docs
- **Where:** file:line (or page URL / element)
- **What:** what is wrong, with evidence (error text, screenshot filename, request/response)
- **Repro:** minimal steps
- **Fix:** concrete suggested fix

Severity: P0 = data loss/security/crash on main path; P1 = broken feature or wrong numbers; P2 = degraded UX, inconsistency, minor bug; P3 = polish/nit. Be concrete, no speculation without evidence; if unverified say "unverified". At the end add a `## Summary` with counts per severity and the 5 most important items.

## Added 2026-09-07 (UI/UX programme)
- `qa/e2e/test_phone.py`: 390px inner-overflow / single-line-amount checks per page and keyboard probes for segmented controls and tabs. Known defects are `xfail(strict=True)`; a fix must remove the mark.
- `qa/e2e/test_css_audit.py` + `python qa/e2e/css_audit.py --check`: static design-system rules (rem type scale, breakpoint ladder 1280/960/768/640/480, radius/colour tokens, one dark token block).
- `qa/e2e/report_snapshot.py capture|verify --user <u> --dir <d>` and `test_report_snapshot.py`: byte-compare every /api/reports payload. Nothing is committed — `test_flows.py` recreates qa_flows each run, so its ids (and payloads) change. Capture before a migration and verify after, using `--user admin` against a directory outside the repo; `test_report_snapshot.py` skips unless `REPORT_SNAPSHOT_DIR` points at one.

## Added 2026-09-07 (features)
- Pages: `/budgets.html` (nav "Budgets", `g b`). Tags manager card on `/categories.html`. Split editor is a modal on `/transactions.html` (`ui.modal({sheet:true})` → bottom sheet on phones).
- API: `/api/budgets` (+ `/progress`, `/copy`), `/api/tags`, `PUT|DELETE /api/transactions/<id>/splits`, bulk `restore|tag|untag`, list params `tag`, `tag_mode`, `split`; rows carry `tag_ids`, `split_count`, `before` snapshots on bulk/pair/resolve.
- Preferences: `saved_views`, `review_skips`, `onboarding`, `week_start`, `default_account_id`.
- Flows F13 (budgets), F14 (tags), F15 (splits); `report_snapshot.py verify` must stay byte-identical after migrations 007–009.

## Added 2026-09-08 (admin console)
- Page: `/admin.html` (nav group "Admin", `g a`, admins only) with hash-routed tabs Overview / Users / Activity.
  Added to `helpers.PAGES` and `CHART_PAGES`, so it is already in the smoke, phone and a11y matrices.
  The `qa_tester` persona is deliberately not an admin: that combination must render the "Admins only"
  empty state with no console errors and no `undefined`/`NaN` text.
- API moved: `/api/users*` → `/api/admin/users*`. `DELETE` changed meaning (it used to deactivate, it now
  soft-deletes), which is why the path moved rather than staying put.
- **Purging a user is two steps** and `conftest._purge_user` does both:
  `DELETE /api/admin/users/<id>` (to the trash), then
  `DELETE /api/admin/users/<id>?permanent=true&confirm=<username>`.
  The server also refuses to purge while one of that user's imports is still `parsing`/`committing`
  (a 10-minute window matching `OCR_TIMEOUT_SECONDS`), so the helper retries for 60s. A statement stuck
  in `parsing` from a killed job is cleared by `importer.recover_interrupted()` on the next backend start.
- New endpoints: `GET /api/admin/stats/overview?days=7|30|90|365[&refresh=1]` (cached 300s in the
  `settings` table), `GET /api/admin/audit` (keyset `cursor`, `format=csv`), `GET /api/admin/audit/actions`,
  `GET|PUT /api/admin/settings`, `GET /api/admin/users/<id>` (per-user detail).
- `users.is_active` is now a **generated** column derived from `users.status`; write `status` instead.
- Lost every admin? `docker compose exec backend python -c "import db; db.promote_admin('admin')"`.

## Added 2026-09-08 (backup & restore)
- Admin tab **Backup** (`js/pages/admin-backup.js`, registered through `window.AdminPanels`).
- API `/api/admin/backup*`. Uploading an archive only *inspects* it; a restore additionally needs
  `{"confirm": "RESTORE"}` and is refused while any statement is `parsing`/`committing`.
- **Never run a restore against the dev stack** — it truncates every table. The round-trip is covered
  by `backend/tests/test_backup_roundtrip.py`, which creates its own scratch databases:
  `ISPEND_TEST_DSN=postgresql://ispend:<pw>@postgres/ispend python -m unittest tests.test_backup_roundtrip`
  (run on its own; under `discover` the db module is stubbed and it skips).
- While a restore runs, `/api/*` returns 503 except `/api/health` and `/api/admin/backup/restore/<id>`.
  A sentinel file at `{STATEMENTS_DIR}/_backups/.restore-in-progress` drives it and is cleared at boot.
- A restore rotates `settings.session_epoch`, which invalidates every session cookie. The check in
  `auth.refresh_session_user` only applies once that key exists, so upgrading never signs people out.
- `nginx` location for backups deliberately has **no trailing slash**: a prefix location ending in `/`
  with `proxy_pass` makes nginx 301 the slash-less URI, which broke `/api/admin/backup`.
- `USE_X_ACCEL` defaults off (Flask streams the artifact, mode 0600). Turning it on lets nginx serve
  the download but requires mode 0644, because the nginx and backend images share no group.

## Added 2026-09-08 (billing, signup, email)
- Pages: `/billing.html` and the unauthenticated `/signup.html`, `/forgot.html`, `/reset.html`,
  `/verify.html`. `billing` is in `helpers.PAGES`; the public ones are in `a11y_scan.PAGES`.
- **Everything is inert unless Stripe is configured.** `billing.enabled()` is false without a
  secret key + monthly price, and then `entitlement.evaluate()` returns `active` for everyone.
  Sign-ups are off unless `settings.signup_enabled = '1'`. The dev stack and CI run this way.
- Migration 012 marks every pre-existing account `comped_until = 'infinity'`, so turning billing
  on never locks out the people already using the install.
- **A password change bumps `users.session_epoch`, which signs every OTHER session out.** A test
  that changes a password through the UI and then reuses a separate API session will get 401s —
  see the end of `test_f10_settings` for the pattern (restore, then rebuild both sessions).
- A restore rotates the *global* `settings.session_epoch`; a password change bumps the *per-user*
  `users.session_epoch`. Both are checked in `auth.refresh_session_user`, and both only bite once
  they have been set, so upgrading signs nobody out.
- Writes are blocked by `entitlement.enforce_write_access`, a `before_request` that denies every
  non-GET outside `WRITE_ALLOWLIST`. Adding a mutating endpoint needs no change; adding a
  *read-only* POST does. `tests/test_entitlement.py` asserts every allowlisted path resolves to a
  real route.
- Stripe is stubbed in tests by `_stubs.install_stripe()`, which must be called before `billing`
  or `billing_api` are imported.
