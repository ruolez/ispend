# iSpend

Self-hosted personal finance analyzer. Upload bank and credit-card statements (CSV, Excel, PDF, even scanned PDFs), let iSpend extract and categorize every charge, and see where the money goes: spending by category, monthly trends, top merchants, recurring subscriptions, anomalies and optional AI-written insights.

- **Import anything**: CSV / XLSX / XLS exports and PDF statements. Text PDFs are parsed directly; scanned PDFs go through OCR (Tesseract via OCRmyPDF) automatically.
- **Bank aware**: built-in profiles for Chase, American Express, Capital One, Bank of America, Citi, Discover, PNC, Wells Fargo, RBC, TD, BMO and Scotiabank, plus a generic column detector with a mapping editor for anything else.
- **Learns as you go**: rules (contains / starts with / equals / regex, amount ranges, per account) run first, then remembered merchant choices, then optional AI suggestions. Everything you confirm is remembered for the next import.
- **Review queue**: unknown charges grouped by merchant so one decision categorizes dozens of rows, with "always do this" creating a rule.
- **Duplicate safe**: overlapping statements and re-uploads are detected before anything is written.
- **Multi-user**: admin-managed accounts, every user sees only their own data.
- **Light and dark themes**, keyboard driven, no build step.

## Stack

nginx (port 5559) → Flask + gunicorn (Python 3.12) → PostgreSQL 16, all in Docker Compose. Frontend is plain HTML/CSS/JS served by nginx with Chart.js vendored locally.

## Quick start (Ubuntu 22.04 / 24.04 LTS)

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/ruolez/ispend/main/install.sh)"
```

The installer menu offers:

| Option | What it does |
|---|---|
| **Install** | Installs Docker if needed, clones the repo to `/opt/ispend`, generates secrets into `.env`, tunes container memory to the host, builds the images and starts the stack. Asks for a **domain name** and issues a **Let's Encrypt certificate** (HTTP on port 80 redirects to HTTPS on 443); answer "n" to run plain HTTP on a port of your choice instead. Prints the admin password when done. |
| **Update** | Backs up the database and uploaded statements, pulls the latest code from GitHub, rebuilds the images while the old stack keeps serving, then swaps to the new build. Users, accounts, transactions, settings and the certificate are all kept; database migrations apply automatically on startup; unused Docker images are pruned. |
| **SSL** | Installs or repairs HTTPS for an existing installation: prompts for the domain, issues (or reuses / force-renews) the certificate and switches nginx to HTTPS. |
| **Remove** | Stops and removes the containers, optionally after a final backup; asks separately before deleting data volumes and the certificate. |

Non-interactive: `install.sh install|update|ssl|remove`.

Certificates are issued with certbot in webroot mode (nginx serves `/.well-known/acme-challenge/` from `certbot-www/`) and renewed automatically by `certbot.timer`; a deploy hook reloads nginx inside the container after each renewal. HTTPS installs set `SESSION_COOKIE_SECURE=1`.

## Local development

```bash
cp .env.example .env            # edit the secrets
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
open http://localhost:5559      # admin / ADMIN_INITIAL_PASSWORD
```

The dev overlay bind-mounts `backend/` with gunicorn `--reload`; the frontend is always served straight from `frontend/` with no-cache headers, so edits show on refresh.

Run the backend test suite (no database needed; pure modules and stubbed APIs):

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/python -m unittest discover -s tests -v
# or inside the container
docker compose exec backend python -m unittest discover -s tests
```

An opt-in integration suite runs the transfer trigger, duplicate detection and statement rollback against a real Postgres (it creates and drops a scratch database):

```bash
docker compose exec -e ISPEND_TEST_DSN=postgresql://ispend:$POSTGRES_PASSWORD@postgres/ispend backend \
  python -m unittest tests.test_db_integration
```

The `qa/` directory holds the QA audit: `qa/IMPROVEMENT_PLAN.md` (findings and phased fixes), the reports behind it, and black-box suites (`qa/e2e/test_api.py`, `test_smoke.py`, `test_flows.py`) that run against the dev stack; see `qa/CONTEXT.md`.

## How an import works

1. **Upload** a file on the Import page (drag and drop, several at once). The file is stored under the `statements` volume and parsed in the background.
2. **Preview**: iSpend shows the detected bank, lets you pick the target account, edit the column mapping for CSV/Excel files, flip the sign convention if charges look inverted, and flags rows that already exist (duplicates are skipped by default).
3. **Commit**: rows become transactions. Rules and merchant memory categorize what they can; if AI is enabled, the rest receive *suggested* categories you confirm in the Review queue.
4. **Review** groups by merchant, one keystroke per decision. Confirmations feed merchant memory so the next statement needs less work.

Sign convention everywhere: negative = money out, positive = money in, regardless of how the bank exports it.

## AI (optional)

Settings → AI: paste an [OpenRouter](https://openrouter.ai) API key, pick a model from the live catalog (context length and price per million tokens are shown), and switch on category suggestions and/or written insights. Nothing is sent anywhere unless a key is saved and the switches are on. Suggestions are never auto-confirmed. Each call is logged with token counts.

## Configuration

`.env` holds only infrastructure secrets:

| Key | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | database password |
| `SECRET_KEY` | Flask session signing key |
| `ADMIN_INITIAL_PASSWORD` | password of the `admin` user created on first start (only used once) |
| `APP_PORT` | port nginx listens on (default 5559) |

Everything else (API keys, AI switches) lives in the database and is edited in Settings. API keys are masked in every response.

## Project layout

```
backend/            Flask app: auth, accounts, categories, statements (import), transactions,
                    review, rules, merchants, reports, ai, admin; importer/ and bank_profiles/
                    packages; migrations/*.sql applied at startup; tests/
frontend/           static pages + css/ js/ vendor/ fonts/
nginx/nginx.conf    static files + /api proxy
docker-compose.yml  production; docker-compose.dev.yml overlay for live reload
install.sh          Ubuntu installer / updater
```

## Budgets, tags and splits

- **Budgets** live in `budgets` (one row per category and month). `/api/budgets/progress` composes the spending breakdown with pace maths (`backend/budgets.py`); the Budgets page and the dashboard card read it. Parent and child categories cannot both carry a budget for the same month.
- **Tags** (`tags`, `transaction_tags`) are free labels across categories. Rows carry `tag_ids`; filter with `?tag=1,2` (`&tag_mode=all`) or `?tag=none`; the CSV export has a `Tags` column.
- **Splits** (`transaction_splits`) divide one row over several categories. The parent keeps its amount and a primary category (the first line); the category breakdowns (`by-category`, `monthly`, `month-over-month`) attribute by line while totals, trends and merchants keep the parent amount. Lines must be at least two, share the transaction's sign and add up to its amount — the API validates and a deferred trigger enforces it. Recategorising, rejecting a suggestion or marking a transfer removes the split.

## Administration

Admins get an **Admin** page (`/admin.html`) with three tabs:

- **Overview** — users, sign-ins, transactions and statements over time, storage, AI usage and bank
  profiles. Storage is reported twice: *on disk* (a scan of the statements volume, so it includes OCR
  output and orphaned files) and *uploaded originals* (the database's own accounting, de-duplicated by
  content hash). AI usage is reported in tokens, never an estimated cost.
- **Users** — create, reset a password, change role, and move an account through its lifecycle:
  **active → locked** (signed out at once, data intact) → **trash** (hidden and unable to sign in, but
  restorable) → **deleted permanently** (rows and uploaded files destroyed). Permanent deletion is only
  possible from the trash and asks you to type the username; locking and trashing are undoable.
- **Activity** — the `audit_log`, filterable by user, action and text, exportable as CSV. Retention for
  the log and for the trash is configurable on the Overview tab.
- **Backup** — see below.

### Backing up and moving servers

**Backup** produces one ZIP holding every table's rows plus every uploaded statement, and hands it to
you as a download. **Restore** replaces everything on a server with the contents of such an archive,
which is how you migrate to new hardware: install iSpend there, sign in as the seeded admin, upload
the archive.

- The archive carries **data only**. Schema comes from the migrations on the target, so an archive
  made months ago restores into a freshly updated server. An archive from a *newer* build is refused
  by name rather than half-applied.
- Restoring is a two-step flow: uploading only inspects the archive and shows a per-table diff of what
  would be replaced; nothing changes until you type `RESTORE`. A backup of the current data is taken
  automatically first, and the whole load runs in one transaction, so a failure leaves the previous
  data in place.
- While a restore runs the app returns 503 to everyone. When it finishes every session is invalidated
  — including yours — because the user accounts have been replaced.
- **The archive contains password hashes and your OpenRouter API key in plain text.** Store it like a
  password database. It does *not* contain `SECRET_KEY` or the database password; a stolen archive
  therefore cannot be used to forge sessions on the server it came from.
- `install.sh` keeps its own host-level `pg_dump` backups under `/opt/ispend-backups` for disaster
  recovery. The in-app archive is the migration tool; the two are complementary.

If an instance ever loses its last administrator (manual SQL, a restore), promote one from the host:

```bash
docker compose exec backend python -c "import db; db.promote_admin('admin')"
```

## Security notes

- Session cookies (HttpOnly, SameSite=Lax, Secure when installed with HTTPS); use the installer's Let's Encrypt option when the app is reachable from the internet.
- Uploaded statements are stored in a private Docker volume and are only downloadable by their owner.
- Locked and deleted accounts return the same message at sign-in, so a password holder cannot tell which happened; a wrong password is always the generic "Invalid username or password".
- Every mutating action is written to `audit_log`.
