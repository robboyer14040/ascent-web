"""routers/settings.py — Database management and API key settings."""

import os
from app.auth import get_session_user_id
import getpass
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter()
db_getter: Callable = None
db_setter: Callable = None
templates = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env_file_path() -> Path:
    """Return the .env file path, checking both the bundled-app config and
    the normal project-root .env."""
    # When running as a packaged .app, persist to ~/.ascent_config
    bundled_config = Path.home() / ".ascent_config"
    if bundled_config.exists():
        return bundled_config
    # Normal dev layout: .env at project root (two levels up from this file)
    project_env = Path(__file__).parent.parent.parent / ".env"
    if project_env.exists():
        return project_env
    # Neither exists yet — prefer project .env for dev, ~/.ascent_config for bundle
    import sys
    if getattr(sys, "_MEIPASS", None):
        return bundled_config
    return project_env


def _read_env_key(key: str) -> Optional[str]:
    """Read a single key from the persistent env/config file."""
    env_file = _env_file_path()
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line[len(key)+1:].strip()
    return None


def _write_env_key(key: str, value: str):
    """Upsert a key=value line in the persistent env/config file."""
    env_file = _env_file_path()
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    new_lines = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_file.write_text("\n".join(new_lines) + "\n")


def _mask_key(key: str) -> str:
    """Return a masked version for display: show first 10 and last 4 chars."""
    if not key or len(key) < 16:
        return "••••••••"
    return key[:10] + "••••••••" + key[-4:]


# ── Pydantic models ───────────────────────────────────────────────────────────

class DbPathRequest(BaseModel):
    path: str

class ApiKeyRequest(BaseModel):
    key: str


# ── Page ─────────────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login?next=/settings", status_code=303)
    try:
        db = db_getter()
        db_path       = db.path
        activity_count = db.count_activities()
    except Exception:
        db_path        = "Not connected"
        activity_count = 0

    user = db_getter().get_user(uid) if uid else {}
    anthropic_key = (user or {}).get("anthropic_api_key") or _read_env_key("ANTHROPIC_API_KEY") or ""

    return templates.TemplateResponse("settings.html", {
        "request":         request,
        "current_user":    user,
        "db_path":         db_path,
        "activity_count":  activity_count,
        "username":        getpass.getuser(),
        "anthropic_key_masked":  _mask_key(anthropic_key) if anthropic_key else "",
    })


# ── Database endpoints ────────────────────────────────────────────────────────

@router.post("/api/db/create")
async def create_database(req: DbPathRequest):
    path = Path(req.path.strip()).expanduser().resolve()
    if path.suffix != ".ascentdb":
        raise HTTPException(400, "File must have .ascentdb extension")
    if not path.parent.exists():
        raise HTTPException(400, f"Directory does not exist: {path.parent}")
    if path.exists():
        raise HTTPException(400, f"File already exists: {path}. Choose a different name.")
    try:
        from app.create_db import create_db
        create_db(str(path))
    except Exception as e:
        raise HTTPException(500, f"Failed to create database: {e}")
    _switch_to(str(path))
    return {"status": "ok", "path": str(path)}


@router.post("/api/db/switch")
async def switch_database(req: DbPathRequest):
    path = Path(req.path.strip()).expanduser().resolve()
    if not path.exists():
        raise HTTPException(404, f"File not found: {path}")
    if path.suffix != ".ascentdb":
        raise HTTPException(400, "File must have .ascentdb extension")
    try:
        _switch_to(str(path))
    except Exception as e:
        raise HTTPException(500, f"Failed to switch database: {e}")
    db = db_getter()
    return {"status": "ok", "path": str(path), "activity_count": db.count_activities()}


def _switch_to(path: str):
    from app.db import AscentDB
    os.environ["ASCENT_DB_PATH"] = path
    _write_env_key("ASCENT_DB_PATH", path)
    if db_setter:
        new_db = AscentDB(path)
        db_setter(new_db)


# ── API key endpoints ─────────────────────────────────────────────────────────

@router.get("/api/settings/keys")
async def get_keys(request: Request):
    """Return masked key values so the UI can show current state."""
    from app.auth import get_session_user_id
    uid  = get_session_user_id(request)
    user = db_getter().get_user(uid) if uid else {}
    anthropic_key = (user or {}).get("anthropic_api_key") or _read_env_key("ANTHROPIC_API_KEY") or ""
    return {
        "anthropic": {
            "set":    bool(anthropic_key),
            "masked": _mask_key(anthropic_key) if anthropic_key else "",
        },
    }


@router.post("/api/settings/anthropic-key")
async def save_anthropic_key(req: ApiKeyRequest, request: Request):
    from app.auth import get_session_user_id
    key = req.key.strip()
    if not key:
        raise HTTPException(400, "Key cannot be empty")
    if not key.startswith("sk-ant-"):
        raise HTTPException(400, "Anthropic API keys should start with sk-ant-")
    uid = get_session_user_id(request)
    if uid:
        db_getter().update_user_settings(uid, anthropic_api_key=key)
    else:
        _write_env_key("ANTHROPIC_API_KEY", key)
        os.environ["ANTHROPIC_API_KEY"] = key
    return {"status": "ok", "masked": _mask_key(key)}



@router.get("/api/settings/training-zones")
async def get_training_zones(request: Request):
    """Return the user profile (max HR, FTP, age, weight) from the database."""
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    try:
        profile = db_getter().get_user_profile(uid)
        return {"status": "ok", **profile}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/settings/training-zones")
async def save_training_zones(request: Request, req: dict):
    """Save user profile fields to the database."""
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")

    def to_int(v):
        try: return int(v) if v not in (None, "", "null") else None
        except: return None
    def to_float(v):
        try: return float(v) if v not in (None, "", "null") else None
        except: return None

    max_hr    = to_int(req.get("max_hr"))
    ftp_watts = to_int(req.get("ftp_watts"))
    age       = to_int(req.get("age"))
    weight_lb = to_float(req.get("weight_lb"))

    def to_bool_int(v):
        if v is None: return None
        return 1 if v in (True, 1, "true", "1") else 0

    use_metric             = to_bool_int(req.get("use_metric"))
    autoplay_videos        = to_bool_int(req.get("autoplay_videos"))
    compare_lookback_years = to_int(req.get("compare_lookback_years"))

    try:
        db_getter().set_user_profile(
            user_id=uid,
            max_hr=max_hr, ftp_watts=ftp_watts, age=age, weight_lb=weight_lb,
            use_metric=use_metric, autoplay_videos=autoplay_videos,
            compare_lookback_years=compare_lookback_years
        )
        return {"status": "ok", "max_hr": max_hr, "ftp_watts": ftp_watts,
                "age": age, "weight_lb": weight_lb,
                "use_metric": bool(use_metric) if use_metric is not None else None,
                "autoplay_videos": bool(autoplay_videos) if autoplay_videos is not None else None,
                "compare_lookback_years": compare_lookback_years}
    except Exception as e:
        raise HTTPException(500, str(e))




# ── UI preferences ───────────────────────────────────────────────────────────

@router.get("/api/settings/ui-prefs")
async def get_ui_prefs(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    return db_getter().get_ui_prefs(uid)


@router.post("/api/settings/ui-prefs")
async def save_ui_prefs(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    prefs = body.get("prefs", {})
    if not isinstance(prefs, dict):
        raise HTTPException(400, "prefs must be an object")
    db_getter().set_ui_prefs(uid, prefs)
    return {"status": "ok"}


# ── Sharing settings ──────────────────────────────────────────────────────────

@router.get("/api/settings/sharing")
async def get_sharing(request: Request):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    user = db_getter().get_user(uid)
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "share_activities": bool(user.get("share_activities")),
        "share_segments":   bool(user.get("share_segments")),
    }


@router.post("/api/settings/sharing")
async def save_sharing(request: Request):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    db_getter().update_user_settings(
        uid,
        share_activities=int(bool(body.get("share_activities"))),
        share_segments=int(bool(body.get("share_segments"))),
    )
    return {"status": "ok"}


