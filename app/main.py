"""
main.py — FastAPI entry point for Ascent Web.

Usage:
    uvicorn app.main:app --reload --port 8000

.env file:
    ASCENT_DB_PATH=/path/to/your/ascent.db
    STRAVA_CLIENT_ID=174788
    STRAVA_CLIENT_SECRET=your_secret
    SECRET_KEY=random_hex_string
    ANTHROPIC_API_KEY=sk-ant-...

When running as a bundled .app (via PyInstaller), the launcher sets:
    ASCENT_TEMPLATE_DIR  — path to bundled templates/
    ASCENT_STATIC_DIR    — path to bundled static/
"""

import os
import sys
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from dotenv import load_dotenv

from app.db import AscentDB
from app.routers import activities, api, strava, photos, settings, weather
from app.routers import coach
from app.routers import auth as auth_router
from app.routers import fitgpx
from app.routers import tours
from app.routers import route_builder
from app.auth import get_session_user_id

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

# Auth is handled per-route via get_session_user_id() checks.
# No middleware needed — BaseHTTPMiddleware strips Set-Cookie headers on redirects.

# Support both normal dev layout and PyInstaller bundle layout.
# The launcher sets ASCENT_TEMPLATE_DIR / ASCENT_STATIC_DIR when running
# as a .app; otherwise fall back to the standard relative paths.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEMPLATE_DIR = os.environ.get(
    "ASCENT_TEMPLATE_DIR",
    os.path.join(_HERE, "app", "templates")
)
STATIC_DIR = os.environ.get(
    "ASCENT_STATIC_DIR",
    os.path.join(_HERE, "app", "static")
)

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
templates.env.globals["app_version"] = "v0.6.18"

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
coach.db_getter      = get_db

# Auth router MUST be first so /login, /logout, /register take priority
app.include_router(auth_router.router)
app.include_router(activities.router)
app.include_router(api.router,     prefix="/api")
app.include_router(strava.router,  prefix="/strava")
app.include_router(photos.router)
app.include_router(settings.router)
app.include_router(weather.router, prefix="/api")
app.include_router(coach.router,   prefix="/api")

auth_router.db_getter = get_db
auth_router.templates = templates
# (auth router already included above)

fitgpx.db_getter = get_db
app.include_router(fitgpx.router)

tours.db_getter  = get_db
tours.templates  = templates
app.include_router(tours.router)

route_builder.db_getter = get_db
route_builder.templates = templates
app.include_router(route_builder.router)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


# ── root: dashboard ───────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Auth first — uid must be defined before any DB calls
    uid = get_session_user_id(request)
    if uid is None:
        return RedirectResponse("/login?next=/", status_code=303)

    db = get_db()
    user = db.get_user(uid)
    try:
        db.touch_last_active(uid)
    except Exception:
        pass

    try:
        stats          = db.get_dashboard_stats(user_id=uid)
        years          = db.get_years(user_id=uid)
        activity_types = db.get_activity_types(user_id=uid)
        monthly        = db.get_monthly_totals(user_id=uid)
        ui_prefs       = db.get_ui_prefs(uid)
        profile        = db.get_user_profile(uid)
        ui_prefs["use_metric"] = profile.get("use_metric", False)
        db_ok          = True
        db_error       = None
    except Exception as e:
        stats = {}; years = []; activity_types = []; monthly = []; ui_prefs = {"use_metric": False}
        db_ok = False; db_error = str(e)

    return templates.TemplateResponse("dashboard.html", {
        "request":          request,
        "current_user":     user,
        "stats":            stats,
        "years":            years,
        "activity_types":   activity_types,
        "monthly":          monthly,
        "ui_prefs":         ui_prefs,
        "db_ok":            db_ok,
        "db_error":         db_error,
        "strava_client_id": os.environ.get("STRAVA_CLIENT_ID", ""),
    })


