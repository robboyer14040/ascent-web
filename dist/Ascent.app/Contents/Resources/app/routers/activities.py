"""routers/activities.py"""

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Callable, Optional

router = APIRouter()
db_getter: Callable = None
templates = None


@router.get("/activities", response_class=HTMLResponse)
async def activities_spa(request: Request):
    return templates.TemplateResponse("main.html", {"request": request})


@router.get("/activities/list")
async def activities_json(
    limit:         int           = Query(1000),
    offset:        int           = Query(0),
    search:        str           = Query(""),
    activity_type: str           = Query(""),
    sort_by:       str           = Query("start_time"),
    sort_dir:      str           = Query("desc"),
    year:          Optional[int] = Query(None),
):
    db    = db_getter()
    acts  = db.get_activities(limit=limit, offset=offset, search=search,
                              activity_type=activity_type, sort_by=sort_by,
                              sort_dir=sort_dir, year=year)
    total = db.count_activities(search=search, activity_type=activity_type, year=year)
    return {"activities": acts, "total": total}


@router.get("/activities/filter-options")
async def filter_options():
    db = db_getter()
    return {"types": db.get_activity_types(), "years": db.get_years()}


@router.get("/activities/{activity_id}/json")
async def activity_json(activity_id: int):
    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return act


@router.get("/activities/{activity_id}", response_class=HTMLResponse)
async def activity_detail(request: Request, activity_id: int):
    db       = db_getter()
    activity = db.get_activity(activity_id)
    if not activity:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": f"Activity {activity_id} not found"},
            status_code=404,
        )
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/activities?select={activity_id}", status_code=302)
