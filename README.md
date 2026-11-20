# Wyze Scale Tracker

A small desktop app that pulls every weigh-in from your Wyze scale, for **every person on the scale**, into a local SQLite database and charts the trends. Everything stays on your computer.

Works with the original Wyze Scale, Scale S, Scale X, Scale Ultra and Ultra BodyScan.

## Quick start

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

The dashboard opens in its own window. On first boot, a second **Connect Wyze** window opens on the Wyze developer console:

1. Sign in with your Wyze email and password. If Wyze emails you a code, type it in yourself.
2. Click **Create API Key**. If you already have a key, Wyze only shows its full value once, so delete it and create a new one.
3. As soon as the key appears, the window says **"API key captured — restarting in 3…"**, closes, logs in, and the dashboard downloads your history.

The sign-in window shows an address bar with a lock and an independent TLS certificate check, so you can see you're on the real `wyze.com`. It reads only the email and password you submit on Wyze's own sign-in page and the key the console shows you. Nothing is sent anywhere except Wyze.

- **Google or Apple sign-in?** The app keeps the key and asks once for a Wyze password. You set that in the Wyze app under Account → Security.
- **2FA?** The dashboard asks for the code (SMS, email or authenticator app).
- **Prefer your normal browser?** `python app.py serve` runs the same dashboard at <http://127.0.0.1:8000> with a manual Connect form.

## What you get

- Weight with a 7-day average trend line. Breaks in the line mean there were no readings for over two weeks.
- Body fat, muscle and BMI cards, each with the change over the last 30 days and a 90-day sparkline.
- Every metric the scale reports: skeletal muscle, subcutaneous and visceral fat, body water, protein, bone mass, BMR, metabolic age and body score.
- A period summary (average, range and change) and muscle and body fat compared at the same body weight.
- A per-person toggle, CSV export, and import of a Wyze app `.xlsx` export.
- Light and dark themes that follow Windows. It works offline, because all assets ship with the app.

## Staying connected

- **One login.** The app logs in once and keeps a refreshable token that renews automatically.
- **Silent re-login.** Saved credentials let it log in again quietly if the token ever stops working. Wyze rate-limits logins (HTTP 429), so this matters.
- **Where secrets live.** Your password, API key and tokens go in the system credential store: Windows Credential Manager, macOS Keychain or Secret Service. If none is available, they go in a local file under `data/`. Only non-secret settings (email, Key ID, key expiry) are in `.env`. Older installs are migrated automatically.
- **Key expiry.** Wyze API keys expire after a year. The connection chip turns amber 30 days before. To capture a new key, click the chip → **Sign in with Wyze**, or run `python app.py desktop --reconnect`.
- **Auto-sync.** Opening the app syncs if the last sync was more than 15 minutes ago.

## Command line (scripts, cron, agents)

```powershell
.venv\Scripts\python app.py                      # desktop app (default)
.venv\Scripts\python app.py desktop --reconnect  # capture a new API key
.venv\Scripts\python app.py serve                # web server + your browser (--no-browser to skip)
.venv\Scripts\python app.py login                # connect from the terminal (handles 2FA)
.venv\Scripts\python app.py sync [--full] [--json]
.venv\Scripts\python app.py status [--json]      # connection, key expiry, per-person counts
.venv\Scripts\python app.py logout               # forget cached tokens
```

## How sync works

1. List your scales, then each scale's family members.
2. Pull records for each person, starting from their newest stored record (with a 2-day overlap). The first sync, or `--full`, downloads the complete history.
3. Records are de-duplicated by Wyze record ID, so re-syncing is always safe.

## Project layout

```
app.py               entry point (python app.py ...)
tracker/
  config.py          paths + constants (override with WST_DATA_DIR / WST_ENV_FILE)
  secret_store.py    OS credential store with file fallback
  auth.py            login, 2FA, token cache/refresh
  wyze_api.py        the only module that touches wyze-sdk internals
  sync.py            per-person incremental sync
  parsing.py         raw record / spreadsheet cell -> DB row
  db.py              SQLite schema, migrations, queries
  importer.py        Wyze app .xlsx import
  web.py             Flask app factory + JSON/CSV routes
  cli.py             command line
  desktop.py         pywebview shell + API-key capture window
  capture.js         script injected into the sign-in window
templates/, static/  dashboard UI (Chart.js vendored in static/vendor)
tests/               pytest suite
```

## Development

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check . ; .venv\Scripts\python -m ruff format --check .
```

Tests never touch your real credential store, `.env` or database. CI runs lint and tests on Windows and Linux with Python 3.12 and 3.13. Set `WST_CAPTURE_DEBUG=1` to log the *shape*, never the values, of the responses the sign-in window sees.

## Notes

- Requires Python 3.12+.
- Uses the unofficial [`wyze-sdk`](https://github.com/shauntarves/wyze-sdk). If Wyze changes its private endpoints, sync can break until the library catches up, and `tracker/wyze_api.py` is where to fix it.
- `data/` (database, token metadata, sign-in window profile) and `.env` are git-ignored.
