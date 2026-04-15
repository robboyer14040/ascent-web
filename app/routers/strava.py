"""
routers/strava.py — Strava OAuth + activity import into Ascent DB.
"""

import os, json, time, asyncio
from pathlib import Path
from typing import Callable, Optional
import httpx
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from app.auth import get_session_user_id

router = APIRouter()
db_getter: Callable = None
templates = None

STRAVA_AUTH_URL  = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"
STRAVA_SCOPE     = "read,activity:read,activity:read_all,activity:write"

# ── token helpers ─────────────────────────────────────────────────────────────

def _get_strava_creds(user_id: Optional[int] = None) -> tuple[str, str]:
    """Get Strava client_id and client_secret.

    Priority:
      1. Per-user stored credentials (legacy; no longer settable via UI)
      2. Server env vars STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET
      3. Any admin user's stored credentials (migration path while env vars not yet set)
    """
    if user_id is not None:
        try:
            user = db_getter().get_user(user_id)
            if user:
                cid = user.get("strava_client_id") or ""
                sec = user.get("strava_client_secret") or ""
                if cid and sec:
                    return cid, sec
        except Exception:
            pass
    # Server-level env vars (preferred)
    cid = os.environ.get("STRAVA_CLIENT_ID", "")
    sec = os.environ.get("STRAVA_CLIENT_SECRET", "")
    if cid and sec:
        return cid, sec
    # Migration fallback: use credentials stored in any admin user's record
    try:
        for user in db_getter().list_users():
            u = db_getter().get_user(user["id"])
            if u and u.get("strava_client_id") and u.get("strava_client_secret"):
                return u["strava_client_id"], u["strava_client_secret"]
    except Exception:
        pass
    return "", ""


def _tokens_path() -> Path:
    """Legacy: strava_tokens.json fallback for single-user mode."""
    db_path = os.environ.get("ASCENT_DB_PATH", ".")
    return Path(db_path).parent / "strava_tokens.json"

def load_tokens(user_id: Optional[int] = None) -> dict:
    """Load tokens from DB for user_id, or fall back to legacy file (single-user mode only)."""
    if user_id is not None:
        # Multi-user mode: only use DB. Never fall back to legacy file for a known user,
        # otherwise a user with no Strava connection inherits another user's tokens.
        try:
            tokens = db_getter().get_user_strava_tokens(user_id)
            return tokens if tokens else {}
        except Exception:
            return {}
    # Legacy fallback: single-user mode (no session / uid=None)
    p = _tokens_path()
    if p.exists():
        try: return json.loads(p.read_text())
        except Exception: pass
    return {}

def save_tokens(tokens: dict, user_id: Optional[int] = None):
    """Save tokens to DB for user_id, and keep file in sync for compatibility."""
    if user_id is not None:
        try:
            db_getter().update_user_strava_tokens(user_id, tokens)
        except Exception:
            pass
    # Also write file for backward compatibility
    try:
        _tokens_path().write_text(json.dumps(tokens, indent=2))
    except Exception:
        pass

def tokens_are_fresh(tokens: dict) -> bool:
    return bool(tokens.get("access_token")) and tokens.get("expires_at", 0) > time.time() + 60

async def refresh_tokens(tokens: dict, user_id: Optional[int] = None) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(STRAVA_TOKEN_URL, data={
            "client_id":     _get_strava_creds(user_id)[0],
            "client_secret": _get_strava_creds(user_id)[1],
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
    save_tokens(tokens, user_id=user_id)
    return tokens

async def get_fresh_token(user_id: Optional[int] = None) -> Optional[str]:
    tokens = load_tokens(user_id)
    if not tokens.get("refresh_token"): return None
    if not tokens_are_fresh(tokens):
        tokens = await refresh_tokens(tokens, user_id=user_id)
    return tokens.get("access_token")

def _callback_uri(request: Request) -> str:
    override = os.environ.get("STRAVA_REDIRECT_URI")
    if override: return override
    return str(request.base_url).rstrip("/") + "/strava/callback"

# ── OAuth routes ──────────────────────────────────────────────────────────────

@router.get("/connect")
async def strava_connect(request: Request):
    uid = get_session_user_id(request)
    client_id, _ = _get_strava_creds(uid)
    if not client_id:
        return templates.TemplateResponse("error.html",
            {"request": request, "message": "Strava is not configured on this server. Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET environment variables."})
    url = (f"{STRAVA_AUTH_URL}?client_id={client_id}&response_type=code"
           f"&redirect_uri={_callback_uri(request)}&approval_prompt=force&scope={STRAVA_SCOPE}")
    return RedirectResponse(url)

@router.get("/callback", response_class=HTMLResponse)
async def strava_callback(request: Request, code: str = Query(None), error: str = Query(None)):
    if error or not code:
        return templates.TemplateResponse("error.html",
            {"request": request, "message": f"Strava auth failed: {error or 'no code'}"})
    async with httpx.AsyncClient() as client:
        uid = get_session_user_id(request)
        cid, csec = _get_strava_creds(uid)
        resp = await client.post(STRAVA_TOKEN_URL, data={
            "client_id":     cid,
            "client_secret": csec,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  _callback_uri(request),
        })
    if resp.status_code != 200:
        return templates.TemplateResponse("error.html",
            {"request": request, "message": f"Token exchange failed: {resp.text}"})
    data = resp.json()
    from app.auth import get_session_user_id as _gsu
    uid = _gsu(request)
    tokens = {"access_token": data["access_token"], "refresh_token": data["refresh_token"],
              "expires_at": data["expires_at"], "athlete": data.get("athlete", {})}
    save_tokens(tokens, user_id=uid)
    # Also link athlete_id to this user (best-effort; ignore UNIQUE conflicts)
    if uid:
        athlete = data.get("athlete", {})
        athlete_id = str(athlete.get("id", "")) if athlete else None
        if athlete_id:
            try:
                db_getter()._con.execute(
                    "UPDATE users SET strava_athlete_id=? WHERE id=?", (athlete_id, uid))
                db_getter()._con.commit()
            except Exception:
                pass
    return templates.TemplateResponse("strava_connected.html",
        {"request": request, "athlete": data.get("athlete", {})})

@router.get("/disconnect")
async def strava_disconnect(request: Request):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    # Clear tokens from DB for this user
    if uid is not None:
        try:
            db_getter()._con.execute(
                "UPDATE users SET strava_tokens_json=NULL, strava_athlete_id=NULL WHERE id=?",
                (uid,))
            db_getter()._con.commit()
        except Exception:
            pass
    # Also clear legacy file if present
    p = _tokens_path()
    if p.exists(): p.unlink()
    return RedirectResponse("/strava/sync")

@router.get("/status")
async def strava_status(request: Request):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    tokens = load_tokens(user_id=uid)
    return {"authorized":   bool(tokens.get("refresh_token")),
            "token_fresh":  tokens_are_fresh(tokens),
            "expires_at":   tokens.get("expires_at"),
            "athlete":      tokens.get("athlete", {})}

# ── Sync page ─────────────────────────────────────────────────────────────────

@router.get("/sync", response_class=HTMLResponse)
async def strava_sync_page(request: Request):
    from app.auth import get_session_user_id
    uid     = get_session_user_id(request)
    if uid is None:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login?next=/strava/sync", status_code=303)
    tokens  = load_tokens(user_id=uid)
    db      = db_getter()
    last_ts = db.get_last_sync_time(user_id=uid)

    # Format the date for display in the template
    last_sync_str = None
    if last_ts:
        from datetime import datetime
        last_sync_str = datetime.fromtimestamp(last_ts).strftime("%b %-d, %Y")

    user = db.get_user(uid)
    try:
        ui_prefs = db.get_ui_prefs(uid)
    except Exception:
        ui_prefs = {}
    return templates.TemplateResponse("strava_sync.html", {
        "request":       request,
        "authorized":    bool(tokens.get("refresh_token")),
        "athlete":       tokens.get("athlete", {}),
        "last_sync":     last_ts,
        "last_sync_str": last_sync_str,
        "db_count":      db.count_activities(user_id=uid),
        "is_admin":      bool(user and user.get("is_admin")),
        "ui_prefs":      ui_prefs,
    })

# ── Gear map helper ───────────────────────────────────────────────────────────

async def fetch_gear_map(token: str) -> dict:
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
    request:     Request,
    after_date:  str = Query(""),      # ISO date "YYYY-MM-DD" (range mode)
    before_date: str = Query(""),      # ISO date "YYYY-MM-DD" (range mode)
    mode:        str = Query("recent"), # "recent" | "range" | "all"
):
    """
    Server-Sent Events endpoint. Imports activity summaries only.
    GPS streams are fetched on demand when the user views each activity.

    Modes:
      recent — after the most recent activity already in the DB (or 90 days if empty)
      range  — between after_date and before_date
      all    — no date filter (fetches everything, use sparingly)
    """
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    token = await get_fresh_token(user_id=uid)
    if not token:
        async def no_auth():
            yield 'data: {"type":"error","msg":"Not authorized — connect Strava first"}\n\n'
        return StreamingResponse(no_auth(), media_type="text/event-stream")

    import time as _time
    from datetime import datetime, timezone

    after_ts  = None
    before_ts = None

    if mode == "recent":
        db      = db_getter()
        last_ts = db.get_last_sync_time(user_id=uid)
        if last_ts:
            after_ts = last_ts - (2 * 24 * 3600)  # 2-day overlap as safety buffer
        else:
            after_ts = int(_time.time()) - (90 * 24 * 3600)  # default 90 days for empty DB

    elif mode == "range":
        if after_date:
            try:
                dt       = datetime.strptime(after_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                after_ts = int(dt.timestamp())
            except ValueError:
                pass
        if before_date:
            try:
                dt        = datetime.strptime(before_date, "%Y-%m-%d").replace(
                                tzinfo=timezone.utc, hour=23, minute=59, second=59)
                before_ts = int(dt.timestamp())
            except ValueError:
                pass

    # mode == "all" → both remain None (no filter)

    from app.strava_importer import StravaImporter
    importer = StravaImporter(db_getter().path)
    gear_map = await fetch_gear_map(token)

    async def event_stream():
        import datetime as dt_mod
        if mode == "all":
            range_str = "all time"
        elif after_ts and before_ts:
            a = dt_mod.datetime.fromtimestamp(after_ts).strftime("%b %d, %Y")
            b = dt_mod.datetime.fromtimestamp(before_ts).strftime("%b %d, %Y")
            range_str = f"{a} → {b}"
        elif after_ts:
            a = dt_mod.datetime.fromtimestamp(after_ts).strftime("%b %d, %Y")
            range_str = f"since {a}"
        elif before_ts:
            b = dt_mod.datetime.fromtimestamp(before_ts).strftime("%b %d, %Y")
            range_str = f"up to {b}"
        else:
            range_str = "all time"

        yield f'data: {json.dumps({"type":"start","msg":f"Fetching summaries ({range_str})… GPS tracks load when you view each activity."})}\n\n'
        try:
            async for event in importer.sync(
                token,
                after_ts=after_ts,
                before_ts=before_ts,
                gear_map=gear_map,
                user_id=uid,
            ):
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0)
        except Exception as e:
            yield f'data: {json.dumps({"type":"error","msg":str(e)})}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ── Preview endpoint ──────────────────────────────────────────────────────────

@router.get("/api/activities")
async def strava_fetch_activities(
    page:     int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=200),
    after:    int = Query(None),
    before:   int = Query(None),
):
    token = await get_fresh_token()
    if not token:
        from fastapi import HTTPException
        raise HTTPException(401, "Not authorized with Strava")
    params = {"page": page, "per_page": per_page}
    if after:  params["after"]  = after
    if before: params["before"] = before
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{STRAVA_API_BASE}/athlete/activities",
                                headers={"Authorization": f"Bearer {token}"},
                                params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()




# ── Strava Webhook (Deauthorization) ─────────────────────────────────────────
# Strava requires apps to implement a webhook endpoint that receives events when
# athletes deauthorize the app. We must delete their tokens immediately.
# Docs: https://developers.strava.com/docs/webhooks/

@router.get("/webhook")
async def strava_webhook_verify(
    hub_mode:      str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """Strava webhook subscription verification (GET handshake)."""
    expected = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN", "ascent-webhook-verify")
    if hub_mode == "subscribe" and hub_verify_token == expected and hub_challenge:
        return {"hub.challenge": hub_challenge}
    from fastapi import HTTPException
    raise HTTPException(403, "Webhook verification failed")


@router.post("/webhook")
async def strava_webhook_event(request: Request):
    """
    Strava webhook event receiver.
    Per Strava API terms: on deauthorization we must immediately delete
    the athlete's tokens and data.
    """
    import logging
    log = logging.getLogger("uvicorn")
    try:
        event = await request.json()
    except Exception:
        return {"status": "ok"}  # always return 200 to Strava

    object_type  = event.get("object_type")
    aspect_type  = event.get("aspect_type")
    owner_id     = event.get("owner_id")   # Strava athlete ID

    log.info(f"[webhook] object_type={object_type} aspect_type={aspect_type} owner_id={owner_id}")

    # Deauthorization event: athlete has revoked app access
    # Per Strava API Agreement: must immediately delete all stored data for that athlete.
    if object_type == "athlete" and aspect_type == "delete" and owner_id:
        try:
            db = db_getter()
            user = db.get_user_by_strava_athlete_id(str(owner_id))
            if user:
                uid = user["id"]
                # Delete all activities imported from Strava for this user
                cur = db._con.execute(
                    "DELETE FROM activities WHERE user_id=? AND strava_activity_id IS NOT NULL",
                    (uid,))
                deleted_count = cur.rowcount
                # Clear Strava credentials
                db._con.execute(
                    "UPDATE users SET strava_tokens_json=NULL, strava_athlete_id=NULL WHERE id=?",
                    (uid,))
                db._con.commit()
                log.info(f"[webhook] Deauth athlete {owner_id}: deleted {deleted_count} activities and cleared tokens for user id={uid}")
            else:
                log.info(f"[webhook] No user found for Strava athlete {owner_id} — nothing to clear")
        except Exception as e:
            log.error(f"[webhook] Error handling deauth for athlete {owner_id}: {e}")

    # Always respond 200 immediately — Strava will retry on failure
    return {"status": "ok"}
