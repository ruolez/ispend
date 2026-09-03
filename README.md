# iSpend

Self-hosted personal finance analyzer. Upload bank and credit-card statements (CSV, Excel, PDF, even scanned PDFs), let iSpend extract and categorize every charge, and see where the money goes: spending by category, monthly trends, top merchants, recurring subscriptions, anomalies and optional AI-written insights.

- **Import anything**: CSV / XLSX / XLS exports and PDF statements. Text PDFs are parsed directly; scanned PDFs go through OCR (Tesseract via OCRmyPDF) automatically.
- **Bank aware**: built-in profiles for Chase, American Express, Capital One, Bank of America, Citi, Discover, Wells Fargo, RBC, TD, BMO and Scotiabank, plus a generic column detector with a mapping editor for anything else.
- **Learns as you go**: rules (contains / starts with / equals / regex, amount ranges, per account) run first, then remembered merchant choices, then optional AI suggestions. Everything you confirm is remembered for the next import.
- **Review queue**: unknown charges grouped by merchant so one decision categorizes dozens of rows, with "always do this" creating a rule.
- **Duplicate safe**: overlapping statements and re-uploads are detected before anything is written.
- **Multi-user**: admin-managed accounts, every user sees only their own data.
- **Light and dark themes**, keyboard driven, no build step.

## Stack

nginx (port 5559) → Flask + gunicorn (Python 3.12) → PostgreSQL 16, all in Docker Compose. Frontend is plain HTML/CSS/JS served by nginx with Chart.js vendored locally.

## Quick start (Ubuntu 24 LTS)

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/ruolez/ispend/main/install.sh)"
```

The installer offers Install / Update / Remove, installs Docker if needed, generates secrets into `/opt/ispend/.env`, tunes container memory limits to the host and prints the admin password when done. Update keeps the database and uploaded statements (a backup is written to `/opt/ispend-backups` first).

## Local development

```bash
cp .env.example .env            # edit the secrets
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
open http://localhost:5559      # admin / ADMIN_INITIAL_PASSWORD
```

The dev overlay bind-mounts `backend/` with gunicorn `--reload`; the frontend is always served straight from `frontend/` with no-cache headers, so edits show on refresh.

Run the backend test suite (no database needed; pure modules and stubbed APIs):

```bash
cd backend && python3 -m unittest discover -s tests -v
# or inside the container
docker compose exec backend python -m unittest discover -s tests
```

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
                    review, rules, merchants, reports, ai; importer/ and bank_profiles/ packages;
                    migrations/*.sql applied at startup; tests/
frontend/           static pages + css/ js/ vendor/ fonts/
nginx/nginx.conf    static files + /api proxy
docker-compose.yml  production; docker-compose.dev.yml overlay for live reload
install.sh          Ubuntu installer / updater
```

## Security notes

- Session cookies (HttpOnly, SameSite=Lax); run behind HTTPS if exposed beyond a trusted LAN.
- Uploaded statements are stored in a private Docker volume and are only downloadable by their owner.
- Every mutating action is written to `audit_log`.
