"""
strava_importer.py — Imports Strava activities into the Ascent SQLite database.

Strategy (rate-limit friendly):
  sync()  → fetches activity *summaries* only (1 API call per page of 50).
             GPS streams and activity detail are NOT fetched during sync.
             points_saved=0 is set so the UI knows to offer on-demand fetch.

  On-demand GPS fetch (existing /api/activities/{id}/fetch-points endpoint)
             fetches streams when the user selects an activity, same as before.

Field mapping reverse-engineered from StravaImporter.m:
  (see original file for full mapping table)
"""

import sqlite3
import json
import uuid as uuid_mod
import time
import math
from datetime import datetime, timezone
from typing import Optional
import httpx
import shutil
from pathlib import Path

# ── unit constants ────────────────────────────────────────────────────────────
M_TO_MI    = 0.000621371
M_TO_FT    = 3.28084
MPS_TO_MPH = 2.2369362921


def _f(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default

def _i(v, default=None):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default

def _s(v):
    return str(v) if v is not None else None

def iso_to_unix(iso: str) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None

def parse_tz_offset(act: dict) -> int:
    tz_str = act.get("timezone", "")
    if tz_str:
        try:
            import re
            m = re.search(r'GMT([+-]\d{2}:\d{2})', tz_str)
            if m:
                parts = m.group(1).split(":")
                sign = 1 if parts[0][0] == "+" else -1
                return sign * (abs(int(parts[0])) * 3600 + int(parts[1]) * 60)
        except Exception:
            pass
    return _i(act.get("utc_offset"), 0)

def parse_tz_name(act: dict) -> Optional[str]:
    tz_str = act.get("timezone", "")
    if ") " in tz_str:
        return tz_str.split(") ", 1)[1].strip()
    return None

def event_type_from_workout(workout_type, sport_type: str) -> Optional[str]:
    if workout_type is None:
        return None
    w = int(workout_type)
    st = (sport_type or "").lower()
    if "run" in st:
        if w == 1: return "race"
        if w == 2: return "long_run"
        if w == 3: return "workout"
    return "training"


def build_attributes_json(act: dict, detail: Optional[dict] = None,
                           gear_name: Optional[str] = None) -> str:
    """
    Build the attributes_json flat array ["key", value, "key2", value2, ...].
    Works from summary data alone; detail fields used if available.
    """
    d = detail or act
    attrs = {}

    attrs["name"] = act.get("name", "")
    notes = (d.get("description") or act.get("description") or "").strip()
    if notes:
        attrs["notes"] = notes

    sport = act.get("sport_type") or act.get("type") or ""
    if sport:
        attrs["activity"] = sport

    loc = act.get("location_city") or act.get("location_country") or ""
    if loc:
        attrs["location"] = loc

    if gear_name:
        attrs["equipment"] = gear_name

    evt = event_type_from_workout(act.get("workout_type"), sport)
    if evt:
        attrs["eventType"] = evt

    device = (d.get("device_name") or "").strip()
    if device:
        attrs["computer"] = device

    dist_mi = _f(act.get("distance"), 0) * M_TO_MI
    attrs["distance"] = round(dist_mi, 4)

    elapsed = _f(act.get("elapsed_time"), 0)
    moving  = _f(act.get("moving_time"), 0)
    attrs["durationAsFloat"]       = elapsed
    attrs["movingDurationAsFloat"] = moving

    climb_ft = _f(act.get("total_elevation_gain"), 0) * M_TO_FT
    attrs["totalClimb"] = round(climb_ft, 1)

    elev_high = _f(act.get("elev_high"))
    if elev_high is not None:
        attrs["maxAltitude"] = round(elev_high * M_TO_FT, 1)

    elev_low = _f(act.get("elev_low"))
    if elev_low is not None:
        attrs["minAltitude"] = round(elev_low * M_TO_FT, 1)

    avg_hr = _f(act.get("average_heartrate"))
    if avg_hr:
        attrs["avgHeartRate"] = round(avg_hr, 1)

    max_hr = _f(act.get("max_heartrate"))
    if max_hr:
        attrs["maxHeartRate"] = round(max_hr, 1)

    avg_cad = _f(act.get("average_cadence"))
    if avg_cad:
        attrs["avgCadence"] = round(avg_cad, 1)

    avg_speed_mps = _f(act.get("average_speed"))
    if avg_speed_mps:
        attrs["avgMovingSpeed"] = round(avg_speed_mps * MPS_TO_MPH, 2)
        if avg_speed_mps > 0:
            mins = 60.0 / (avg_speed_mps * MPS_TO_MPH)
            m = int(mins); s = int((mins - m) * 60)
            attrs["avgMovingPace"] = f"{m}:{s:02d}"

    max_speed_mps = _f(act.get("max_speed"))
    if max_speed_mps:
        attrs["maxSpeed"] = round(max_speed_mps * MPS_TO_MPH, 2)

    avg_watts = _f(act.get("weighted_average_watts") or act.get("average_watts"))
    if avg_watts:
        attrs["avgPower"] = round(avg_watts, 1)

    max_watts = _f(act.get("max_watts"))
    if max_watts:
        attrs["maxPower"] = round(max_watts, 1)

    kj = _f(act.get("kilojoules"))
    if kj:
        attrs["work"] = round(kj, 1)

    avg_temp_c = _f(act.get("average_temp"))
    if avg_temp_c is not None:
        attrs["avgTemperature"] = round(avg_temp_c * 9/5 + 32, 1)

    calories = _f(act.get("calories") or (d.get("calories") if d else None))
    if calories:
        attrs["calories"] = round(calories)

    suffer = _f(act.get("suffer_score") or (d.get("suffer_score") if d else None))
    if suffer:
        attrs["sufferScore"] = round(suffer)

    flat = []
    for k, v in attrs.items():
        flat.append(k)
        flat.append(v)
    return json.dumps(flat)


def build_points_rows(streams: dict, activity_id_db: int) -> list[tuple]:
    """
    Convert Strava streams dict into list of tuples for INSERT into points table.
    """
    def stream_data(key):
        s = streams.get(key)
        if isinstance(s, dict):
            return s.get("data", [])
        return []

    time_s   = stream_data("time")
    latlng   = stream_data("latlng")
    distance = stream_data("distance")
    vel      = stream_data("velocity_smooth")
    alt      = stream_data("altitude")
    hr       = stream_data("heartrate")
    cad      = stream_data("cadence")
    tmp      = stream_data("temp")
    watts    = stream_data("watts")
    moving   = stream_data("moving")

    N = max((len(x) for x in [time_s, latlng, distance, vel, alt, hr, cad, tmp, watts] if x),
            default=0)
    if N == 0:
        return []

    rows = []
    prev_time = 0.0
    prev_dist = 0.0

    for i in range(N):
        t  = float(time_s[i]) if i < len(time_s) else prev_time
        dt = max(0.0, t - prev_time)

        lat, lon = 0.0, 0.0
        if i < len(latlng) and len(latlng[i]) == 2:
            lat = float(latlng[i][0])
            lon = float(latlng[i][1])

        a_m    = float(alt[i]) if i < len(alt) else 0.0
        alt_cm = a_m * 3.28084  # stored as feet (column name misleading)

        d_raw = float(distance[i]) if i < len(distance) else (prev_dist / M_TO_MI)
        d_m   = d_raw * M_TO_MI   # metres → miles

        v_mps = float(vel[i]) if i < len(vel) else 0.0

        if i < len(moving):
            is_moving = bool(moving[i])
        else:
            is_moving = v_mps > 0.1
        active_dt = dt if is_moving else 0.0

        hr_bpm  = float(hr[i])    if i < len(hr)    else 0.0
        cad_rpm = float(cad[i])   if i < len(cad)   else 0.0
        power_w = float(watts[i]) if i < len(watts) else 0.0

        temp_c10 = 0.0
        if i < len(tmp):
            temp_f   = float(tmp[i]) * 9/5 + 32
            temp_c10 = temp_f * 10.0

        rows.append((
            activity_id_db, int(t), int(active_dt),
            lat, lon, alt_cm,
            hr_bpm, cad_rpm, temp_c10,
            v_mps, power_w, d_m, 0,
        ))

        prev_time = t
        prev_dist = d_raw

    return rows


def get_support_dir(db_path: str) -> Path:
    p = Path(db_path).parent / "support"
    p.mkdir(exist_ok=True)
    return p

def get_photos_dir(db_path: str, strava_id: int) -> Path:
    d = get_support_dir(db_path) / "photos" / str(strava_id)
    d.mkdir(parents=True, exist_ok=True)
    return d

async def download_activity_photos(
    client: httpx.AsyncClient,
    token: str,
    strava_id: int,
    db_path: str,
    photo_size: int = 1024,
) -> list[str]:
    try:
        resp = await client.get(
            f"https://www.strava.com/api/v3/activities/{strava_id}/photos",
            headers={"Authorization": f"Bearer {token}"},
            params={"size": photo_size, "photo_sources": "true"},
            timeout=30,
        )
        if resp.status_code != 200:
            return []
        photos = resp.json()
        if not isinstance(photos, list) or not photos:
            return []
    except Exception:
        return []

    photos_dir = get_photos_dir(db_path, strava_id)
    filenames  = []

    for i, photo in enumerate(photos):
        urls = photo.get("urls", {})
        url  = urls.get(str(photo_size)) or urls.get("600") or urls.get("100")
        if not url:
            url = photo.get("url") or photo.get("source_url")
        if not url:
            continue

        uid      = photo.get("unique_id") or photo.get("id") or i
        ext      = ".jpg"
        if ".png"  in url.lower(): ext = ".png"
        elif ".webp" in url.lower(): ext = ".webp"
        filename = f"{uid}{ext}"
        dest     = photos_dir / filename

        if dest.exists():
            filenames.append(filename)
            continue

        try:
            r = await client.get(url, timeout=30, follow_redirects=True)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                filenames.append(filename)
        except Exception:
            pass

    return filenames


class StravaImporter:
    """
    Imports Strava activities into the Ascent SQLite database.
    GPS streams are NOT fetched during sync — only activity summaries.
    Streams are fetched on-demand when the user views an activity.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def get_existing_strava_ids(self) -> set:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT strava_activity_id FROM activities WHERE strava_activity_id IS NOT NULL"
            ).fetchall()
            return {r[0] for r in rows}
        finally:
            con.close()

    def get_last_sync_time(self) -> Optional[int]:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT MAX(COALESCE(creation_time_override_s, creation_time_s)) FROM activities"
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            con.close()

    def insert_activity_summary(self, act: dict,
                                 gear_name: Optional[str] = None) -> Optional[int]:
        """
        Insert one Strava activity *summary* (no GPS streams) into the activities table.
        points_saved=0, points_count=0 — streams fetched later on demand.
        Returns the new DB row id, or None if skipped/failed.
        """
        strava_id  = _i(act.get("id"))
        if not strava_id:
            return None

        start_unix = iso_to_unix(act.get("start_date"))
        dist_mi    = _f(act.get("distance"), 0) * M_TO_MI
        act_uuid   = str(uuid_mod.uuid4()).upper()
        attrs_json = build_attributes_json(act, gear_name=gear_name)

        src_distance    = dist_mi
        src_max_speed   = _f(act.get("max_speed"),    0) * MPS_TO_MPH
        src_avg_hr      = _f(act.get("average_heartrate"))
        src_max_hr      = _f(act.get("max_heartrate"))
        src_avg_temp_f  = (_f(act.get("average_temp"), 0) * 9/5 + 32) \
                          if act.get("average_temp") is not None else None
        src_max_elev_ft = _f(act.get("elev_high"), 0) * M_TO_FT
        src_min_elev_ft = _f(act.get("elev_low"),  0) * M_TO_FT
        src_avg_power   = _f(act.get("weighted_average_watts") or act.get("average_watts"))
        src_max_power   = _f(act.get("max_watts"))
        src_avg_cad     = _f(act.get("average_cadence"))
        src_total_climb = _f(act.get("total_elevation_gain"), 0) * M_TO_FT
        src_kj          = _f(act.get("kilojoules"))
        src_elapsed     = _f(act.get("elapsed_time"))
        src_moving      = _f(act.get("moving_time"))

        con = self._connect()
        try:
            cur = con.execute("""
                INSERT INTO activities (
                    uuid, name, creation_time_s, creation_time_override_s,
                    distance_mi, attributes_json,
                    strava_activity_id,
                    src_distance, src_max_speed,
                    src_avg_heartrate, src_max_heartrate,
                    src_avg_temperature,
                    src_max_elevation, src_min_elevation,
                    src_avg_power, src_max_power,
                    src_avg_cadence, src_total_climb,
                    src_kilojoules, src_elapsed_time_s, src_moving_time_s,
                    time_zone, seconds_from_gmt_at_sync,
                    flags, device_id, firmware_version,
                    has_distance_data, moving_speed_only,
                    points_saved, points_count,
                    weight_lb, altitude_smooth_factor, equipment_weight_lb, device_total_time_s,
                    local_media_items_json, photo_urls_json
                ) VALUES (
                    ?,?,?,NULL,
                    ?,?,
                    ?,
                    ?,?,
                    ?,?,
                    ?,
                    ?,?,
                    ?,?,
                    ?,?,
                    ?,?,?,
                    ?,?,
                    0,0,0,
                    1,0,
                    0,0,
                    0,0,0,0,
                    NULL,NULL
                )
            """, (
                act_uuid,
                act.get("name", ""),
                start_unix,
                dist_mi,
                attrs_json,
                strava_id,
                src_distance, src_max_speed,
                src_avg_hr, src_max_hr,
                src_avg_temp_f,
                src_max_elev_ft, src_min_elev_ft,
                src_avg_power, src_max_power,
                src_avg_cad, src_total_climb,
                src_kj, src_elapsed, src_moving,
                parse_tz_name(act), parse_tz_offset(act),
            ))
            activity_db_id = cur.lastrowid

            con.execute("""
                UPDATE meta SET
                    totalTracks = (SELECT COUNT(*) FROM activities),
                    endDate_s   = (SELECT MAX(COALESCE(creation_time_override_s, creation_time_s))
                                   FROM activities)
            """)
            con.commit()
            return activity_db_id

        except Exception as e:
            con.rollback()
            raise e
        finally:
            con.close()

    # Keep old method name as alias for api.py compatibility
    def insert_activity(self, act, streams, detail, gear_name, photo_filenames=None):
        """Legacy method — inserts summary only (streams ignored, fetch on demand)."""
        return self.insert_activity_summary(act, gear_name=gear_name)

    async def sync(
        self,
        access_token: str,
        after_ts:  Optional[int] = None,
        before_ts: Optional[int] = None,
        gear_map:  Optional[dict] = None,
    ):
        """
        Fetch Strava activity *summaries* for the given date range and insert
        into the DB. GPS streams are NOT fetched — they are loaded on demand
        when the user selects an activity in the UI.

        Yields progress dicts for SSE streaming.

        Parameters:
          after_ts  — only fetch activities after this unix timestamp (inclusive)
          before_ts — only fetch activities before this unix timestamp (inclusive)
          gear_map  — {gear_id: gear_name} dict for equipment labelling
        """
        existing_ids = self.get_existing_strava_ids()
        headers      = {"Authorization": f"Bearer {access_token}"}
        page         = 1
        imported     = 0
        skipped      = 0
        errors       = 0
        total_fetched = 0

        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                params = {"page": page, "per_page": 50}
                if after_ts:
                    params["after"] = after_ts
                if before_ts:
                    params["before"] = before_ts

                resp = await client.get(
                    "https://www.strava.com/api/v3/athlete/activities",
                    headers=headers, params=params,
                )
                if resp.status_code == 401:
                    yield {"type": "error", "msg": "Strava token expired — reconnect Strava"}
                    return
                if resp.status_code == 429:
                    yield {"type": "error",
                           "msg": "Strava rate limit hit — wait 15 minutes and try again"}
                    return
                resp.raise_for_status()
                activities = resp.json()

                if not activities:
                    break

                total_fetched += len(activities)
                yield {
                    "type":     "progress",
                    "msg":      f"Fetched page {page} ({total_fetched} activities so far)…",
                    "imported": imported,
                    "skipped":  skipped,
                }

                for act in activities:
                    strava_id = act.get("id")

                    if strava_id in existing_ids:
                        skipped += 1
                        continue

                    name = act.get("name", "(unnamed)")
                    yield {
                        "type":     "progress",
                        "msg":      f"Importing: {name}",
                        "imported": imported,
                        "skipped":  skipped,
                    }

                    # Resolve gear name from pre-fetched map (no extra API call)
                    gear_name = None
                    gear_id   = act.get("gear_id")
                    if gear_id and gear_map:
                        gear_name = gear_map.get(gear_id)

                    try:
                        db_id = self.insert_activity_summary(act, gear_name=gear_name)
                        existing_ids.add(strava_id)
                        imported += 1
                        yield {
                            "type":       "imported",
                            "msg":        f"✓ {name}",
                            "db_id":      db_id,
                            "strava_id":  strava_id,
                            "imported":   imported,
                            "skipped":    skipped,
                        }
                    except Exception as e:
                        errors += 1
                        yield {
                            "type":     "error",
                            "msg":      f"✗ {name}: {e}",
                            "imported": imported,
                            "skipped":  skipped,
                        }

                page += 1

        yield {
            "type":     "done",
            "msg":      (f"Sync complete — {imported} imported, {skipped} already existed"
                         + (f", {errors} errors" if errors else "")
                         + ". GPS tracks load automatically when you view each activity."),
            "imported": imported,
            "skipped":  skipped,
            "errors":   errors,
        }
