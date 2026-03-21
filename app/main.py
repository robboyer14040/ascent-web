"""
main.py — FastAPI entry point for Ascent Web.

Usage:
    uvicorn app.main:app --reload --port 8000

.env file:
    ASCENT_DB_PATH=/path/to/your/ascent.db
    STRAVA_CLIENT_ID=174788
    STRAVA_CLIENT_SECRET=your_secret
    SECRET_KEY=random_hex_string
"""

import os
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from dotenv import load_dotenv

from app.db import AscentDB
from app.routers import activities, api, strava, photos, settings, weather

load_dotenv()

# ── DB singleton ──────────────────────────────────────────────────────────────
_db = None

# Mutable container so settings router can hot-swap the DB instance
_db_instance: list = []  # holds [AscentDB]

def set_db(new_db):
    global _db
    _db = new_db
    if _db_instance:
        _db_instance[0] = new_db
    else:
        _db_instance.append(new_db)

def get_db() -> AscentDB:
    global _db
    if _db is None:
        db_path = os.environ.get("ASCENT_DB_PATH", "")
        if not db_path:
            raise RuntimeError(
                "ASCENT_DB_PATH is not set.\n"
                "Add to .env:  ASCENT_DB_PATH=/path/to/ascent.db"
            )
        _db = AscentDB(db_path)
    return _db


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db = get_db()
        count = db.count_activities()
        _db_instance.append(db)
        print(f"[Ascent] ✓ Connected — {count} activities")
    except Exception as e:
        print(f"[Ascent] ⚠ DB not connected: {e}")
    yield
    if _db:
        _db.close()


# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Ascent Web", version="1.0.0", lifespan=lifespan)

BASE_DIR     = os.path.dirname(os.path.dirname(__file__))
STATIC_DIR   = os.path.join(BASE_DIR, "app", "static")
TEMPLATE_DIR = os.path.join(BASE_DIR, "app", "templates")

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATE_DIR)

# ── Jinja2 custom filters ─────────────────────────────────────────────────────
def fmt_date(ts):
    if not ts: return "—"
    try:
        d = datetime.fromtimestamp(int(ts))
        return d.strftime("%b %-d, %Y")
    except Exception:
        return str(ts)

def type_badge(t):
    if not t: return '<span style="color:#636366">—</span>'
    l = t.lower()
    if "run" in l:
        cls = "t-run"; color = "#30d158"
    elif "ride" in l or "cycl" in l:
        cls = "t-ride"; color = "#f97316"
    elif "swim" in l:
        cls = "t-swim"; color = "#0a84ff"
    else:
        cls = "t-other"; color = "#8e8e93"
    return (f'<span style="display:inline-block;padding:.15rem .5rem;border-radius:999px;'
            f'font-size:.68rem;font-weight:600;text-transform:uppercase;'
            f'color:{color};background:{color}22">{t}</span>')

templates.env.filters["fmt_date"]   = fmt_date
templates.env.filters["type_badge"] = type_badge

# ── wire routers ──────────────────────────────────────────────────────────────
activities.db_getter = get_db
activities.templates = templates
api.db_getter        = get_db
strava.db_getter     = get_db
strava.templates     = templates
photos.db_getter     = get_db
settings.db_getter   = get_db
settings.db_setter   = set_db
settings.templates   = templates
weather.db_getter    = get_db

app.include_router(activities.router)
app.include_router(api.router,    prefix="/api")
app.include_router(strava.router, prefix="/strava")
app.include_router(photos.router)
app.include_router(settings.router)
app.include_router(weather.router, prefix="/api")


# ── root: dashboard ───────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    try:
        db             = get_db()
        stats          = db.get_dashboard_stats()
        years          = db.get_years()
        activity_types = db.get_activity_types()
        monthly        = db.get_monthly_totals()
        db_ok          = True
        db_error       = None
    except Exception as e:
        stats = {}; years = []; activity_types = []; monthly = []
        db_ok = False; db_error = str(e)

    return templates.TemplateResponse("dashboard.html", {
        "request":          request,
        "stats":            stats,
        "years":            years,
        "activity_types":   activity_types,
        "monthly":          monthly,
        "db_ok":            db_ok,
        "db_error":         db_error,
        "strava_client_id": os.environ.get("STRAVA_CLIENT_ID", ""),
    })
