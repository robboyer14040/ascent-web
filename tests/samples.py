"""Captured-shape sample payloads for the external APIs, so tests don't hit the
network. Shapes mirror what Strava and the Anthropic Messages API actually
return today; trim/extend as the real responses evolve."""

# ── Strava: a summary activity (GET /athlete/activities item) ─────────────────
# summary_polyline is Google's canonical example string; decodes to three points
# spanning ~lat 38.5–43.25, lon -126.45–-120.20.
STRAVA_SUMMARY_RIDE = {
    "id": 12345678901,
    "name": "Morning Ride",
    "distance": 40234.5,              # metres  → ~25.00 mi
    "moving_time": 5400,
    "elapsed_time": 5700,
    "total_elevation_gain": 320.0,    # metres  → ~1049.9 ft
    "type": "Ride",
    "sport_type": "Ride",
    "workout_type": None,
    "start_date": "2026-06-15T13:30:00Z",
    "timezone": "(GMT-08:00) America/Los_Angeles",
    "utc_offset": -25200.0,
    "start_latlng": [37.77, -122.42],
    "average_speed": 7.45,            # m/s
    "max_speed": 13.4,
    "average_heartrate": 148.0,
    "max_heartrate": 176.0,
    "average_cadence": 84.0,
    "average_watts": 195.0,
    "weighted_average_watts": 210.0,  # preferred over average_watts
    "max_watts": 620.0,
    "kilojoules": 1053.0,
    "average_temp": 18.0,             # °C → 64.4 °F
    "elev_high": 210.0,
    "elev_low": 12.0,
    "calories": 980.0,
    "suffer_score": 72,
    "location_city": "San Francisco",
    "location_country": "United States",
    "visibility": "everyone",
    "map": {"summary_polyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
}

# ── Strava: activity streams (GET /activities/{id}/streams) ────────────────────
STRAVA_STREAMS = {
    "time":            {"data": [0, 5, 10, 15]},
    "latlng":          {"data": [[37.770, -122.42], [37.771, -122.42],
                                 [37.772, -122.42], [37.773, -122.42]]},
    "distance":        {"data": [0.0, 50.0, 100.0, 150.0]},   # metres
    "altitude":        {"data": [12.0, 15.0, 20.0, 18.0]},    # metres
    "heartrate":       {"data": [120, 130, 140, 145]},
    "velocity_smooth": {"data": [0.0, 5.0, 5.5, 5.2]},        # m/s
    "watts":           {"data": [100, 200, 210, 190]},
    "cadence":         {"data": [80, 85, 88, 84]},
    "temp":            {"data": [18, 18, 19, 19]},            # °C
    "moving":          {"data": [False, True, True, True]},
}

# ── Anthropic: POST /v1/messages 200 body ─────────────────────────────────────
ANTHROPIC_MESSAGE_RESPONSE = {
    "id": "msg_01ABC",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [
        {"type": "text",
         "text": "Nice work this week — your volume is trending up. "
                 "Focus on recovery before your next hard effort."}
    ],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1234, "output_tokens": 56},
}
