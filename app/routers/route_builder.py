import os
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from app.auth import get_session_user_id

router = APIRouter()
db_getter = None
templates = None

# ── Page ──────────────────────────────────────────────────────────────────────

@router.get("/routes", response_class=HTMLResponse)
async def route_builder_page(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        return RedirectResponse("/login?next=/routes", status_code=303)
    user = db_getter().get_user(uid)
    stadia_key = os.environ.get("STADIA_API_KEY", "")
    if stadia_key:
        tile_url = ("https://tiles.stadiamaps.com/tiles/osm_bright"
                    "/{z}/{x}/{y}.png?api_key=" + stadia_key)
    else:
        tile_url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    return templates.TemplateResponse("route_builder.html", {
        "request": request,
        "current_user": user,
        "has_stadia_key": bool(stadia_key),
        "tile_url": tile_url,
    })


# ── Route CRUD ─────────────────────────────────────────────────────────────────

@router.delete("/api/routes/thumbnails")
async def clear_thumbnails(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    n = db_getter().clear_all_thumbnails(uid)
    return JSONResponse({"cleared": n})


@router.get("/api/routes/list")
async def list_routes(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    return JSONResponse(db_getter().get_routes(uid))


@router.post("/api/routes/save")
async def save_route(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    body = await request.json()
    route_id = db_getter().save_route(
        user_id=uid,
        name=body.get("name", "Route"),
        profile=body.get("profile", "bicycle"),
        points=body.get("points", []),
        distance_km=body.get("distance_km"),
        duration_s=body.get("duration_s"),
        climb_m=body.get("climb_m"),
    )
    return JSONResponse({"id": route_id})


@router.get("/api/routes/{route_id}/gpx")
async def download_route_gpx(route_id: int, request: Request):
    from fastapi.responses import Response
    import xml.etree.ElementTree as ET
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    route = db_getter().get_route(route_id, uid)
    if not route:
        raise HTTPException(404, "Route not found")

    name = route["name"]
    points = route["points"] or []

    gpx = ET.Element("gpx", {
        "version": "1.1",
        "creator": "Ascent",
        "xmlns": "http://www.topografix.com/GPX/1/1",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": (
            "http://www.topografix.com/GPX/1/1 "
            "http://www.topografix.com/GPX/1/1/gpx.xsd"
        ),
    })
    trk = ET.SubElement(gpx, "trk")
    ET.SubElement(trk, "name").text = name
    trkseg = ET.SubElement(trk, "trkseg")
    for pt in points:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            ET.SubElement(trkseg, "trkpt", {
                "lat": f"{pt[0]:.7f}",
                "lon": f"{pt[1]:.7f}",
            })

    xml_body = ET.tostring(gpx, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body

    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "route"
    filename = f"{safe}.gpx"

    return Response(
        content=xml_str,
        media_type="application/gpx+xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/routes/{route_id}/thumbnail")
async def get_route_thumbnail(route_id: int, request: Request):
    from fastapi.responses import Response
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    data = db_getter().get_route_thumbnail(route_id, uid)
    if not data:
        raise HTTPException(404)
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


@router.post("/api/routes/{route_id}/thumbnail")
async def save_route_thumbnail(route_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    data = await request.body()
    if len(data) < 100:
        raise HTTPException(400, "Invalid image data")
    db_getter().save_route_thumbnail(route_id, uid, data)
    return JSONResponse({"ok": True})


@router.get("/api/routes/{route_id}")
async def get_route(route_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    route = db_getter().get_route(route_id, uid)
    if not route:
        raise HTTPException(404, "Route not found")
    return JSONResponse(route)


@router.put("/api/routes/{route_id}")
async def update_route(route_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    body = await request.json()
    ok = db_getter().update_route(
        route_id=route_id,
        user_id=uid,
        name=body.get("name", "Route"),
        profile=body.get("profile", "bicycle"),
        points=body.get("points", []),
        distance_km=body.get("distance_km"),
        duration_s=body.get("duration_s"),
        climb_m=body.get("climb_m"),
    )
    if not ok:
        raise HTTPException(404, "Route not found")
    return JSONResponse({"ok": True})


@router.delete("/api/routes/{route_id}")
async def delete_route(route_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    ok = db_getter().delete_route(route_id, uid)
    if not ok:
        raise HTTPException(404, "Route not found")
    return JSONResponse({"ok": True})


# ── Valhalla snap proxy ────────────────────────────────────────────────────────

@router.post("/api/routes/snap")
async def snap_route(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    locations = body.get("locations", [])
    costing   = body.get("costing", "pedestrian")

    if len(locations) < 2:
        raise HTTPException(400, "Need at least 2 locations")

    valhalla_body = {
        "locations": [{"lat": lat, "lon": lon} for lat, lon in locations],
        "costing": costing,
        "directions_options": {"units": "kilometers"},
    }

    stadia_key = os.environ.get("STADIA_API_KEY", "")
    route_url  = (f"https://valhalla.stadiamaps.com/route?api_key={stadia_key}"
                  if stadia_key else "https://valhalla1.openstreetmap.de/route")
    height_url = (f"https://valhalla.stadiamaps.com/height?api_key={stadia_key}"
                  if stadia_key else "https://valhalla1.openstreetmap.de/height")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(route_url, json=valhalla_body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Routing error: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(502, f"Routing unavailable: {e}")

    legs    = data["trip"]["legs"]
    summary = data["trip"]["summary"]

    all_points = []
    for leg in legs:
        pts = _decode_polyline6(leg["shape"])
        if all_points:
            pts = pts[1:]
        all_points.extend(pts)

    # Elevation (best-effort)
    climb_m = None
    try:
        step    = max(1, len(all_points) // 100)
        sampled = all_points[::step]
        h_body  = {"shape": [{"lat": lat, "lon": lon} for lat, lon in sampled],
                   "height_precision": 1}
        async with httpx.AsyncClient(timeout=10.0) as hclient:
            hr = await hclient.post(height_url, json=h_body)
            hr.raise_for_status()
            heights = hr.json().get("height", [])
        if heights:
            climb_m = sum(max(0.0, heights[i] - heights[i - 1])
                          for i in range(1, len(heights)))
    except Exception:
        pass

    return JSONResponse({
        "points":      all_points,
        "distance_km": summary["length"],
        "duration_s":  summary["time"],
        "climb_m":     climb_m,
    })


# ── Strava routes sync ────────────────────────────────────────────────────────

async def _get_fresh_strava_token(uid: int):
    from app.routers.strava import load_tokens, refresh_tokens, tokens_are_fresh, save_tokens
    tokens = load_tokens(user_id=uid)
    if not tokens.get("access_token"):
        raise HTTPException(400, "Strava not connected")
    if not tokens_are_fresh(tokens):
        tokens = await refresh_tokens(tokens, user_id=uid)
        save_tokens(tokens, user_id=uid)
    return tokens["access_token"]


@router.post("/api/routes/strava-sync")
async def strava_sync(request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)

    body = await request.json()
    delete_removed = body.get("delete_removed", True)

    try:
        token = await _get_fresh_strava_token(uid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Token refresh failed: {e}")

    db = db_getter()
    added = 0
    deleted = 0
    updated_starred = 0

    # Fetch all routes from Strava (paginated)
    strava_routes = {}  # sid -> route dict
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for page in range(1, 6):  # up to 150 routes
                resp = await client.get(
                    "https://www.strava.com/api/v3/athlete/routes",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"page": page, "per_page": 30},
                )
                if resp.status_code != 200:
                    break
                routes = resp.json()
                if not routes:
                    break
                for r in routes:
                    sid = str(r.get("id", ""))
                    if sid:
                        strava_routes[sid] = r
    except Exception as e:
        raise HTTPException(502, f"Strava fetch failed: {e}")

    # Delete routes no longer on Strava
    if delete_removed:
        local_strava_ids = db.get_strava_route_ids(uid)
        for sid in local_strava_ids:
            if sid not in strava_routes:
                db.delete_route_by_strava_id(uid, sid)
                deleted += 1

    # Add new routes and update starred status for existing ones
    for sid, r in strava_routes.items():
        starred = bool(r.get("starred", False))
        if db.strava_route_exists(uid, sid):
            # Update starred status
            if db.update_route_starred(uid, sid, starred):
                updated_starred += 1
        else:
            polyline = (r.get("map") or {}).get("polyline") or \
                       (r.get("map") or {}).get("summary_polyline") or ""
            if not polyline:
                continue
            pts = _decode_polyline5(polyline)
            if not pts:
                continue
            rtype = r.get("type", 1)
            profile = "bicycle" if rtype == 1 else "pedestrian"
            dist_km = (r.get("distance") or 0) / 1000
            climb_m = r.get("elevation_gain") or None
            db.save_route(
                user_id=uid,
                name=r.get("name", "Strava Route"),
                profile=profile,
                points=pts,
                distance_km=dist_km if dist_km else None,
                climb_m=climb_m,
                source="strava",
                strava_id=sid,
                starred=starred,
            )
            added += 1

    return JSONResponse({"added": added, "deleted": deleted, "updated_starred": updated_starred})


@router.put("/api/routes/{route_id}/favorite")
async def toggle_local_favorite(route_id: int, request: Request):
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401)
    body = await request.json()
    starred = bool(body.get("starred", False))
    db_getter().set_local_starred(route_id, uid, starred)
    return JSONResponse({"starred": starred})


# ── Polyline decoders ──────────────────────────────────────────────────────────

def _decode_polyline6(encoded: str) -> list:
    """Valhalla precision-6 encoded polyline → [[lat, lon], ...]."""
    i = lat = lng = 0
    result = []
    while i < len(encoded):
        shift = r = 0
        while True:
            b = ord(encoded[i]) - 63; i += 1
            r |= (b & 0x1f) << shift; shift += 5
            if b < 0x20: break
        lat += ~(r >> 1) if (r & 1) else (r >> 1)
        shift = r = 0
        while True:
            b = ord(encoded[i]) - 63; i += 1
            r |= (b & 0x1f) << shift; shift += 5
            if b < 0x20: break
        lng += ~(r >> 1) if (r & 1) else (r >> 1)
        result.append([lat / 1e6, lng / 1e6])
    return result


def _decode_polyline5(encoded: str) -> list:
    """Standard Google/Strava precision-5 encoded polyline → [[lat, lon], ...]."""
    i = lat = lng = 0
    result = []
    while i < len(encoded):
        shift = r = 0
        while True:
            b = ord(encoded[i]) - 63; i += 1
            r |= (b & 0x1f) << shift; shift += 5
            if b < 0x20: break
        lat += ~(r >> 1) if (r & 1) else (r >> 1)
        shift = r = 0
        while True:
            b = ord(encoded[i]) - 63; i += 1
            r |= (b & 0x1f) << shift; shift += 5
            if b < 0x20: break
        lng += ~(r >> 1) if (r & 1) else (r >> 1)
        result.append([lat / 1e5, lng / 1e5])
    return result
