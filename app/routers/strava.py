"""
routers/strava.py — Strava OAuth + full activity import into Ascent DB.
"""

import os, json, time, asyncio
from pathlib import Path
from typing import Callable, Optional
import httpx
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

router = APIRouter()
db_getter: Callable = None
templates = None

STRAVA_AUTH_URL  = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"
STRAVA_SCOPE     = "read,profile:read_all,activity:read,activity:read_all"

# ── token helpers ─────────────────────────────────────────────────────────────
def _tokens_path() -> Path:
    db_path = os.environ.get("ASCENT_DB_PATH", ".")
    return Path(db_path).parent / "strava_tokens.json"

def load_tokens() -> dict:
    p = _tokens_path()
    if p.exists():
        try: return json.loads(p.read_text())
        except Exception: pass
    return {}

def save_tokens(tokens: dict):
    _tokens_path().write_text(json.dumps(tokens, indent=2))

def tokens_are_fresh(tokens: dict) -> bool:
    return bool(tokens.get("access_token")) and tokens.get("expires_at", 0) > time.time() + 60

async def refresh_tokens(tokens: dict) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(STRAVA_TOKEN_URL, data={
            "client_id":     os.environ.get("STRAVA_CLIENT_ID", ""),
            "client_secret": os.environ.get("STRAVA_CLIENT_SECRET", ""),
            "grant_type":    "refresh_token",
            "refresh_token": tokens["refresh_token"],
        })
        resp.raise_for_status()
        data = resp.json()
    tokens.update({
        "access_token":  data["access_token"],
        "refresh_token": data.get("refresh_token", tokens["refresh_token"]),
        "expires_at":    data["expires_at"],
    })
    save_tokens(tokens)
    return tokens

async def get_fresh_token() -> Optional[str]:
    tokens = load_tokens()
    if not tokens.get("refresh_token"): return None
    if not tokens_are_fresh(tokens):    tokens = await refresh_tokens(tokens)
    return tokens.get("access_token")

def _callback_uri(request: Request) -> str:
    override = os.environ.get("STRAVA_REDIRECT_URI")
    if override: return override
    return str(request.base_url).rstrip("/") + "/strava/callback"

# ── OAuth routes ──────────────────────────────────────────────────────────────
@router.get("/connect")
async def strava_connect(request: Request):
    client_id = os.environ.get("STRAVA_CLIENT_ID", "")
    if not client_id:
        return templates.TemplateResponse("error.html",
            {"request": request, "message": "STRAVA_CLIENT_ID is not set in your .env file."})
    url = (f"{STRAVA_AUTH_URL}?client_id={client_id}&response_type=code"
           f"&redirect_uri={_callback_uri(request)}&approval_prompt=auto&scope={STRAVA_SCOPE}")
    return RedirectResponse(url)

@router.get("/callback", response_class=HTMLResponse)
async def strava_callback(request: Request, code: str = Query(None), error: str = Query(None)):
    if error or not code:
        return templates.TemplateResponse("error.html",
            {"request": request, "message": f"Strava auth failed: {error or 'no code'}"})
    async with httpx.AsyncClient() as client:
        resp = await client.post(STRAVA_TOKEN_URL, data={
            "client_id":     os.environ.get("STRAVA_CLIENT_ID", ""),
            "client_secret": os.environ.get("STRAVA_CLIENT_SECRET", ""),
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  _callback_uri(request),
        })
    if resp.status_code != 200:
        return templates.TemplateResponse("error.html",
            {"request": request, "message": f"Token exchange failed: {resp.text}"})
    data = resp.json()
    save_tokens({"access_token": data["access_token"], "refresh_token": data["refresh_token"],
                 "expires_at": data["expires_at"], "athlete": data.get("athlete", {})})
    return templates.TemplateResponse("strava_connected.html",
        {"request": request, "athlete": data.get("athlete", {})})

@router.get("/disconnect")
async def strava_disconnect():
    p = _tokens_path()
    if p.exists(): p.unlink()
    return RedirectResponse("/")

@router.get("/status")
async def strava_status():
    tokens = load_tokens()
    return {"authorized": bool(tokens.get("refresh_token")),
            "token_fresh": tokens_are_fresh(tokens),
            "expires_at":  tokens.get("expires_at"),
            "athlete":     tokens.get("athlete", {})}

# ── Sync page ─────────────────────────────────────────────────────────────────
@router.get("/sync", response_class=HTMLResponse)
async def strava_sync_page(request: Request):
    tokens   = load_tokens()
    db       = db_getter()
    last_ts  = db.get_last_sync_time()
    existing = len(db.get_activity_types())  # cheap proxy for "DB has data"
    return templates.TemplateResponse("strava_sync.html", {
        "request":    request,
        "authorized": bool(tokens.get("refresh_token")),
        "athlete":    tokens.get("athlete", {}),
        "last_sync":  last_ts,
        "db_count":   db.count_activities(),
    })

# ── Gear map helper ───────────────────────────────────────────────────────────
async def fetch_gear_map(token: str) -> dict:
    """Fetch athlete gear (bikes + shoes) and return {gear_id: name}."""
    gear_map = {}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{STRAVA_API_BASE}/athlete",
                                 headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 200:
                athlete = r.json()
                for bike in (athlete.get("bikes") or []):
                    gear_map[bike["id"]] = bike.get("name", bike["id"])
                for shoe in (athlete.get("shoes") or []):
                    gear_map[shoe["id"]] = shoe.get("name", shoe["id"])
    except Exception:
        pass
    return gear_map

# ── SSE sync stream ───────────────────────────────────────────────────────────
@router.get("/run-sync")
async def run_sync(
    request: Request,
    mode:    str = Query("new"),   # "new" = since last activity, "all" = everything
):
    """
    Server-Sent Events endpoint. Streams progress as activities are imported.
    The browser connects and receives real-time updates.
    """
    token = await get_fresh_token()
    if not token:
        async def no_auth():
            yield 'data: {"type":"error","msg":"Not authorized — connect Strava first"}\n\n'
        return StreamingResponse(no_auth(), media_type="text/event-stream")

    db = db_getter()
    after_ts = None
    if mode == "new":
        last_ts = db.get_last_sync_time()
        if last_ts:
            # Subtract 2 days as safety buffer — Strava's `after` is exclusive
            # and timezone differences can cause edge cases
            after_ts = last_ts - (2 * 24 * 3600)

    from app.strava_importer import StravaImporter
    importer = StravaImporter(db.path)

    gear_map = await fetch_gear_map(token)

    async def event_stream():
        import datetime
        since_str = datetime.datetime.fromtimestamp(after_ts).strftime("%b %d, %Y") if after_ts else "the beginning"
        yield f'data: {json.dumps({"type":"start","msg":f"Starting sync — fetching activities since {since_str}…"})}\n\n'
        try:
            async for event in importer.sync(token, after_ts=after_ts, gear_map=gear_map):
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0)  # yield to event loop
        except Exception as e:
            yield f'data: {json.dumps({"type":"error","msg":str(e)})}\n\n'

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Preview endpoints (read-only from Strava, not written to DB) ──────────────
@router.get("/api/activities")
async def strava_fetch_activities(
    page:     int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=200),
    after:    int = Query(None),
):
    token = await get_fresh_token()
    if not token:
        from fastapi import HTTPException
        raise HTTPException(401, "Not authorized with Strava")
    params = {"page": page, "per_page": per_page}
    if after: params["after"] = after
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{STRAVA_API_BASE}/athlete/activities",
                                headers={"Authorization": f"Bearer {token}"},
                                params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()
