"""
routers/forecast.py — Weather forecast along a route or activity GPS track.

API:  POST /api/forecast
      Body: { source: "activity"|"route", source_id: int, start_time: ISO-8601 UTC }
      Returns hourly weather samples along the route for the given start time.
"""

import asyncio
import math
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from app.auth import require_user

router = APIRouter()
db_getter = None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(max(0.0, a)))


@router.post("/forecast")
async def get_forecast(request: Request):
    uid  = require_user(request)
    body = await request.json()

    source     = body.get("source")
    source_id  = body.get("source_id")
    start_str  = body.get("start_time")

    if not all([source, source_id is not None, start_str]):
        raise HTTPException(400, "source, source_id, and start_time are required")

    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(400, "Invalid start_time format — expected ISO-8601")

    if start_dt > datetime.now(tz=timezone.utc) + timedelta(days=16):
        raise HTTPException(400, "Forecast is only available up to 16 days ahead")

    db  = db_getter()
    pts: list[dict] = []
    name = ""
    avg_speed_mps = 5.0  # ~18 kph default

    if source == "activity":
        act = db.get_activity(int(source_id))
        if not act:
            raise HTTPException(404, "Activity not found")
        if act.get("user_id") and act["user_id"] != uid:
            raise HTTPException(403, "Access denied")

        raw = db.get_track_points(int(source_id))
        pts = [
            {
                "lat":   p["lat"],
                "lon":   p["lon"],
                # alt_m=0 is ambiguous (sea level vs. missing data); treat as None
                # so gradient logic ignores it rather than creating false grades.
                "alt_m": p["alt_m"] if p.get("alt_m") and 0 < p["alt_m"] < 8850 else None,
            }
            for p in raw
            if -90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180
            and p["lat"] != 999.0
            and not (p["lat"] == 0 and p["lon"] == 0)
        ]
        name = act.get("name") or "Activity"

        spd = act.get("avg_speed_mph") or 0.0
        if spd < 1.0:
            atype = (act.get("activity_type") or act.get("local_sport_type") or "").lower()
            if "run" in atype:
                spd = 6.0
            elif "swim" in atype:
                spd = 1.5
            else:
                spd = 12.0  # cycling default
        avg_speed_mps = spd * 0.44704

    elif source == "route":
        route = db.get_route(int(source_id), uid)
        if not route:
            raise HTTPException(404, "Route not found")

        raw_pts = route.get("points") or []
        pts = [
            {"lat": float(p[0]), "lon": float(p[1]), "alt_m": None}
            for p in raw_pts
            if isinstance(p, (list, tuple)) and len(p) >= 2
        ]
        name = route.get("name") or "Route"

        d_km = route.get("distance_km")
        d_s  = route.get("duration_s")
        if d_km and d_s and d_s > 0:
            avg_speed_mps = (d_km * 1000) / d_s
        else:
            avg_speed_mps = 1.4 if route.get("profile") == "pedestrian" else 5.0

        await _fetch_elevations(pts)

    else:
        raise HTTPException(400, "source must be 'activity' or 'route'")

    if len(pts) < 2:
        raise HTTPException(400, "Not enough GPS points to generate a weather forecast")

    if avg_speed_mps < 0.5:
        avg_speed_mps = 5.0

    result = await _build_forecast(pts, start_dt.timestamp(), avg_speed_mps, name)
    return JSONResponse(result)


async def _fetch_elevations(pts: list[dict]) -> None:
    """Populate alt_m in-place via Open-Meteo elevation API (batched, 100 pts/request)."""
    need = [i for i, p in enumerate(pts) if p.get("alt_m") is None]
    if not need:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        for start in range(0, len(need), 100):
            chunk = need[start:start + 100]
            try:
                r = await client.get(
                    "https://api.open-meteo.com/v1/elevation",
                    params={
                        "latitude":  ",".join(f"{pts[i]['lat']:.4f}" for i in chunk),
                        "longitude": ",".join(f"{pts[i]['lon']:.4f}" for i in chunk),
                    },
                )
                if r.status_code == 200:
                    for idx, elev in zip(chunk, r.json().get("elevation", [])):
                        if elev is not None:
                            pts[idx]["alt_m"] = float(elev)
            except Exception:
                pass  # elevation unavailable; gradient adjustment skipped for this chunk


# Grade above this → "climbing" (40 % speed penalty); below → flat/descent (40 % bonus).
_CLIMB_GRADE = 0.01   # 1 %


async def _build_forecast(pts: list, start_ts: float, speed_mps: float, name: str) -> dict:
    has_elev = any(p.get("alt_m") is not None for p in pts)

    # Cumulative distance (m) and elapsed time (s) at each point.
    # Speed per segment is adjusted for gradient when elevation is available.
    cum_d = [0.0]
    cum_t = [0.0]
    for i in range(1, len(pts)):
        seg_d = _haversine_m(
            pts[i-1]["lat"], pts[i-1]["lon"],
            pts[i]["lat"],   pts[i]["lon"],
        )
        if has_elev:
            a0 = pts[i-1].get("alt_m")
            a1 = pts[i].get("alt_m")
            if a0 is not None and a1 is not None and seg_d > 0:
                grade = (a1 - a0) / seg_d
                seg_spd = speed_mps * (0.6 if grade >= _CLIMB_GRADE else 1.4)
            else:
                seg_spd = speed_mps
        else:
            seg_spd = speed_mps
        cum_d.append(cum_d[-1] + seg_d)
        cum_t.append(cum_t[-1] + seg_d / max(seg_spd, 0.1))

    total_m = cum_d[-1]
    total_s = cum_t[-1]

    # ~1 sample per 15 min, capped between 5 and 30
    N = max(5, min(30, int(total_s / 900) + 1))

    samples_meta = []
    for i in range(N):
        target = total_m * i / (N - 1)
        idx = next((j for j, d in enumerate(cum_d) if d >= target), len(pts) - 1)
        eta_ts = start_ts + cum_t[idx]
        samples_meta.append({
            "lat":    pts[idx]["lat"],
            "lon":    pts[idx]["lon"],
            "dist_m": cum_d[idx],
            "eta_ts": eta_ts,
            "eta_dt": datetime.fromtimestamp(eta_ts, tz=timezone.utc),
        })

    start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    end_dt   = datetime.fromtimestamp(start_ts + total_s, tz=timezone.utc)

    async with httpx.AsyncClient(timeout=15) as client:
        wx_list = await asyncio.gather(
            *[_fetch_point_wx(client, sm["lat"], sm["lon"], sm["eta_dt"], start_dt, end_dt)
              for sm in samples_meta],
            return_exceptions=True,
        )

    samples = []
    for sm, wx in zip(samples_meta, wx_list):
        if isinstance(wx, dict) and wx:
            samples.append({
                "time_iso":      sm["eta_dt"].isoformat(),
                "time_offset_h": round((sm["eta_ts"] - start_ts) / 3600, 2),
                "dist_km":       round(sm["dist_m"] / 1000, 1),
                **wx,
            })

    if not samples:
        raise HTTPException(
            502,
            "No weather data returned — verify the date is within 16 days from today",
        )

    temps  = [s["temp_f"]     for s in samples if s.get("temp_f")     is not None]
    winds  = [s["wind_mph"]   for s in samples if s.get("wind_mph")   is not None]
    precip = [s["precip_prob"] for s in samples if s.get("precip_prob") is not None]

    # Subsample route points for map preview (max 120 points)
    n_pts = len(pts)
    step = max(1, n_pts // 120)
    preview = [[pts[i]["lat"], pts[i]["lon"]] for i in range(0, n_pts, step)]
    if [pts[-1]["lat"], pts[-1]["lon"]] != preview[-1]:
        preview.append([pts[-1]["lat"], pts[-1]["lon"]])

    return {
        "name":           name,
        "start_time_iso": start_dt.isoformat(),
        "duration_h":     round(total_s / 3600, 2),
        "distance_km":    round(total_m / 1000, 1),
        "avg_speed_kph":  round(total_m / total_s * 3.6, 1) if total_s > 0 else round(speed_mps * 3.6, 1),
        "samples":        samples,
        "preview_points": preview,
        "summary": {
            "avg_temp_f":      round(sum(temps) / len(temps), 1) if temps  else None,
            "min_temp_f":      round(min(temps), 1)              if temps  else None,
            "max_temp_f":      round(max(temps), 1)              if temps  else None,
            "avg_wind_mph":    round(sum(winds) / len(winds), 1) if winds  else None,
            "max_wind_mph":    round(max(winds), 1)              if winds  else None,
            "max_precip_prob": round(max(precip))                if precip else None,
        },
    }


async def _fetch_point_wx(
    client: httpx.AsyncClient,
    lat: float, lon: float,
    eta_dt: datetime,
    start_dt: datetime, end_dt: datetime,
) -> Optional[dict]:
    d0 = min(start_dt, eta_dt).strftime("%Y-%m-%d")
    d1 = max(end_dt,   eta_dt).strftime("%Y-%m-%d")

    params = {
        "latitude":  round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": ",".join([
            "temperature_2m", "apparent_temperature",
            "wind_speed_10m", "wind_direction_10m",
            "precipitation_probability", "precipitation",
            "cloud_cover", "weather_code",
        ]),
        "temperature_unit":   "fahrenheit",
        "wind_speed_unit":    "mph",
        "precipitation_unit": "mm",
        "timezone":           "UTC",
        "start_date":         d0,
        "end_date":           d1,
    }

    try:
        r = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    if not times:
        return None

    # Closest hour index to ETA
    eta_str  = eta_dt.strftime("%Y-%m-%dT%H:00")
    best_idx = next((i for i, t in enumerate(times) if t == eta_str), None)
    if best_idx is None:
        eta_ts   = eta_dt.timestamp()
        best_idx, best_d = 0, float("inf")
        for i, t in enumerate(times):
            try:
                td = abs(
                    datetime.strptime(t, "%Y-%m-%dT%H:%M")
                    .replace(tzinfo=timezone.utc).timestamp() - eta_ts
                )
                if td < best_d:
                    best_d, best_idx = td, i
            except Exception:
                pass

    def _s(key):
        arr = hourly.get(key, [])
        try:
            return arr[best_idx]
        except Exception:
            return None

    return {
        "temp_f":       _s("temperature_2m"),
        "feels_like_f": _s("apparent_temperature"),
        "wind_mph":     _s("wind_speed_10m"),
        "wind_dir_deg": _s("wind_direction_10m"),
        "precip_prob":  _s("precipitation_probability"),
        "precip_mm":    _s("precipitation"),
        "cloud_cover":  _s("cloud_cover"),
        "weather_code": _s("weather_code"),
    }
