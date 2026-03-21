"""routers/api.py — JSON API endpoints consumed by the frontend JS."""

import os, json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from typing import Callable, Optional

router = APIRouter()
db_getter: Callable = None


@router.get("/stats/recent")
async def recent_stats(limit: int = Query(15)):
    return db_getter().get_activities(limit=limit, sort_by="start_time", sort_dir="desc")


@router.get("/stats/monthly")
async def monthly_stats(year: Optional[int] = Query(None)):
    return db_getter().get_monthly_totals(year=year)


@router.get("/activities/{activity_id}/geojson")
async def activity_geojson(activity_id: int):
    db = db_getter()
    if not db.get_activity(activity_id):
        raise HTTPException(404, "Activity not found")
    return db.get_track_points_geojson(activity_id)


@router.get("/activities/{activity_id}/charts")
async def activity_charts(activity_id: int):
    db = db_getter()
    if not db.get_activity(activity_id):
        raise HTTPException(404, "Activity not found")
    return db.get_chart_data_for_points(activity_id)


@router.get("/activities/{activity_id}/laps")
async def activity_laps(activity_id: int):
    return db_getter().get_laps(activity_id)


@router.get("/schema")
async def schema_info():
    db = db_getter()
    tables = db.raw_tables()
    return {
        "tables": tables,
        "activities_columns": db.raw_columns("activities"),
        "points_columns": db.raw_columns("points") if "points" in tables else [],
    }


@router.post("/activities/{activity_id}/fetch-points")
async def fetch_points_from_strava(activity_id: int):
    """
    Fetch GPS streams from Strava for an activity that has no local points,
    store them permanently in the points table, and return the point count.
    """
    import os, json, time
    from pathlib import Path
    import httpx
    from app.strava_importer import build_points_rows

    db = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    strava_id = act.get("strava_activity_id")
    if not strava_id:
        raise HTTPException(400, "Activity has no Strava ID — cannot fetch GPS points")

    # Get fresh token
    token_file = Path(os.environ.get("ASCENT_DB_PATH", "")).parent / "strava_tokens.json"
    if not token_file.exists():
        raise HTTPException(401, "Not connected to Strava")

    tokens = json.loads(token_file.read_text())
    if tokens.get("expires_at", 0) <= time.time() + 60:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post("https://www.strava.com/oauth/token", data={
                "client_id":     os.environ.get("STRAVA_CLIENT_ID", ""),
                "client_secret": os.environ.get("STRAVA_CLIENT_SECRET", ""),
                "grant_type":    "refresh_token",
                "refresh_token": tokens["refresh_token"],
            })
            if r.status_code != 200:
                raise HTTPException(401, "Strava token refresh failed")
            data = r.json()
            tokens.update({"access_token": data["access_token"],
                           "refresh_token": data.get("refresh_token", tokens["refresh_token"]),
                           "expires_at": data["expires_at"]})
            token_file.write_text(json.dumps(tokens, indent=2))

    token = tokens["access_token"]
    stream_types = "latlng,heartrate,velocity_smooth,time,cadence,altitude,distance,watts,temp,moving"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}/streams",
            headers={"Authorization": f"Bearer {token}"},
            params={"keys": stream_types, "key_by_type": "true"},
        )
        if resp.status_code == 404:
            raise HTTPException(404, "Activity not found on Strava")
        if resp.status_code == 401:
            raise HTTPException(401, "Strava token invalid — reconnect Strava")
        resp.raise_for_status()
        streams = resp.json()

    # Convert streams to point rows
    point_rows_raw = build_points_rows(streams, activity_id)
    if not point_rows_raw:
        raise HTTPException(422, "No GPS data returned from Strava for this activity")

    # patch track_id (build_points_rows uses activity_id directly now)
    count = db.store_points(activity_id, point_rows_raw)

    return {"status": "ok", "points_stored": count, "activity_id": activity_id}
