# Wyze Scale Tracker (Local Web App)

This app pulls your Wyze Scale records, stores them in a local SQLite database, and plots trends in a browser.

## What it tracks

- Weight (lb and kg)
- BMI
- Body fat
- Muscle
- Body water
- Protein
- Bone mineral
- Visceral fat (`body_vfr`)
- BMR
- Metabolic age

## Requirements

- Python 3.10+
- Your Wyze account credentials
- A Wyze `KEY_ID` and `API_KEY` from the Wyze API portal

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your values.
4. Start the app:

```powershell
python app.py
```

5. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Usage

1. Click **Sync Now**.
2. The app pulls records from Wyze and writes to `data/measurements.db`.
3. If Wyze sync shows 0 records, click **Import Excel** and upload your Wyze export `.xlsx`.
4. Use **Profile** to filter by person (Wyze profile ID for cloud records, sheet names for imported data).
5. Use **Export CSV** and choose `day`, `week`, `year`, `all`, or `custom` date range.
6. Use **Show Few** / **Show All Values** and column chips to customize the Recent Measurements table.
7. Review the **Period Stats** cards for average, highest, lowest, and change in the selected period.
8. Use the chart controls to switch metrics and date range.

## Notes

- Data is stored locally after sync.
- Sync uses the reverse-engineered Wyze client (`wyze-sdk`) to fetch scale records.
- Sync now includes a fallback mode that queries known scale models directly when `scales.list()` returns 0 devices.
- `WYZE_SKIP_SCALE_LIST=true` (default) skips the fragile scale-list step and uses direct model queries first.
- Imported exports are separated by worksheet name as profile identity.
- If Wyze changes private endpoints, sync behavior can break until the client library is updated.
