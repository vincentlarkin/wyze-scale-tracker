# Wyze Scale Tracker (Local Web App)

Pulls every weigh-in from your Wyze scale — for **every person on the scale** — into a local SQLite database and charts trends in your browser.

## What it tracks

- Weight (lb and kg), BMI
- Body fat, subcutaneous fat, visceral fat (`body_vfr`)
- Muscle, skeletal muscle, protein, body water, bone mineral
- BMR, metabolic age, body score (where the scale reports it)

Works with the original Wyze Scale, Scale S, Scale X, Scale Ultra and Ultra BodyScan.

## Quick start

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

A desktop window opens. On first boot a second **Connect Wyze** window opens on the Wyze developer console:

1. Sign in with your Wyze email and password (type in the email code yourself if Wyze sends one).
2. Click **Create API Key**. If you already have a key and the full API Key isn't shown, delete it and create a new one.
3. As soon as the key appears, the window says **"API key captured — restarting in 3…"**, closes, logs in, and the dashboard pulls your history.

The window only reads the email/password you submit on Wyze's own sign-in page and the key the console shows you; they're saved to `.env` on this computer and nothing goes anywhere but Wyze. If you sign in with Google/Apple (no Wyze password typed), the app keeps the key and asks for your Wyze password once. If Wyze asks for 2FA, the dashboard asks for the code.

The app logs in **once**, caches a refreshable token in `data/wyze_session.json`, and refreshes it automatically; saved credentials let it silently re-login if the refresh token ever dies. To capture a new key later: click the Wyze chip → **Sign in with Wyze**, or run `python app.py desktop --reconnect`.

Prefer your normal browser? `python app.py serve` runs the same dashboard at <http://127.0.0.1:8000> with a manual Connect form (paste Key ID + API Key from the [developer console](https://developer-api-console.wyze.com/#/apikey/view)).

After that, opening the page auto-syncs if the last sync was more than 15 minutes ago.

## CLI (for scripts, cron, and agents)

```powershell
.venv\Scripts\python app.py              # desktop app (default)
.venv\Scripts\python app.py desktop --reconnect  # re-capture an API key
.venv\Scripts\python app.py serve      # web server + your browser (--no-browser to skip)
.venv\Scripts\python app.py login        # connect from the terminal (handles 2FA)
.venv\Scripts\python app.py sync         # pull new records for everyone
.venv\Scripts\python app.py sync --full  # re-fetch complete history
.venv\Scripts\python app.py status       # connection + per-person record counts
.venv\Scripts\python app.py logout       # forget cached tokens
```

## How sync works

- Lists your scales, then each scale's family members, and pulls records per person.
- Incremental: each person is fetched from their newest stored record (2-day overlap). First sync pulls full history.
- Records are de-duplicated by Wyze record ID, so re-syncing is safe.
- The profile picker defaults to the person with the most records; pick **All Profiles** to see everyone.

## Other features

- **Import Excel**: load a Wyze app `.xlsx` export (each worksheet becomes a profile).
- **Export CSV**: day / week / year / all / custom range, per profile.
- Period stats, matched-weight insights (muscle/body-fat change at the same weight ±1 lb), configurable table columns.

## Notes

- Requires Python 3.12+ (wyze-sdk 2.3.x).
- Uses the unofficial [`wyze-sdk`](https://github.com/shauntarves/wyze-sdk); if Wyze changes private endpoints, sync can break until the library updates.
- Wyze rate-limits the login endpoint (HTTP 429 after repeated attempts). Token caching means normal use logs in only once; if you do hit 429, wait 10–15 minutes.
- Accounts created with Google/Apple sign-in need a Wyze password set in the app (Account → Security).
- Everything stays local: `data/` (database + token) and `.env` are git-ignored.
