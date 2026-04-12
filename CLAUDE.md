# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Ascent Web is a FastAPI + Jinja2 web app that provides a multi-user browser interface to user fitness activities with maps, charts, Strava sync, and AI coaching.

## Development Commands

```bash
# Run dev server
uvicorn app.main:app --reload --port 8000

# With explicit DB path
ASCENT_DB_PATH=/path/to/Ascent.ascentdb uvicorn app.main:app --reload --port 8000

# Database migrations (for existing Ascent DBs)
python3 scripts/migrate_add_users.py
python3 scripts/migrate_step2.py

# Deploy to Fly.io
fly deploy
fly logs
```

No test suite exists. Testing is manual via browser.

## Architecture

**Backend:** FastAPI with Jinja2 server-rendered templates. No frontend build step — `main.html` is a self-contained ~6000-line SPA with embedded CSS and JavaScript.

**Key files:**
- `app/main.py` — FastAPI entry, lifespan setup, router wiring
- `app/db.py` — All SQLite queries; the core data layer (~51KB)
- `app/auth.py` — bcrypt password hashing + itsdangerous session tokens
- `app/routers/` — Route handlers split by domain (api, coach, strava, photos, settings, weather, auth, activities)
- `app/templates/main.html` — Primary SPA interface

**Router dependency injection pattern:** Routers receive `db_getter` and `templates` at startup in `main.py` rather than using FastAPI's `Depends()`. Each router module has a `create_router(db_getter, templates)` factory function.

**Auth pattern:** No middleware. Each route manually calls `get_session_user_id(request)` from `app/auth.py` and redirects to login if unauthenticated.

**Multi-tenancy:** All DB queries accept a `user_id` parameter to scope data per user. Users can have separate Ascent `.db` files stored in their settings.

**Lazy table creation:** Coach and user-related tables use `CREATE TABLE IF NOT EXISTS` on first access rather than upfront migrations.

## Database Schema Notes

The Ascent SQLite schema has non-obvious unit conventions:

- `points.latitude_e7` / `longitude_e7` — stored as plain degrees (naming is misleading; not multiplied by 1e7)
- `points.orig_altitude_cm` — actually feet, not centimeters
- `points.temperature_c10` — tenths of °F, not °C
- `activities.attributes_json` — flat NSArray serialized as `["key", "value", "key", "value", ...]`

Tables added by migrations: `users`, `coach_goals`, `coach_messages`, `strava_tokens`, `user_settings`.

## Environment Variables

```
ASCENT_DB_PATH       # Path to Ascent SQLite DB (required)
SECRET_KEY           # Session signing key (required for production)
STRAVA_CLIENT_ID     # Optional: Strava OAuth
STRAVA_CLIENT_SECRET # Optional: Strava OAuth
STRAVA_REDIRECT_URI  # Optional: Strava callback URL
ANTHROPIC_API_KEY    # Optional: AI Coach feature
ASCENT_HTTPS         # Optional: force secure cookies (set true on Fly.io)
ASCENT_TEMPLATE_DIR  # Optional: override template path (for bundled apps)
ASCENT_STATIC_DIR    # Optional: override static path (for bundled apps)
SMTP_HOST            # Optional: SMTP server for invite emails (e.g. smtp.gmail.com)
SMTP_PORT            # Optional: SMTP port — 587 (STARTTLS, default) or 465 (SSL)
SMTP_USER            # Optional: SMTP login username
SMTP_PASSWORD        # Optional: SMTP login password / app password
SMTP_FROM            # Optional: From address (defaults to SMTP_USER)
```

## Versioning

After every fix or feature, bump the patch version in `app/main.py`:

```python
templates.env.globals["app_version"] = "v0.1.X"
```

Increment the third number (e.g. `v0.1.96` → `v0.1.97`). The third number can be up to 3 digits (e.g. `v0.5.0` → `v0.5.1` → … → `v0.5.100`). This is the single source of truth for the version displayed in the UI.

## Deployment

Deployed on Fly.io (`fly.toml`): region `sjc`, 512MB RAM, persistent volume `ascent_data` mounted at `/data`. Health check via `GET /favicon.ico`.
