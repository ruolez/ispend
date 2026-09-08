# iSpend QA operation — shared context for audit agents

- Repo: /Users/ruolez/Desktop/Dev/ispend (Flask backend in backend/, vanilla JS frontend in frontend/, nginx). Read frontend/js/README.md for frontend conventions. Read backend/app.py for blueprint list. Blueprint prefixes: settings/ai use `/api` (so users endpoint is `/api/users`, settings `/api/settings`), others `/api/<name>`.
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
- `qa/e2e/report_snapshot.py capture|verify --user <u> --dir <d>` and `test_report_snapshot.py`: byte-compare every /api/reports payload. The qa_flows capture is committed under `qa/fixtures/report-snapshots/qa_flows/` (re-capture after `test_flows.py` when a report change is intended); keep admin captures outside the repo.
