"""
db.py — SQLite access layer for the Ascent .db file.

ACTUAL SCHEMA (from inspect_db.py output):

  activities (801 rows):
    id, uuid, name, creation_time_s, creation_time_override_s,
    distance_mi, weight_lb, altitude_smooth_factor, equipment_weight_lb,
    device_total_time_s, moving_speed_only, has_distance_data,
    attributes_json,   ← JSON blob with ALL activity metadata
    markers_json, override_json, seconds_from_gmt_at_sync, time_zone,
    flags, device_id, firmware_version, photo_urls_json,
    strava_activity_id,
    src_distance, src_max_speed, src_avg_heartrate, src_max_heartrate,
    src_avg_temperature, src_max_elevation, src_min_elevation,
    src_avg_power, src_max_power, src_avg_cadence, src_total_climb,
    src_kilojoules, src_elapsed_time_s, src_moving_time_s,
    local_media_items_json, points_saved, points_count

  points (372,399 rows):
    id, track_id, wall_clock_delta_s, active_time_delta_s,
    latitude_e7, longitude_e7,   ← already degrees (not ×1e7!)
    orig_altitude_cm,            ← centimetres
    heartrate_bpm, cadence_rpm,
    temperature_c10,             ← tenths of °F (NOT °C! 60.8 = 60.8°F in Santa Cruz)
    speed_mps, power_w, orig_distance_m, flags

  laps (0 rows currently): track_id, lap_index, start_time_delta_s, ...
  meta (1 row): uuid_s, startDate_s, endDate_s, lastSyncTime_s, totalTracks

attributes_json keys (from tableInfo_json in meta):
  name, notes, weather, location, effort, disposition, activity (type),
  equipment, keyword1, keyword2, custom, computer, device,
  avgHeartRate, maxHeartRate, avgCadence, maxCadence,
  avgSpeed, maxSpeed, avgMovingSpeed, avgPace, avgMovingPace,
  totalClimb, totalDescent, rateOfClimb, rateOfDescent,
  avgPower, maxPower, work, calories, sufferScore,
  avgTemperature, maxTemperature, minTemperature,
  avgAltitude, maxAltitude, minAltitude, avgGradient, maxGradient, minGradient,
  durationAsFloat, movingDurationAsFloat, distance,
  firmwareVersion, eventType, ...

Unit notes:
  distance_mi         → miles (stored directly)
  src_distance        → miles
  orig_altitude_cm    → cm → ft: × (1/100) × 3.28084
  speed_mps           → mph: × 2.23694
  temperature_c10     → tenths of °F → °F: ÷ 10
  orig_distance_m     → metres (cumulative within track)
  src_total_climb     → feet (Strava stores in metres but Ascent converts)
  attributes avgHeartRate etc → stored as float strings or numbers in JSON
"""

import sqlite3
import json
import os
from typing import Optional, Optional

# ── unit helpers ──────────────────────────────────────────────────────────────
# Column names in the DB are MISLEADING — actual stored units per TrackPointStore.m:
#   orig_altitude_cm → FEET (not cm)     BAD_ALTITUDE = 1_000_000
#   orig_distance_m  → MILES (not m)     BAD_DISTANCE  = 1_000_000
#   temperature_c10  → tenths of °F (not °C×10)
#   latitude_e7      → degrees (not ×1e7)
#   longitude_e7     → degrees (not ×1e7)
#   speed_mps        → m/s (correct)
#   BAD_LATLON = 999.0
CM_TO_FT   = 1.0        # orig_altitude_cm stores feet directly
MPS_TO_MPH = 2.23694
M_TO_MILES = 1.0 / 1609.344

def cm_to_ft(cm):
    if cm is None: return None
    return cm * CM_TO_FT

def mps_to_mph(mps):
    if mps is None: return None
    return mps * MPS_TO_MPH

def f10_to_f(f10):
    """Tenths of °F → °F"""
    if f10 is None: return None
    return f10 / 10.0

def secs_to_hms(secs):
    if not secs: return "—"
    secs = int(secs)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h}:{m:02d}:{s:02d}"

def pace_str(speed_mph):
    """mph → min/mile string"""
    if not speed_mph or speed_mph <= 0: return "—"
    mins = 60.0 / speed_mph
    m = int(mins)
    s = int((mins - m) * 60)
    return f"{m}:{s:02d}/mi"

def safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default

def safe_int(v, default=0):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ── attributes_json parser ────────────────────────────────────────────────────
# Maps the JSON keys in attributes_json to canonical field names.
# The JSON is stored as a flat dict: {"name": "My Ride", "avgHeartRate": 142.0, ...}

ATTR_KEYS = [
    "name", "notes", "weather", "location", "effort", "disposition",
    "activity",       # activity type (Run, Ride, etc.)
    "equipment", "keyword1", "keyword2", "custom", "computer", "device",
    "eventType", "firmwareVersion",
    # computed stats stored in attributes
    "avgHeartRate", "maxHeartRate",
    "avgCadence", "maxCadence",
    "avgSpeed", "maxSpeed", "avgMovingSpeed",
    "avgPace", "avgMovingPace",
    "totalClimb", "totalDescent", "rateOfClimb", "rateOfDescent",
    "avgPower", "maxPower", "work", "calories", "sufferScore",
    "avgTemperature", "maxTemperature", "minTemperature",
    "avgAltitude", "maxAltitude", "minAltitude",
    "avgGradient", "maxGradient", "minGradient",
    "durationAsFloat", "movingDurationAsFloat", "distance",
]

def parse_attrs(json_str: Optional[str]) -> dict:
    """
    Parse the attributes_json blob into a flat dict.

    Ascent stores attributes as a flat JSON array of alternating key/value pairs
    (from NSMutableArray serialization):
        ["name", "My Ride", "avgHeartRate", 142.0, "activity", "Ride", ...]

    Convert to: {"name": "My Ride", "avgHeartRate": 142.0, "activity": "Ride", ...}
    """
    if not json_str:
        return {}
    try:
        data = json.loads(json_str)
        if isinstance(data, dict):
            return data  # already a dict (shouldn't happen but handle it)
        if isinstance(data, list) and len(data) % 2 == 0:
            return dict(zip(data[::2], data[1::2]))
        if isinstance(data, list):
            # odd-length fallback: zip truncates to shorter
            return dict(zip(data[::2], data[1::2]))
        return {}
    except Exception:
        return {}


def build_activity(row: sqlite3.Row) -> dict:
    """
    Convert a raw activities row into a display-ready dict.
    Merges attributes_json fields and applies unit conversions.
    """
    d = dict(row)
    attrs = parse_attrs(d.get("attributes_json"))

    # ── identity ──────────────────────────────────────────────────────────
    a = {
        "id":               d["id"],
        "uuid":             d.get("uuid"),
        "start_time":       d.get("creation_time_override_s") or d.get("creation_time_s"),
        "strava_activity_id": d.get("strava_activity_id"),
        "points_saved":     d.get("points_saved", 0),
        "points_count":     d.get("points_count", 0),
        "flags":            d.get("flags", 0),
        "time_zone":        d.get("time_zone"),
        "seconds_from_gmt": d.get("seconds_from_gmt_at_sync", 0),
    }

    # ── name / text ───────────────────────────────────────────────────────
    a["name"]          = attrs.get("name") or d.get("name") or "(unnamed)"
    a["notes"]         = attrs.get("notes", "")
    a["weather"]       = attrs.get("weather", "")
    a["location"]      = attrs.get("location", "")
    a["effort"]        = attrs.get("effort", "")
    a["disposition"]   = attrs.get("disposition", "")
    a["activity_type"] = attrs.get("activity", "")
    a["equipment"]     = attrs.get("equipment", "")
    a["keyword1"]      = attrs.get("keyword1", "")
    a["keyword2"]      = attrs.get("keyword2", "")
    a["custom"]        = attrs.get("custom", "")
    a["computer"]      = attrs.get("computer", "")
    a["device"]        = attrs.get("device", "")
    a["event_type"]    = attrs.get("eventType", "")

    # ── distance ──────────────────────────────────────────────────────────
    dist_mi = safe_float(d.get("distance_mi")) or safe_float(attrs.get("distance")) or safe_float(d.get("src_distance"))
    a["distance_mi"] = round(dist_mi, 2)
    a["distance_km"] = round(dist_mi * 1.60934, 2)

    # ── duration ─────────────────────────────────────────────────────────
    duration_s     = safe_float(attrs.get("durationAsFloat") or d.get("src_elapsed_time_s"))
    moving_time_s  = safe_float(attrs.get("movingDurationAsFloat") or d.get("src_moving_time_s"))
    a["duration"]        = round(duration_s)
    a["active_time"]     = round(moving_time_s) if moving_time_s else round(duration_s)
    a["duration_hms"]    = secs_to_hms(duration_s)
    a["active_time_hms"] = secs_to_hms(moving_time_s or duration_s)

    # ── elevation ─────────────────────────────────────────────────────────
    # totalClimb in attributes is in feet (Ascent's display unit)
    climb_ft = safe_float(attrs.get("totalClimb")) or safe_float(d.get("src_total_climb"))
    descent_ft = safe_float(attrs.get("totalDescent"))
    a["total_climb_ft"]   = round(climb_ft)
    a["total_descent_ft"] = round(descent_ft)
    a["total_climb_m"]    = round(climb_ft / 3.28084)

    # ── speed ─────────────────────────────────────────────────────────────
    avg_moving_spd = safe_float(attrs.get("avgMovingSpeed") or attrs.get("avgSpeed") or d.get("src_max_speed"))
    a["avg_speed_mph"]   = round(avg_moving_spd, 1)
    a["avg_pace"]        = pace_str(avg_moving_spd)
    a["max_speed_mph"]   = round(safe_float(attrs.get("maxSpeed")), 1)
    # Overall avg speed = distance / elapsed time (includes stops)
    _dur = duration_s or a.get("duration") or 0
    if dist_mi and _dur and _dur > 0:
        a["avg_overall_speed_mph"] = round(dist_mi / (_dur / 3600.0), 1)
    else:
        a["avg_overall_speed_mph"] = 0

    # ── heart rate ────────────────────────────────────────────────────────
    a["avg_heartrate"] = round(safe_float(attrs.get("avgHeartRate") or d.get("src_avg_heartrate")))
    a["max_heartrate"] = round(safe_float(attrs.get("maxHeartRate") or d.get("src_max_heartrate")))

    # ── cadence / power ───────────────────────────────────────────────────
    a["avg_cadence"] = round(safe_float(attrs.get("avgCadence") or d.get("src_avg_cadence")))
    a["max_cadence"] = round(safe_float(attrs.get("maxCadence")))
    a["avg_power"]   = round(safe_float(attrs.get("avgPower") or d.get("src_avg_power")))
    a["max_power"]   = round(safe_float(attrs.get("maxPower") or d.get("src_max_power")))
    a["work_kj"]     = round(safe_float(attrs.get("work") or d.get("src_kilojoules")))

    # ── misc ──────────────────────────────────────────────────────────────
    a["calories"]     = round(safe_float(attrs.get("calories")))
    a["suffer_score"] = round(safe_float(attrs.get("sufferScore")))

    # Photos — pass through raw JSON string; API layer parses it
    a["local_media_items_json"] = d.get("local_media_items_json")

    # ── altitude ─────────────────────────────────────────────────────────
    a["max_altitude_ft"] = round(safe_float(attrs.get("maxAltitude") or d.get("src_max_elevation")))
    a["min_altitude_ft"] = round(safe_float(attrs.get("minAltitude") or d.get("src_min_elevation")))
    a["avg_altitude_ft"] = round(safe_float(attrs.get("avgAltitude")))

    # ── temperature ───────────────────────────────────────────────────────
    a["avg_temp_f"] = round(safe_float(attrs.get("avgTemperature") or d.get("src_avg_temperature")), 1)
    a["max_temp_f"] = round(safe_float(attrs.get("maxTemperature")), 1)
    a["min_temp_f"] = round(safe_float(attrs.get("minTemperature")), 1)

    return a


# ── AscentDB ──────────────────────────────────────────────────────────────────

class AscentDB:
    """Read-only access layer for the Ascent SQLite database."""

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            from app.create_db import create_db
            create_db(path)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        # Migration: add start_lat/start_lon and map bbox columns if missing
        for col in ("start_lat REAL", "start_lon REAL",
                    "map_min_lat REAL", "map_max_lat REAL",
                    "map_min_lon REAL", "map_max_lon REAL"):
            try:
                self._con.execute(f"ALTER TABLE activities ADD COLUMN {col}")
                self._con.commit()
            except Exception:
                pass

    def close(self):
        self._con.close()

    # ── activity list ─────────────────────────────────────────────────────

    def get_activities(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str = "",
        activity_type: str = "",
        sort_by: str = "creation_time_s",
        sort_dir: str = "desc",
        year: Optional[int] = None,
        user_id: Optional[int] = None,
        include_shared: bool = False,
    ) -> list[dict]:

        where, params = self._build_where(search, activity_type, year,
                                          user_id=user_id, include_shared=include_shared)
        order = self._safe_order(sort_by, sort_dir)

        sql = f"""
            SELECT * FROM activities
            {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
        params += [limit, offset]
        rows = self._con.execute(sql, params).fetchall()
        return [build_activity(r) for r in rows]

    def get_activity(self, activity_id: int) -> Optional[dict]:
        row = self._con.execute(
            "SELECT * FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
        return build_activity(row) if row else None

    def delete_activities(self, ids: list[int]) -> int:
        """Delete activities (and their points/laps via CASCADE) by id list."""
        if not ids:
            return 0
        placeholders = ','.join('?' * len(ids))
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            cur = con.execute(
                f"DELETE FROM activities WHERE id IN ({placeholders})", ids
            )
            count = cur.rowcount
            con.commit()
            return count
        except Exception as e:
            con.rollback()
            raise e
        finally:
            con.close()

    def count_activities(
        self, search: str = "", activity_type: str = "", year: Optional[int] = None,
        user_id: Optional[int] = None, include_shared: bool = False,
    ) -> int:
        where, params = self._build_where(search, activity_type, year,
                                          user_id=user_id, include_shared=include_shared)
        return self._con.execute(
            f"SELECT COUNT(*) FROM activities {where}", params
        ).fetchone()[0]

    def get_activity_types(self, user_id: Optional[int] = None,
                            include_shared: bool = False) -> list[str]:
        """Activity types stored in attributes_json as {"activity": "Ride"}."""
        try:
            where, params = self._build_where("", "", None,
                                               user_id=user_id, include_shared=include_shared)
            user_clause = where + (" AND " if where else " WHERE ")
            rows = self._con.execute(
                f"""SELECT DISTINCT json_extract(attributes_json, '$.activity') AS t
                   FROM activities
                   {where}
                   {"AND" if where else "WHERE"} t IS NOT NULL AND t != ''
                   ORDER BY t""",
                params
            ).fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []

    def get_years(self, user_id: Optional[int] = None,
                  include_shared: bool = False) -> list[int]:
        where, params = self._build_where("", "", None,
                                          user_id=user_id, include_shared=include_shared)
        rows = self._con.execute(
            f"""SELECT DISTINCT strftime('%Y', datetime(
                   COALESCE(creation_time_override_s, creation_time_s), 'unixepoch'
               )) AS y
               FROM activities
               {where}
               {"AND" if where else "WHERE"} creation_time_s IS NOT NULL
               ORDER BY y DESC""",
            params
        ).fetchall()
        return [int(r[0]) for r in rows if r[0]]

    # ── track points ──────────────────────────────────────────────────────

    def get_track_points(self, activity_id: int) -> list[dict]:
        rows = self._con.execute(
            """SELECT wall_clock_delta_s, active_time_delta_s,
                      latitude_e7, longitude_e7,
                      orig_altitude_cm, heartrate_bpm,
                      cadence_rpm, temperature_c10,
                      speed_mps, power_w, orig_distance_m, flags
               FROM points
               WHERE track_id = ?
               ORDER BY wall_clock_delta_s ASC, active_time_delta_s ASC""",
            (activity_id,),
        ).fetchall()

        result = []
        for r in rows:
            alt_ft = (r["orig_altitude_cm"] or 0) * CM_TO_FT
            result.append({
                "t":         r["wall_clock_delta_s"],
                "at":        r["active_time_delta_s"],
                "lat":       r["latitude_e7"],
                "lon":       r["longitude_e7"],
                "alt_ft":    round(alt_ft, 1),
                "alt_m":     round((r["orig_altitude_cm"] or 0) / 3.28084, 1),  # ft→m for GeoJSON (orig_altitude_cm stores feet)
                "hr":        r["heartrate_bpm"] or 0,
                "cad":       r["cadence_rpm"] or 0,
                "temp_f":    round(f10_to_f(r["temperature_c10"]) or 0, 1),
                "speed_mph": round(mps_to_mph(r["speed_mps"] or 0), 2),
                "power":     r["power_w"] or 0,
                "dist_m":    r["orig_distance_m"] or 0,  # miles (orig_distance_m column stores miles)
                "flags":     r["flags"] or 0,
            })
        return result

    def get_track_points_geojson(self, activity_id: int) -> dict:
        pts = self.get_track_points(activity_id)
        # Filter bad GPS points (0,0 or out of range)
        # BAD_LATLON = 999.0 (from Defs.h) marks dead zone points — exclude those
        # Also exclude 0,0 (null island) and anything outside valid ranges
        coords = [
            [p["lon"], p["lat"], p["alt_m"]]
            for p in pts
            if p["lat"] != 999.0 and p["lon"] != 999.0
            and -90.0 <= p["lat"] <= 90.0
            and -180.0 <= p["lon"] <= 180.0
            and not (p["lat"] == 0.0 and p["lon"] == 0.0)
        ]
        return {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"activity_id": activity_id},
        }

    def store_points(self, activity_id: int, points_rows: list) -> int:
        """
        Insert GPS points into the points table and update points_saved/points_count.
        points_rows: list of tuples matching points table columns (without id).
        Returns number of rows inserted.
        """
        if not points_rows:
            return 0
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            # Delete any existing points for this activity first
            con.execute("DELETE FROM points WHERE track_id=?", (activity_id,))
            con.executemany("""
                INSERT INTO points (
                    track_id, wall_clock_delta_s, active_time_delta_s,
                    latitude_e7, longitude_e7, orig_altitude_cm,
                    heartrate_bpm, cadence_rpm, temperature_c10,
                    speed_mps, power_w, orig_distance_m, flags
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, points_rows)
            count = len(points_rows)
            con.execute(
                "UPDATE activities SET points_saved=1, points_count=? WHERE id=?",
                (count, activity_id)
            )
            con.commit()
            return count
        except Exception as e:
            con.rollback()
            raise e
        finally:
            con.close()

    def get_chart_data_for_points(self, activity_id: int) -> dict:
        import math
        all_pts = self.get_track_points(activity_id)
        # Exclude BAD_LATLON (999.0) dead zone markers from chart data
        # Also exclude points where distance is BAD_DISTANCE (1000000m) 
        BAD_DIST_VAL = 1000.0 * 1000.0
        # BAD_LATLON=999.0, BAD_ALTITUDE=1000*1000, BAD_DISTANCE=1000*1000
        BAD_VAL = 999999.0
        pts = [p for p in all_pts
               if p["lat"] != 999.0 and p["lon"] != 999.0
               and p["alt_ft"] < BAD_VAL]

        # BAD_DISTANCE = 1000000.0 (1000*1000) marks points without valid distance
        # Filter these out and check if any real distance data exists
        BAD_DISTANCE = 999999.0  # BAD_DISTANCE = 1000*1000
        dist_m_raw = [p["dist_m"] for p in pts]  # miles (orig_distance_m stores miles)
        clean_dist = [v for v in dist_m_raw if 0 < v < BAD_DISTANCE]
        has_dist = len(clean_dist) > len(pts) * 0.5  # >50% valid points have distance

        if has_dist:
            # Stored in miles — convert to metres for chart (JS divides by 1609)
            dist_m = []
            last_good = 0.0
            for v in dist_m_raw:
                if v >= BAD_DISTANCE:
                    dist_m.append(last_good * 1609.344)
                else:
                    dist_m.append(v * 1609.344)  # miles → metres
                    last_good = v
        else:
            # Calculate cumulative distance from GPS coordinates (haversine) in metres
            dist_m = [0.0]
            for i in range(1, len(pts)):
                lat1, lon1 = math.radians(pts[i-1]["lat"]), math.radians(pts[i-1]["lon"])
                lat2, lon2 = math.radians(pts[i]["lat"]),   math.radians(pts[i]["lon"])
                dlat = lat2 - lat1
                dlon = lon2 - lon1
                a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
                d_m = 6371000 * 2 * math.asin(min(1, math.sqrt(a)))
                dist_m.append(dist_m[-1] + d_m)  # keep in metres

        return {
            "time":    [p["t"] for p in pts],   # wall_clock_delta_s = cumulative elapsed seconds
            "alt_ft":  [p["alt_ft"] for p in pts],
            "hr":      [p["hr"] for p in pts],
            "speed":   [p["speed_mph"] for p in pts],
            "power":   [p["power"] for p in pts],
            "cadence": [p["cad"] for p in pts],
            "dist_m":  dist_m,  # in miles (orig_distance_m stores miles)
        }

    # ── laps ──────────────────────────────────────────────────────────────

    def get_laps(self, activity_id: int) -> list[dict]:
        try:
            rows = self._con.execute(
                "SELECT * FROM laps WHERE track_id = ? ORDER BY lap_index ASC",
                (activity_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ── dashboard stats ───────────────────────────────────────────────────

    def get_dashboard_stats(self, user_id: Optional[int] = None,
                             include_shared: bool = False) -> dict:
        # Aggregate from activities table + attributes_json
        where, params = self._build_where("", "", None,
                                          user_id=user_id, include_shared=include_shared)
        row = self._con.execute(
            f"""SELECT
                COUNT(*)                                                AS total,
                SUM(distance_mi)                                        AS total_dist,
                SUM(src_moving_time_s)                                  AS total_moving_s,
                SUM(src_total_climb)                                    AS total_climb_ft,
                AVG(src_avg_heartrate)                                  AS avg_hr,
                MAX(distance_mi)                                        AS longest_mi,
                MAX(src_total_climb)                                    AS most_climb_ft
               FROM activities {where}""",
            params
        ).fetchone()

        climb_where = (where + " AND " if where else " WHERE ")
        climb_row = self._con.execute(
            f"""SELECT SUM(CAST(json_extract(attributes_json,'$.totalClimb') AS REAL)) AS c
               FROM activities
               {climb_where}src_total_climb IS NULL OR src_total_climb = 0""",
            params
        ).fetchone()

        total_climb = (row["total_climb_ft"] or 0) + (climb_row["c"] or 0)

        return {
            "total_activities":  row["total"] or 0,
            "total_distance_mi": round(row["total_dist"] or 0, 1),
            "total_distance_km": round((row["total_dist"] or 0) * 1.60934, 1),
            "total_active_hms":  secs_to_hms(row["total_moving_s"] or 0),
            "total_climb_ft":    round(total_climb),
            "total_climb_m":     round(total_climb / 3.28084),
            "avg_heartrate":     round(row["avg_hr"] or 0),
            "longest_mi":        round(row["longest_mi"] or 0, 2),
            "most_climb_ft":     round(row["most_climb_ft"] or 0),
        }

    def get_monthly_totals(self, year: Optional[int] = None,
                           user_id: Optional[int] = None,
                           include_shared: bool = False) -> list[dict]:
        base_where, params = self._build_where("", "", year,
                                                user_id=user_id, include_shared=include_shared)
        where = base_where if base_where else "WHERE creation_time_s IS NOT NULL"
        if base_where:
            where += " AND creation_time_s IS NOT NULL"

        rows = self._con.execute(
            f"""SELECT
                    strftime('%Y-%m', datetime(creation_time_s,'unixepoch')) AS month,
                    COUNT(*)            AS count,
                    SUM(distance_mi)    AS dist_mi,
                    SUM(src_total_climb) AS climb_ft,
                    SUM(src_moving_time_s) AS moving_s
                FROM activities
                {where}
                GROUP BY month
                ORDER BY month ASC""",
            params,
        ).fetchall()

        return [
            {
                "month":    r["month"],
                "count":    r["count"],
                "dist_mi":  round(r["dist_mi"] or 0, 1),
                "climb_ft": round(r["climb_ft"] or 0),
                "active_h": round((r["moving_s"] or 0) / 3600, 1),
            }
            for r in rows
        ]

    # ── internal helpers ──────────────────────────────────────────────────

    def _build_where(self, search: str, activity_type: str, year: Optional[int],
                     user_id: Optional[int] = None, include_shared: bool = False):
        where_parts, params = [], []

        # User isolation: show own activities + optionally shared ones from others
        if user_id is not None:
            if include_shared:
                where_parts.append(
                    "(user_id = ? OR user_id IN "
                    "(SELECT id FROM users WHERE share_activities = 1 AND id != ?))"
                )
                params += [user_id, user_id]
            else:
                where_parts.append("user_id = ?")
                params.append(user_id)

        if search:
            where_parts.append(
                "(name LIKE ? OR json_extract(attributes_json,'$.name') LIKE ?"
                " OR json_extract(attributes_json,'$.notes') LIKE ?)"
            )
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]

        if activity_type:
            where_parts.append("json_extract(attributes_json,'$.activity') = ?")
            params.append(activity_type)

        if year:
            where_parts.append(
                "strftime('%Y', datetime(COALESCE(creation_time_override_s, creation_time_s),'unixepoch')) = ?"
            )
            params.append(str(year))

        clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        return clause, params

    # Valid sort columns → actual DB column names
    _SORT_MAP = {
        "start_time":      "COALESCE(creation_time_override_s, creation_time_s)",
        "creation_time_s": "COALESCE(creation_time_override_s, creation_time_s)",
        "name":            "json_extract(attributes_json,'$.name')",
        "distance_m":      "distance_mi",
        "distance_mi":     "distance_mi",
        "duration":        "src_elapsed_time_s",
        "active_time":     "src_moving_time_s",
        "total_climb_m":   "src_total_climb",
        "avg_speed_mps":   "src_max_speed",
        "avg_heartrate":   "src_avg_heartrate",
        "calories":        "json_extract(attributes_json,'$.calories')",
    }

    def _safe_order(self, sort_by: str, sort_dir: str) -> str:
        col = self._SORT_MAP.get(sort_by, "COALESCE(creation_time_override_s, creation_time_s)")
        direction = "DESC" if sort_dir.lower() == "desc" else "ASC"
        return f"{col} {direction}"

    # ── debug ─────────────────────────────────────────────────────────────


    def update_activity_attrs(self, activity_id: int, updates: dict) -> None:
        """Merge key/value pairs into attributes_json for an activity."""
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            row = con.execute(
                "SELECT attributes_json FROM activities WHERE id=?", (activity_id,)
            ).fetchone()
            if not row:
                return
            try:
                data = json.loads(row[0]) if row[0] else []
            except Exception:
                data = []
            # attributes_json is a flat alternating array: [key, val, key, val, ...]
            d = dict(zip(data[::2], data[1::2]))
            d.update(updates)
            flat = []
            for k, v in d.items():
                flat.append(k)
                flat.append(v)
            con.execute(
                "UPDATE activities SET attributes_json=? WHERE id=?",
                (json.dumps(flat), activity_id)
            )
            con.commit()
        finally:
            con.close()

    def get_last_sync_time(self) -> Optional[int]:
        """Return unix timestamp of the most recent activity in the DB."""
        row = self._con.execute(
            "SELECT MAX(COALESCE(creation_time_override_s, creation_time_s)) FROM activities"
        ).fetchone()
        return row[0] if row and row[0] else None

    def raw_tables(self) -> list[str]:

        return [r[0] for r in self._con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]

    def raw_columns(self, table: str) -> list[dict]:
        return [
            {"cid": r[0], "name": r[1], "type": r[2]}
            for r in self._con.execute(f"PRAGMA table_info('{table}')").fetchall()
        ]

    # ── user profile ──────────────────────────────────────────────────────────

    def _ensure_user_profile_table(self):
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                id         INTEGER PRIMARY KEY CHECK(id=1),
                max_hr     INTEGER,
                ftp_watts  INTEGER,
                age        INTEGER,
                weight_lb  REAL,
                use_metric INTEGER DEFAULT 0
            )
        """)
        self._con.execute(
            "INSERT OR IGNORE INTO user_profile (id) VALUES (1)"
        )
        # Migration: add use_metric column if missing (existing databases)
        try:
            self._con.execute("ALTER TABLE user_profile ADD COLUMN use_metric INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists
        self._con.commit()

    def get_user_profile(self) -> dict:
        self._ensure_user_profile_table()
        row = self._con.execute(
            "SELECT max_hr, ftp_watts, age, weight_lb, use_metric FROM user_profile WHERE id=1"
        ).fetchone()
        if row:
            return {
                "max_hr":     row[0],
                "ftp_watts":  row[1],
                "age":        row[2],
                "weight_lb":  row[3],
                "use_metric": bool(row[4]),
            }
        return {"max_hr": None, "ftp_watts": None, "age": None, "weight_lb": None, "use_metric": False}

    def set_user_profile(self, max_hr=None, ftp_watts=None, age=None, weight_lb=None, use_metric=None):
        self._ensure_user_profile_table()
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("""
                UPDATE user_profile SET
                    max_hr     = COALESCE(?, max_hr),
                    ftp_watts  = COALESCE(?, ftp_watts),
                    age        = COALESCE(?, age),
                    weight_lb  = COALESCE(?, weight_lb),
                    use_metric = CASE WHEN ? IS NOT NULL THEN ? ELSE use_metric END
                WHERE id=1
            """, (max_hr, ftp_watts, age, weight_lb, use_metric, use_metric))
            con.commit()
        finally:
            con.close()

    # ── Segments ──────────────────────────────────────────────────────────────

    def _ensure_segments_table(self):
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS segments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                activity_id INTEGER NOT NULL,
                start_idx   INTEGER NOT NULL,
                end_idx     INTEGER NOT NULL,
                length_km   REAL,
                min_lat     REAL, max_lat REAL,
                min_lon     REAL, max_lon REAL,
                points_json TEXT,
                created_at  INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        self._con.commit()

    def save_segment(self, name: str, activity_id: int, start_idx: int, end_idx: int,
                     length_km: float, min_lat: float, max_lat: float,
                     min_lon: float, max_lon: float, points_json: str) -> int:
        self._ensure_segments_table()
        cur = self._con.execute("""
            INSERT INTO segments
              (name, activity_id, start_idx, end_idx, length_km,
               min_lat, max_lat, min_lon, max_lon, points_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (name, activity_id, start_idx, end_idx, length_km,
              min_lat, max_lat, min_lon, max_lon, points_json))
        self._con.commit()
        return cur.lastrowid

    def update_segment_name(self, segment_id: int, name: str):
        self._ensure_segments_table()
        self._con.execute("UPDATE segments SET name=? WHERE id=?", (name, segment_id))
        self._con.commit()

    def get_segments_for_activity(self, activity_id: int) -> list:
        """Return segments that the activity actually traverses.
        Checks that the activity has GPS points within 200m of the segment's
        start, midpoint, and end — much more accurate than bbox overlap."""
        import json, math
        self._ensure_segments_table()

        # Quick bbox pre-filter first (cheap)
        rows = self._con.execute("""
            SELECT s.id, s.name, s.activity_id, s.start_idx, s.end_idx,
                   s.length_km, s.min_lat, s.max_lat, s.min_lon, s.max_lon,
                   s.points_json, s.created_at
            FROM segments s
            WHERE EXISTS (
                SELECT 1 FROM points p WHERE p.track_id = ?
                AND p.latitude_e7  BETWEEN s.min_lat AND s.max_lat
                AND p.longitude_e7 BETWEEN s.min_lon AND s.max_lon
            )
            ORDER BY s.name
        """, (activity_id,)).fetchall()

        if not rows:
            return []

        # Load activity points once for proximity check
        act_pts = self._con.execute(
            "SELECT latitude_e7, longitude_e7 FROM points WHERE track_id=? AND latitude_e7 != 999.0 ORDER BY wall_clock_delta_s",
            (activity_id,)
        ).fetchall()

        if not act_pts:
            return [dict(r) for r in rows]

        def min_dist_deg(lat, lon, pts):
            """Minimum squared degree distance from (lat,lon) to any point in pts."""
            cos_l = math.cos(math.radians(lat))
            best = float("inf")
            for p in pts:
                d2 = (p[0]-lat)**2 + ((p[1]-lon)*cos_l)**2
                if d2 < best:
                    best = d2
            return math.sqrt(best) * 111000  # approx metres

        tol_m = 200.0  # must be within 200m of start, mid, and end

        result = []
        for row in rows:
            seg_pts = json.loads(row["points_json"]) if row["points_json"] else []
            if len(seg_pts) < 2:
                result.append(dict(row))
                continue
            start = seg_pts[0]
            end   = seg_pts[-1]
            mid   = seg_pts[len(seg_pts)//2]
            # Check all three anchor points are close to some activity point
            if (min_dist_deg(start[0], start[1], act_pts) <= tol_m and
                min_dist_deg(mid[0],   mid[1],   act_pts) <= tol_m and
                min_dist_deg(end[0],   end[1],   act_pts) <= tol_m):
                result.append(dict(row))

        return result

    def get_segment(self, segment_id: int):
        self._ensure_segments_table()
        row = self._con.execute(
            "SELECT * FROM segments WHERE id=?", (segment_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_segment(self, segment_id: int):
        self._ensure_segments_table()
        self._con.execute("DELETE FROM segments WHERE id=?", (segment_id,))
        self._con.commit()



    # ── Users ─────────────────────────────────────────────────────────────────

    def _ensure_users_tables(self):
        """Create users and invites tables if they don't exist."""
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                email               TEXT UNIQUE NOT NULL,
                username            TEXT NOT NULL,
                password_hash       TEXT,
                strava_athlete_id   TEXT UNIQUE,
                strava_tokens_json  TEXT,
                anthropic_api_key      TEXT,
                strava_client_id       TEXT,
                strava_client_secret   TEXT,
                is_admin               INTEGER NOT NULL DEFAULT 0,
                share_activities    INTEGER NOT NULL DEFAULT 0,
                share_segments      INTEGER NOT NULL DEFAULT 0,
                created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                invited_by          INTEGER
            );

            CREATE TABLE IF NOT EXISTS invites (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                token               TEXT UNIQUE NOT NULL,
                email               TEXT,
                invited_by_user_id  INTEGER,
                created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                used_at             INTEGER,
                used_by_user_id     INTEGER
            );
        """)
        # Add per-user key columns if missing (migration)
        for col in ("anthropic_api_key TEXT", "strava_client_id TEXT", "strava_client_secret TEXT"):
            try:
                self._con.execute(f"ALTER TABLE users ADD COLUMN {col}")
                self._con.commit()
            except Exception:
                pass  # column already exists

        self._con.commit()

    def get_user(self, user_id: int) -> Optional[dict]:
        self._ensure_users_tables()
        row = self._con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[dict]:
        self._ensure_users_tables()
        row = self._con.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
        return dict(row) if row else None

    def get_user_by_strava_athlete_id(self, athlete_id: str) -> Optional[dict]:
        self._ensure_users_tables()
        row = self._con.execute(
            "SELECT * FROM users WHERE strava_athlete_id=?", (str(athlete_id),)
        ).fetchone()
        return dict(row) if row else None

    def create_user(self, email: str, username: str, password_hash: str = None,
                    strava_athlete_id: str = None, invited_by: int = None,
                    is_admin: bool = False) -> int:
        self._ensure_users_tables()
        import time
        cur = self._con.execute("""
            INSERT INTO users (email, username, password_hash, strava_athlete_id,
                               invited_by, is_admin, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (email.lower(), username, password_hash, strava_athlete_id,
              invited_by, 1 if is_admin else 0, int(time.time())))
        self._con.commit()
        return cur.lastrowid

    def update_user_strava_tokens(self, user_id: int, tokens: dict):
        import json as json_mod
        self._ensure_users_tables()
        athlete    = tokens.get("athlete", {})
        athlete_id = str(athlete.get("id", "")) if athlete else None
        self._con.execute("""
            UPDATE users SET strava_tokens_json=?, strava_athlete_id=COALESCE(strava_athlete_id,?)
            WHERE id=?
        """, (json_mod.dumps(tokens), athlete_id, user_id))
        self._con.commit()

    def get_user_strava_tokens(self, user_id: int) -> dict:
        import json as json_mod
        self._ensure_users_tables()
        row = self._con.execute(
            "SELECT strava_tokens_json FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if row and row[0]:
            try: return json_mod.loads(row[0])
            except Exception: pass
        return {}

    def update_user_settings(self, user_id: int, **kwargs):
        """Update user settings fields. Allowed: share_activities, share_segments, username."""
        allowed = {"share_activities", "share_segments", "username",
                   "anthropic_api_key", "strava_client_id", "strava_client_secret"}
        fields  = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields: return
        sets = ", ".join(f"{k}=?" for k in fields)
        self._con.execute(f"UPDATE users SET {sets} WHERE id=?",
                          list(fields.values()) + [user_id])
        self._con.commit()

    def list_users(self) -> list:
        self._ensure_users_tables()
        rows = self._con.execute(
            "SELECT id, email, username, is_admin, share_activities, share_segments, "
            "created_at, invited_by, strava_athlete_id FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Invites ───────────────────────────────────────────────────────────────

    def create_invite(self, token: str, email: str = "", invited_by_user_id: int = None):
        import time
        self._ensure_users_tables()
        self._con.execute("""
            INSERT INTO invites (token, email, invited_by_user_id, created_at)
            VALUES (?,?,?,?)
        """, (token, email.lower(), invited_by_user_id, int(time.time())))
        self._con.commit()

    def get_invite(self, token: str) -> Optional[dict]:
        self._ensure_users_tables()
        row = self._con.execute("SELECT * FROM invites WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None

    def mark_invite_used(self, token: str, used_by_user_id: int):
        import time
        self._con.execute("""
            UPDATE invites SET used_at=?, used_by_user_id=? WHERE token=?
        """, (int(time.time()), used_by_user_id, token))
        self._con.commit()

    def delete_invite(self, token: str):
        self._con.execute("DELETE FROM invites WHERE token=?", (token,))
        self._con.commit()

    def list_invites(self) -> list:
        self._ensure_users_tables()
        rows = self._con.execute(
            "SELECT * FROM invites ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def ensure_seed_admin(self, email: str, username: str = "Admin") -> int:
        """Create a seed admin user if no users exist yet. Returns user_id."""
        self._ensure_users_tables()
        count = self._con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            user = self.get_user_by_email(email)
            return user["id"] if user else 1
        user_id = self.create_user(
            email=email, username=username, is_admin=True
        )
        return user_id



