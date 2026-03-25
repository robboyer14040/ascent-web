#!/usr/bin/env python3
"""
migrate_step2.py — Add user_id to activities and segments tables.
Backfills all existing rows to the first admin user.

Safe to run multiple times.

Usage:
    ASCENT_DB_PATH=/path/to/Ascent.ascentdb python3 scripts/migrate_step2.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

db_path = os.environ.get("ASCENT_DB_PATH", "")
if not db_path:
    print("ERROR: ASCENT_DB_PATH not set"); sys.exit(1)

import sqlite3
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row

# ── 1. Find the admin user ────────────────────────────────────────────────────
users = con.execute("SELECT id, username, email, is_admin FROM users ORDER BY id").fetchall()
if not users:
    print("ERROR: No users found. Run migrate_add_users.py first."); sys.exit(1)

admin = next((u for u in users if u["is_admin"]), users[0])
admin_id = admin["id"]
print(f"Using user: {admin['username']} <{admin['email']}> (id={admin_id})")

# ── 2. Add user_id to activities if not present ───────────────────────────────
cols = [r[1] for r in con.execute("PRAGMA table_info(activities)").fetchall()]
if "user_id" not in cols:
    con.execute("ALTER TABLE activities ADD COLUMN user_id INTEGER")
    print("✓ Added user_id column to activities")
else:
    print("  activities.user_id already exists")

# ── 3. Add user_id to segments if not present ────────────────────────────────
seg_cols = [r[1] for r in con.execute("PRAGMA table_info(segments)").fetchall()]
if seg_cols and "user_id" not in seg_cols:
    con.execute("ALTER TABLE segments ADD COLUMN user_id INTEGER")
    print("✓ Added user_id column to segments")
elif not seg_cols:
    print("  segments table not found (will be created on first use)")
else:
    print("  segments.user_id already exists")

# ── 4. Backfill NULL user_id rows to admin ────────────────────────────────────
n_acts = con.execute(
    "UPDATE activities SET user_id=? WHERE user_id IS NULL", (admin_id,)
).rowcount
print(f"✓ Backfilled {n_acts} activities → user_id={admin_id}")

if seg_cols:
    n_segs = con.execute(
        "UPDATE segments SET user_id=? WHERE user_id IS NULL", (admin_id,)
    ).rowcount
    print(f"✓ Backfilled {n_segs} segments → user_id={admin_id}")

con.commit()

# ── 5. Migrate strava_tokens.json → users table ───────────────────────────────
import json
from pathlib import Path
token_file = Path(db_path).parent / "strava_tokens.json"
if token_file.exists():
    try:
        tokens = json.loads(token_file.read_text())
        if tokens.get("access_token"):
            athlete = tokens.get("athlete", {})
            athlete_id = str(athlete.get("id", "")) if athlete else None
            con.execute("""
                UPDATE users SET strava_tokens_json=?,
                    strava_athlete_id=COALESCE(strava_athlete_id,?)
                WHERE id=?
            """, (json.dumps(tokens), athlete_id, admin_id))
            con.commit()
            print(f"✓ Migrated strava_tokens.json → users table (id={admin_id})")
    except Exception as e:
        print(f"  Warning: could not migrate strava tokens: {e}")
else:
    print("  No strava_tokens.json found (skipping)")

# ── 6. Create indexes for performance ─────────────────────────────────────────
con.execute("CREATE INDEX IF NOT EXISTS idx_activities_user_id ON activities(user_id)")
con.execute("CREATE INDEX IF NOT EXISTS idx_activities_user_bbox ON activities(user_id, map_min_lat, map_max_lat, map_min_lon, map_max_lon)")
con.commit()
print("✓ Created indexes")

con.close()
print("\nStep 2 migration complete!")
print("Restart the server to apply changes.")
