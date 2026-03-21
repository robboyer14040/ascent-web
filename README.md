# Ascent Web

A browser-based version of the Ascent macOS fitness activity tracker.
Reads directly from your existing Ascent `.db` SQLite file — no migration needed.

## Stack
- **Backend**: Python / Flask
- **Map**: Leaflet.js (OpenStreetMap tiles, dark-inverted)
- **Charts**: Chart.js
- **No build step required**

## Setup

```bash
# 1. Install dependencies
pip install flask

# 2. Run the server, pointing at your Ascent database file
python app.py --db /path/to/your/ascent.db

# 3. Open in browser
open http://localhost:5000
```

## Options

```
python app.py --db /path/to/ascent.db   # path to your .db file
             --port 5000                # port (default: 5000)
             --debug                    # enable Flask debug mode
```

You can also set the database path via environment variable:
```bash
export ASCENT_DB=/path/to/ascent.db
python app.py
```

## Features

- **Activity list** — browse all activities, newest first; search by name
- **Overview tab** — key stats (distance, time, pace, HR, power, cadence, climb, etc.)
- **Map tab** — GPS track on an interactive dark map (Leaflet + OpenStreetMap)
- **Charts tab** — elevation, heart rate, speed, power, cadence over time
- **Activity details** — notes, keywords, equipment, effort, weather, location, device

## What it reads from your .db file

The app reads two SQLite tables:

| Table        | Contents                                              |
|-------------|-------------------------------------------------------|
| `activities` | One row per activity — metadata, source stats, attrs  |
| `points`     | Per-point GPS/sensor data (lat, lon, alt, HR, speed…) |

If your database has a slightly different schema (older Ascent versions used
different column names), the app introspects available columns and falls back
gracefully — source stats (distance, time, HR, etc.) are always shown even
when track points aren't available.

## Project structure

```
ascent-web/
├── app.py                  ← Flask backend + all API routes
├── requirements.txt
├── templates/
│   └── index.html          ← Single-page app shell
└── static/
    ├── css/style.css       ← Dark industrial UI theme
    └── js/app.js           ← All frontend logic
```

## Extending

- **Import GPX/TCX/FIT**: add a `/import` route in `app.py` using `gpxpy` or `fitparse`
- **Strava sync**: add OAuth flow endpoints mirroring `StravaAPI.m`
- **Equipment log**: add routes reading the `equipment` table (if present)
- **Laps**: `app.py` already has `/api/activity/<uuid>/laps` — wire it to the UI
