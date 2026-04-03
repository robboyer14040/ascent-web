"""
routers/weather.py — Weather and location info for activities.

Checks DB first (attributes_json weather/location keys).
If missing, fetches from APIs and stores back to DB.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()
db_getter = None

WMO_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Heavy thunderstorm with hail",
}

def wmo_desc(code: int) -> str:
    return WMO_CODES.get(code, f"Code {code}")


@router.get("/activities/{activity_id}/weather-location")
async def activity_weather_location(activity_id: int):
    db  = db_getter()
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    # Return cached values from DB if present
    cached_weather   = act.get("weather", "")
    cached_location  = act.get("location", "")

    # Fast path: both cached
    if cached_weather and cached_location:
        return {"weather": {"description": cached_weather}, "locations": cached_location}

    # Need GPS points to fetch whatever is missing
    pts = db.get_track_points(activity_id)
    valid_pts = [p for p in pts
                 if p["lat"] != 999.0 and p["lon"] != 999.0
                 and -90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180
                 and not (p["lat"] == 0 and p["lon"] == 0)]

    if not valid_pts:
        # Return whatever we have cached rather than nulling both
        return {
            "weather":   {"description": cached_weather} if cached_weather else None,
            "locations": cached_location or None,
        }

    start_ts   = act.get("start_time")
    duration_s = act.get("duration") or 3600

    # Fetch whatever is missing
    weather_task  = fetch_weather(valid_pts, start_ts, duration_s) if not cached_weather  else asyncio.sleep(0)
    location_task = fetch_locations(valid_pts)                      if not cached_location else asyncio.sleep(0)

    weather_result, location_result = await asyncio.gather(
        weather_task, location_task, return_exceptions=True
    )

    # Save to DB
    updates = {}
    if not cached_weather and isinstance(weather_result, dict) and weather_result.get("description"):
        updates["weather"] = weather_result["description"]
    if not cached_location and isinstance(location_result, str) and location_result:
        updates["location"] = location_result

    if updates:
        db.update_activity_attrs(activity_id, updates)

    return {
        "weather":   weather_result if isinstance(weather_result, dict) else ({"description": cached_weather} if cached_weather else None),
        "locations": location_result if isinstance(location_result, str) else (cached_location or None),
    }


async def fetch_weather(pts: list, start_ts: int, duration_s: float) -> Optional[dict]:
    if not start_ts:
        return None

    n = len(pts)
    sample_indices = [int(i * (n-1) / 4) for i in range(5)] if n >= 5 else list(range(n))
    samples = [pts[i] for i in sample_indices]

    start_dt     = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    end_dt       = start_dt + timedelta(seconds=max(duration_s, 3600))
    date_str     = start_dt.strftime("%Y-%m-%d")
    end_date_str = end_dt.strftime("%Y-%m-%d")

    avg_lat = sum(s["lat"] for s in samples) / len(samples)
    avg_lon = sum(s["lon"] for s in samples) / len(samples)

    params = {
        "latitude": round(avg_lat, 4), "longitude": round(avg_lon, 4),
        "start_date": date_str, "end_date": end_date_str,
        "hourly": "temperature_2m,wind_speed_10m,precipitation,weather_code,relative_humidity_2m",
        "wind_speed_unit": "kmh", "temperature_unit": "fahrenheit", "timezone": "UTC",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://archive-api.open-meteo.com/v1/era5", params=params)
            if r.status_code != 200:
                return None
            data = r.json()
    except Exception:
        return None

    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    temps  = hourly.get("temperature_2m", [])
    winds  = hourly.get("wind_speed_10m", [])
    precip = hourly.get("precipitation", [])
    codes  = hourly.get("weather_code", [])
    humid  = hourly.get("relative_humidity_2m", [])

    if not times:
        return None

    act_hours = []
    for i, t_str in enumerate(times):
        try:
            t = datetime.strptime(t_str, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
            if start_dt <= t <= end_dt:
                act_hours.append(i)
        except Exception:
            pass

    if not act_hours:
        act_hours = [0]

    def safe(arr, idx):
        try: return arr[idx]
        except: return None

    hour_temps  = [safe(temps, i) for i in act_hours if safe(temps, i) is not None]
    hour_winds  = [safe(winds, i) for i in act_hours if safe(winds, i) is not None]
    hour_humid  = [safe(humid, i) for i in act_hours if safe(humid, i) is not None]
    hour_precip = [safe(precip, i) for i in act_hours if safe(precip, i) is not None]
    hour_codes  = [safe(codes, i) for i in act_hours if safe(codes, i) is not None]

    avg_temp   = sum(hour_temps)  / len(hour_temps)  if hour_temps  else None
    avg_wind   = sum(hour_winds)  / len(hour_winds)  if hour_winds  else None
    avg_humid  = sum(hour_humid)  / len(hour_humid)  if hour_humid  else None
    tot_precip = sum(hour_precip) if hour_precip else 0

    seen_codes, seen_descs = [], []
    for c in hour_codes:
        if c not in seen_codes:
            seen_codes.append(c)
            seen_descs.append(wmo_desc(c))

    return {
        "description":  ", ".join(seen_descs) if seen_descs else None,
        "avg_temp_f":   round(avg_temp, 1)  if avg_temp  is not None else None,
        "avg_wind_kph": round(avg_wind, 1)  if avg_wind  is not None else None,
        "avg_humidity": round(avg_humid, 1) if avg_humid is not None else None,
        "precip_mm":    round(tot_precip, 1),
    }


async def fetch_locations(pts: list) -> Optional[str]:
    n = len(pts)
    num_samples = min(8, n)
    if num_samples < 2:
        return None

    indices = [int(i * (n-1) / (num_samples-1)) for i in range(num_samples)]
    samples = [pts[i] for i in indices]

    places = []
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Ascent-Web/1.0"}) as client:
        for i, pt in enumerate(samples):
            try:
                r = await client.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={"lat": pt["lat"], "lon": pt["lon"],
                            "format": "json", "zoom": 10, "addressdetails": 1},
                )
                if r.status_code == 200:
                    addr = r.json().get("address", {})
                    place = (addr.get("town") or addr.get("city") or
                             addr.get("village") or addr.get("municipality") or
                             addr.get("county") or addr.get("state"))
                    if place:
                        places.append(place)
            except Exception:
                pass
            if i < len(samples) - 1:
                await asyncio.sleep(1.1)

    if not places:
        return None

    deduped = [places[0]]
    for p in places[1:]:
        if p != deduped[-1]:
            deduped.append(p)

    return " → ".join(deduped)
