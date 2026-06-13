"""routers/activities.py"""

import sqlite3
import secrets as _secrets

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from typing import Callable, Optional
from app.auth import get_session_user_id

router = APIRouter()
db_getter: Callable = None
templates = None


@router.get("/activities", response_class=HTMLResponse)
async def activities_spa(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        return RedirectResponse(f"/login?next=/activities", status_code=303)
    db = db_getter()
    try:
        db.touch_last_active(uid)
    except Exception:
        pass
    try:
        ui_prefs = db.get_ui_prefs(uid)
    except Exception:
        ui_prefs = {}
    resp = templates.TemplateResponse("main.html", {"request": request, "ui_prefs": ui_prefs})
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


@router.get("/activities/list")
async def activities_json(
    request:         Request,
    limit:           int           = Query(1000),
    offset:          int           = Query(0),
    search:          str           = Query(""),
    activity_type:   str           = Query(""),
    sort_by:         str           = Query("start_time"),
    sort_dir:        str           = Query("desc"),
    year:            Optional[int] = Query(None),
    include_friends: bool          = Query(False),
    view_user_ids:   str           = Query(""),
):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    # Parse comma-separated user IDs for multi-user view
    user_ids_filter = [int(x) for x in view_user_ids.split(',') if x.strip().isdigit()] if view_user_ids else []
    db    = db_getter()
    acts  = db.get_activities(
        limit=limit, offset=offset, search=search,
        activity_type=activity_type, sort_by=sort_by,
        sort_dir=sort_dir, year=year,
        user_id=uid if not user_ids_filter else None,
        include_shared=include_friends,
        user_ids=user_ids_filter if user_ids_filter else None,
    )
    total = db.count_activities(
        search=search, activity_type=activity_type, year=year,
        user_id=uid if not user_ids_filter else None,
        include_shared=include_friends,
        user_ids=user_ids_filter if user_ids_filter else None,
    )
    return {"activities": acts, "total": total}


@router.get("/activities/filter-options")
async def filter_options(
    request: Request,
    include_friends: bool = Query(False),
    view_user_ids: str = Query(""),
):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    user_ids_filter = [int(x) for x in view_user_ids.split(',') if x.strip().isdigit()] if view_user_ids else []
    db = db_getter()
    return {
        "types": db.get_activity_types(
            user_id=uid if not user_ids_filter else None,
            include_shared=include_friends,
            user_ids=user_ids_filter if user_ids_filter else None,
        ),
        "years": db.get_years(
            user_id=uid if not user_ids_filter else None,
            include_shared=include_friends,
            user_ids=user_ids_filter if user_ids_filter else None,
        ),
    }


# Explicit routes to prevent /activities/login, /activities/logout etc
# from being caught by the /{activity_id} DELETE route
@router.get("/activities/login")
async def act_login_redirect():
    return RedirectResponse("/login", status_code=303)

@router.get("/activities/logout")
async def act_logout_redirect():
    return RedirectResponse("/logout", status_code=303)


# ── Activity share helpers ─────────────────────────────────────────────────────

def _ensure_activity_shares_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS activity_shares (
            activity_id INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            token       TEXT    NOT NULL,
            PRIMARY KEY (activity_id, user_id)
        )
    """)
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS activity_shares_token ON activity_shares(token)"
    )
    con.commit()


@router.get("/activities/share/{token}", response_class=HTMLResponse)
async def activity_share_page(token: str, request: Request):
    """Public share page — no auth required."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_activity_shares_table(con)
        row = con.execute(
            "SELECT a.id, a.name, u.username, s.user_id "
            "FROM activity_shares s "
            "JOIN activities a ON a.id = s.activity_id "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ?",
            (token,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Share link not found or revoked")
        activity_id, activity_name, display_name, share_uid = row
    finally:
        con.close()
    try:
        profile = db_getter().get_user_profile(share_uid)
        use_metric = profile.get("use_metric", False)
    except Exception:
        use_metric = False
    return templates.TemplateResponse("activity_share.html", {
        "request":       request,
        "token":         token,
        "activity_name": activity_name or "Activity",
        "display_name":  display_name or "Unknown",
        "use_metric":    use_metric,
    })


@router.get("/activities/share/{token}/data")
async def activity_share_data(token: str):
    """Public endpoint — activity metadata for share page."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_activity_shares_table(con)
        row = con.execute(
            "SELECT s.activity_id, s.user_id, u.username "
            "FROM activity_shares s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ?",
            (token,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Share link not found or revoked")
        activity_id, share_uid, display_name = row
    finally:
        con.close()
    db = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")
    try:
        profile = db.get_user_profile(share_uid)
        use_metric = profile.get("use_metric", False)
    except Exception:
        use_metric = False
    safe_fields = [
        "id", "name", "start_time", "activity_type", "distance_mi",
        "duration", "active_time", "total_climb_ft", "total_descent_ft",
        "avg_speed_mph", "avg_heartrate", "max_heartrate", "avg_cadence",
        "avg_power", "suffer_score", "perceived_exertion", "equipment",
        "notes", "strava_activity_id", "points_count", "points_saved",
    ]
    activity = {k: act.get(k) for k in safe_fields}
    return JSONResponse({"activity": activity, "display_name": display_name, "use_metric": use_metric})


@router.get("/activities/share/{token}/points")
async def activity_share_points(token: str):
    """Public endpoint — GPS track for share page (subsampled)."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_activity_shares_table(con)
        row = con.execute(
            "SELECT activity_id FROM activity_shares WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Share link not found or revoked")
        activity_id = row[0]
    finally:
        con.close()
    db = db_getter()
    geo = db.get_track_points_geojson(activity_id)
    coords = (geo.get("geometry") or {}).get("coordinates") or []
    step = max(1, len(coords) // 1000)
    return JSONResponse({"coordinates": coords[::step]})

@router.get("/activities/{activity_id}/json")
async def activity_json(activity_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return act


@router.get("/activities/{activity_id}", response_class=HTMLResponse)
async def activity_detail(request: Request, activity_id: int):
    uid = get_session_user_id(request)
    if uid is None:
        return RedirectResponse(f"/login?next=/activities", status_code=303)
    db       = db_getter()
    activity = db.get_activity(activity_id)
    if not activity:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": f"Activity {activity_id} not found"},
            status_code=404,
        )
    return RedirectResponse(url=f"/activities?select={activity_id}", status_code=302)


@router.delete("/activities/{activity_id}")
async def delete_activity(activity_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")
    if act.get("user_id") not in (None, uid):
        raise HTTPException(403, "Not your activity")
    strava_id = act.get("strava_activity_id")
    db.delete_activities([activity_id])
    if strava_id and uid is not None:
        db.block_strava_activities(uid, [strava_id])
    return {"deleted": activity_id}


@router.delete("/activities")
async def delete_activities_bulk(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    ids  = body.get("ids", [])
    if not ids:
        return {"deleted": 0}
    db    = db_getter()
    # Only delete activities owned by this user
    owned = [r[0] for r in db._con.execute(
        f"SELECT id FROM activities WHERE id IN ({','.join('?'*len(ids))}) AND user_id=?",
        ids + [uid]
    ).fetchall()]
    if not owned:
        return {"deleted": 0}
    ph = ','.join('?' * len(owned))
    strava_ids = [r[0] for r in db._con.execute(
        f"SELECT strava_activity_id FROM activities WHERE id IN ({ph}) AND strava_activity_id IS NOT NULL",
        owned
    ).fetchall()]
    count = db.delete_activities(owned)
    if strava_ids:
        db.block_strava_activities(uid, strava_ids)
    return {"deleted": count}


@router.post("/activities/{activity_id}/publish")
async def publish_activity(activity_id: int, request: Request):
    """Generate (or return existing) public share token for this activity."""
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    db = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")
    if act.get("user_id") not in (None, uid):
        raise HTTPException(403, "Not your activity")
    con = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_activity_shares_table(con)
        existing = con.execute(
            "SELECT token FROM activity_shares WHERE activity_id=? AND user_id=?",
            (activity_id, uid),
        ).fetchone()
        token = existing[0] if existing else _secrets.token_hex(20)
        con.execute(
            "INSERT OR REPLACE INTO activity_shares (activity_id, user_id, token) VALUES (?,?,?)",
            (activity_id, uid, token),
        )
        con.commit()
        return JSONResponse({"token": token})
    finally:
        con.close()


@router.delete("/activities/{activity_id}/publish")
async def revoke_activity_publish(activity_id: int, request: Request):
    """Revoke this user's share token for an activity."""
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    db = db_getter()
    con = sqlite3.connect(db.path, timeout=10)
    try:
        _ensure_activity_shares_table(con)
        con.execute(
            "DELETE FROM activity_shares WHERE activity_id=? AND user_id=?",
            (activity_id, uid),
        )
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()
