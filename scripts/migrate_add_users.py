#!/usr/bin/env python3
"""
migrate_add_users.py — One-time migration to add users/invites tables
and create a seed admin account linked to the existing data.

Usage:
    ASCENT_DB_PATH=/path/to/Ascent.ascentdb python3 scripts/migrate_add_users.py

This is safe to run multiple times — it won't overwrite existing data.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

db_path = os.environ.get("ASCENT_DB_PATH", "")
if not db_path:
    print("ERROR: ASCENT_DB_PATH not set")
    sys.exit(1)

from app.db import AscentDB
db = AscentDB(db_path)

# Create users/invites tables (idempotent)
db._ensure_users_tables()

import sqlite3
count = db._con.execute("SELECT COUNT(*) FROM users").fetchone()[0]

if count == 0:
    print("No users found. Creating seed admin account.")
    email    = input("Admin email: ").strip().lower()
    username = input("Admin username (e.g. Rob): ").strip()
    password = input("Admin password (min 8 chars): ").strip()

    if len(password) < 8:
        print("ERROR: Password too short")
        sys.exit(1)

    from app.auth import hash_password
    user_id = db.create_user(
        email=email,
        username=username,
        password_hash=hash_password(password),
        is_admin=True,
    )
    print(f"✓ Created admin user: {username} <{email}> (id={user_id})")

    # Try to link existing strava_tokens.json
    from pathlib import Path
    import json
    token_file = Path(db_path).parent / "strava_tokens.json"
    if token_file.exists():
        try:
            tokens = json.loads(token_file.read_text())
            athlete = tokens.get("athlete", {})
            athlete_id = str(athlete.get("id", "")) if athlete else ""
            db._con.execute(
                "UPDATE users SET strava_tokens_json=?, strava_athlete_id=? WHERE id=?",
                (json.dumps(tokens), athlete_id or None, user_id)
            )
            db._con.commit()
            print(f"✓ Linked existing Strava tokens (athlete: {athlete.get('firstname','')} {athlete.get('lastname','')})")
        except Exception as e:
            print(f"  (Could not link Strava tokens: {e})")
else:
    print(f"Found {count} existing user(s) — skipping seed creation.")
    users = db.list_users()
    for u in users:
        admin = " [ADMIN]" if u["is_admin"] else ""
        print(f"  id={u['id']} {u['username']} <{u['email']}>{admin}")

print("\nMigration complete.")
print("Tables: users ✓, invites ✓")
print("\nNext: restart the server and visit http://localhost:8000/login")
print("      Then go to http://localhost:8000/admin/invites to invite friends.")
