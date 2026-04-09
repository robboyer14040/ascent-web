"""routers/activities.py"""

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
    db.delete_activities([activity_id])
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
    count = db.delete_activities(owned)
    return {"deleted": count}

