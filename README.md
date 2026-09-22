# iSpend

Self-hosted personal finance analyzer. Upload bank and credit-card statements (CSV, Excel, PDF, even scanned PDFs), let iSpend extract and categorize every charge, and see where the money goes: spending by category, monthly trends, top merchants, recurring subscriptions, anomalies and optional AI-written insights.

- **Import anything**: CSV / XLSX / XLS exports and PDF statements. Text PDFs are parsed directly; scanned PDFs go through OCR (Tesseract via OCRmyPDF) automatically.
- **Bank aware**: built-in profiles for Chase, American Express, Capital One, Bank of America, Citi, Discover, PNC, Wells Fargo, RBC, TD, BMO and Scotiabank, plus a generic column detector with a mapping editor for anything else.
- **Learns as you go**: rules (contains / starts with / equals / regex, amount ranges, per account) run first, then remembered merchant choices, then optional AI suggestions. Everything you confirm is remembered for the next import.
- **Review queue**: unknown charges grouped by merchant so one decision categorizes dozens of rows, with "always do this" creating a rule.
- **Duplicate safe**: overlapping statements and re-uploads are detected before anything is written.
- **Multi-user**: admin-managed accounts, every user sees only their own data.
- **Light and dark themes**, keyboard driven, no build step.
- **A front door**: `/` is a public landing page whose pricing block is driven by the admin console; the app itself lives at `/index.html`.

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

### Sharing the server with other apps

On a machine whose ports 80/443 already belong to another app, **Install** offers to put iSpend behind the [shared proxy](https://github.com/ruolez/shared-proxy) instead — a host-level nginx that terminates TLS for every app and forwards `https://<domain>` to this stack on `127.0.0.1:5559`. The check runs before anything is cloned: while another app's *container* still holds the ports the installer stops with instructions (move that app behind the proxy first) and changes nothing.

In this mode `.env` holds `PROXY_MODE=1` and `COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml`. The certificate is issued through the proxy's webroot (`/var/www/certbot`), the vhost is rendered from `nginx/host-vhost.conf.template` to `/etc/nginx/sites-available/ispend.conf` and validated by nginx before it goes live, and `APP_BASE_URL` is set for Stripe. The container's nginx keeps its whole job (rate limits, security headers, X-Accel downloads) and gets `nginx/realip.conf` so per-IP limits still see real visitors. Memory limits are sized from `HOST_MEM_BUDGET_MB` — what was free at install time — rather than the whole machine; edit it in `.env` and run Update to re-tune. Update, SSL and Remove all handle the mode on their own.

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
2. **Preview**: iSpend shows the detected bank, lets you pick the target account, edit the column mapping for CSV/Excel files, flip the sign convention if charges look inverted, and flags rows that already exist (duplicates are skipped by default). A column mapping is remembered per file layout and account the moment you apply it (not only when you import), so the next export with the same columns is mapped automatically and goes into the same account; an account without its own saved mapping borrows the one used most recently for those columns, and imports made before this existed are picked up once at startup (saved layouts are listed under Settings → Accounts).
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
frontend/           static pages + css/ js/ vendor/ fonts/; landing.html is the public front page
nginx/nginx.conf    static files + /api proxy
docker-compose.yml  production; docker-compose.dev.yml overlay for live reload
install.sh          Ubuntu installer / updater
```

## Budgets, tags and splits

- **Budgets** live in `budgets` (one row per category and month). `/api/budgets/progress` composes the spending breakdown with pace maths (`backend/budgets.py`); the Budgets page and the dashboard card read it. Parent and child categories cannot both carry a budget for the same month.
- **Tags** (`tags`, `transaction_tags`) are free labels across categories. Rows carry `tag_ids`; filter with `?tag=1,2` (`&tag_mode=all`) or `?tag=none`; the CSV export has a `Tags` column.
- **Splits** (`transaction_splits`) divide one row over several categories. The parent keeps its amount and a primary category (the first line); the category breakdowns (`by-category`, `monthly`, `month-over-month`) attribute by line while totals, trends and merchants keep the parent amount. Lines must be at least two, share the transaction's sign and add up to its amount — the API validates and a deferred trigger enforces it. Recategorising, rejecting a suggestion or marking a transfer removes the split.

## The landing page

`/` serves `frontend/landing.html` — a public marketing page — while the app stays at
`/index.html`, which is what every link, redirect and test already points at. nginx does this with
one exact-match location, so nothing inside the app had to move.

- It is **dark-locked** (`<html data-theme="dark">`, no `theme.js`) and loads nothing from anywhere
  else: no fonts, no scripts, no analytics. The product shot in the hero is markup built from the
  same design tokens as the app, not a screenshot that goes stale.
- Everything it needs comes from one anonymous endpoint, `GET /api/public/landing`: whether
  sign-ups are open, the trial length, the live Stripe prices (amount, currency, interval — never a
  `price_id`) and the editable copy below. A Stripe outage degrades it to "no plans" rather than an
  error, and a signed-in visitor gets an **Open iSpend** button instead of a redirect.
- The wording of the pricing block — heading, sub-heading, plan name, tagline, ribbon, bullet
  points, button label and the small print — is edited in **Admin → Billing → Landing page** and
  stored as one JSON row in `settings` (`landing_pricing`). Prices themselves are never stored
  there; they are always read from Stripe. Everything typed is normalised server-side
  (`backend/landing.py`): markup stripped, lengths clamped, at most 12 bullets, blank lines dropped.
- There is one plan card, because there is one product with two prices; Monthly/Yearly is a toggle
  above it and the saving is worked out from the two Stripe amounts. With no Stripe configured the
  card becomes a plain "Full access — free" card pointing at sign-up, and with sign-ups closed every
  call to action becomes **Sign in**. So the same page ships correctly to a private single-user
  install and to a paid service.
- The copy is deliberately consumer-facing: the page sells privacy and control, and says nothing
  about the project being open source or self-hostable — true, but not a reason anyone pays for a
  money app. That framing stays here in the README, where developers read it.

The sign-in, sign-up, forgot, reset and verify screens share the same art direction: a dark panel
beside the form on a desktop, the familiar centred card below 960px. The form half stays
theme-aware, so the theme toggle still means something.

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

## Running iSpend as a service

iSpend runs happily with no billing at all: leave the Stripe settings empty and every account has
full access, which is what a self-hosted install wants. To sell it instead:

1. In Stripe, create a product with a monthly price (and optionally a yearly one).
2. Add a webhook endpoint pointing at `https://<your host>/api/billing/webhook`, subscribing to
   `checkout.session.completed`, `customer.subscription.created|updated|deleted`,
   `invoice.paid`, `invoice.payment_failed` and `customer.subscription.trial_will_end`.
   Copy its signing secret.
3. Put the keys in `.env` (see `.env.example`) or in **Admin → Billing**, set `APP_BASE_URL`, and
   turn on **Accept new sign-ups**.
4. Optionally configure SMTP so iSpend can send its welcome, trial-ending, payment-failed and
   password-reset messages. Stripe emails receipts itself.
5. Edit the pricing wording in **Admin → Billing → Landing page**; the amounts come from Stripe
   on their own.

How it behaves:

- A new sign-up gets a **local** trial (14 days by default). No Stripe object exists until someone
  subscribes, so sign-up works even if Stripe is down and spam accounts never reach your dashboard.
- Payment uses hosted **Stripe Checkout** and the **Billing Portal** — card details never touch
  this server.
- When a trial ends or a payment fails, the account gets a **grace period** (7 days by default)
  with in-app warnings, then becomes **read-only**: the user can still sign in, read everything and
  export their data, but cannot import or edit until they subscribe. Nothing is ever deleted.
- **Administrators are never billed.** That keeps a self-hosted operator from being locked out of
  their own admin console, but it means an `admin` account is a free licence — never grant one to
  a customer.
- Existing accounts on an install that upgrades into billing are marked complimentary forever, so
  turning billing on never locks out the people already using it.

## Security notes

- Session cookies (HttpOnly, SameSite=Lax, Secure when installed with HTTPS); use the installer's Let's Encrypt option when the app is reachable from the internet.
- Uploaded statements are stored in a private Docker volume and are only downloadable by their owner.
- Locked and deleted accounts return the same message at sign-in, so a password holder cannot tell which happened; a wrong password is always the generic "Invalid username or password".
- Changing a password signs every other session out; password-reset links are single-use, expire in an hour, and are stored only as a hash.
- Sign-up and password-reset endpoints are rate-limited in nginx and again in the application, because nginx can be bypassed by anything that reaches the backend directly.
- Every mutating action is written to `audit_log`.
