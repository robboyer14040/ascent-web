"""
create_db.py — Create a fresh Ascent SQLite database.

Column units (matching macOS Ascent app exactly, per TrackPointStore.m):
  points.latitude_e7      → degrees (not ×1e7, column name is misleading)
  points.longitude_e7     → degrees
  points.orig_altitude_cm → feet (not cm, column name is misleading)
  points.orig_distance_m  → miles (not metres, column name is misleading)
  points.temperature_c10  → tenths of °F (not °C, column name is misleading)
  points.speed_mps        → m/s (this one IS correct)
  
  activities.distance_mi  → miles ✓
  activities.src_*        → statute units matching above
"""

import sqlite3
import uuid


CREATE_ACTIVITIES = """
CREATE TABLE IF NOT EXISTS activities (
    id                       INTEGER PRIMARY KEY,
    uuid                     TEXT UNIQUE NOT NULL,
    name                     TEXT,
    creation_time_s          INTEGER,
    creation_time_override_s INTEGER,
    distance_mi              REAL,
    weight_lb                REAL,
    altitude_smooth_factor   REAL,
    equipment_weight_lb      REAL,
    device_total_time_s      REAL,
    moving_speed_only        INTEGER,
    has_distance_data        INTEGER,
    attributes_json          TEXT,
    markers_json             TEXT,
    override_json            TEXT,
    seconds_from_gmt_at_sync INTEGER,
    time_zone                TEXT,
    flags                    INTEGER,
    device_id                INTEGER,
    firmware_version         INTEGER,
    photo_urls_json          TEXT,
    strava_activity_id       INTEGER,
    src_distance             REAL,
    src_max_speed            REAL,
    src_avg_heartrate        REAL,
    src_max_heartrate        REAL,
    src_avg_temperature      REAL,
    src_max_elevation        REAL,
    src_min_elevation        REAL,
    src_avg_power            REAL,
    src_max_power            REAL,
    src_avg_cadence          REAL,
    src_total_climb          REAL,
    src_kilojoules           REAL,
    src_elapsed_time_s       REAL,
    src_moving_time_s        REAL,
    local_media_items_json   TEXT,
    points_saved             INTEGER DEFAULT 0,
    points_count             INTEGER DEFAULT 0
);
"""

CREATE_POINTS = """
CREATE TABLE IF NOT EXISTS points (
    id                  INTEGER PRIMARY KEY,
    track_id            INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    wall_clock_delta_s  INTEGER NOT NULL,
    active_time_delta_s INTEGER NOT NULL DEFAULT 0,
    latitude_e7         REAL NOT NULL,      -- degrees (column name misleading)
    longitude_e7        REAL NOT NULL,      -- degrees (column name misleading)
    orig_altitude_cm    REAL DEFAULT 0,     -- FEET (column name misleading)
    heartrate_bpm       REAL DEFAULT 0,
    cadence_rpm         REAL DEFAULT 0,
    temperature_c10     REAL DEFAULT 0,     -- tenths of °F (column name misleading)
    speed_mps           REAL DEFAULT 0,     -- m/s (correct)
    power_w             REAL DEFAULT 0,
    orig_distance_m     REAL DEFAULT 0,     -- MILES (column name misleading)
    flags               INTEGER DEFAULT 0
);
"""

CREATE_LAPS = """
CREATE TABLE IF NOT EXISTS laps (
    id                  INTEGER PRIMARY KEY,
    track_id            INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    lap_index           INTEGER,
    orig_start_time_s   INTEGER,
    start_time_delta_s  REAL,
    total_time_s        REAL,
    distance_mi         REAL,
    max_speed_mph       REAL,
    avg_speed_mph       REAL,
    begin_lat           REAL,
    begin_lon           REAL,
    end_lat             REAL,
    end_lon             REAL,
    device_total_time_s REAL,
    average_hr          INTEGER,
    max_hr              INTEGER,
    average_cad         INTEGER,
    max_cad             INTEGER,
    calories            INTEGER,
    intensity           INTEGER,
    trigger_method      INTEGER,
    selected            INTEGER,
    stats_calculated    INTEGER
);
"""

CREATE_META = """
CREATE TABLE IF NOT EXISTS meta (
    id               INTEGER PRIMARY KEY CHECK(id=1),
    uuid_s           TEXT,
    tableInfo_json   TEXT,
    splitsTableInfo_json TEXT,
    startDate_s      INTEGER,
    endDate_s        INTEGER,
    lastSyncTime_s   INTEGER,
    flags            INTEGER,
    totalTracks      INTEGER,
    int3             INTEGER,
    int4             INTEGER
);
"""

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_uuid ON activities(uuid);",
    "CREATE INDEX IF NOT EXISTS idx_activities_ct ON activities(creation_time_s);",
    "CREATE INDEX IF NOT EXISTS idx_laps_track ON laps(track_id, lap_index);",
    "CREATE INDEX IF NOT EXISTS i_points_track_time ON points(track_id, wall_clock_delta_s, active_time_delta_s);",
]


def create_db(path: str) -> None:
    """Create a fresh Ascent database at the given path."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")

    con.execute(CREATE_ACTIVITIES)
    con.execute(CREATE_POINTS)
    con.execute(CREATE_LAPS)
    con.execute(CREATE_META)

    for idx in INDEXES:
        con.execute(idx)

    # Insert the single meta row
    con.execute("INSERT OR IGNORE INTO meta (id, uuid_s, totalTracks) VALUES (1, ?, 0)",
                (str(uuid.uuid4()).upper(),))

    con.commit()
    con.close()
    print(f"Created fresh Ascent database: {path}")


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "ascent_new.ascentdb"
    create_db(path)
