#!/usr/bin/env python3
"""
delete_ai_summaries_for_user.py — Delete all AI summaries for a given username.

Usage:
    ASCENT_DB_PATH=/path/to/Ascent.ascentdb python3 scripts/delete_ai_summaries_for_user.py Kelly
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

if len(sys.argv) < 2:
    print("Usage: python3 scripts/delete_ai_summaries_for_user.py <username>")
    sys.exit(1)

username = sys.argv[1]

db_path = os.environ.get("ASCENT_DB_PATH", "")
if not db_path:
    print("ERROR: ASCENT_DB_PATH not set"); sys.exit(1)

import sqlite3
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row

user = con.execute(
    "SELECT id, username FROM users WHERE username = ? COLLATE NOCASE",
    (username,)
).fetchone()
if not user:
    print(f"ERROR: No user found with username '{username}'"); sys.exit(1)

user_id = user["id"]
print(f"Found user: {user['username']} (id={user_id})")

rows = con.execute(
    """SELECT s.activity_id FROM activity_ai_summaries s
       JOIN activities a ON s.activity_id = a.id
       WHERE a.user_id = ?""",
    (user_id,)
).fetchall()

count = len(rows)
if count == 0:
    print("No AI summaries found for this user.")
    con.close()
    sys.exit(0)

print(f"Found {count} AI summary/summaries to delete.")
confirm = input("Delete them? [y/N] ").strip().lower()
if confirm != "y":
    print("Aborted."); con.close(); sys.exit(0)

cur = con.execute(
    """DELETE FROM activity_ai_summaries WHERE activity_id IN (
       SELECT a.id FROM activities a WHERE a.user_id = ?
    )""",
    (user_id,)
)
con.commit()
print(f"Deleted {cur.rowcount} AI summary/summaries for '{user['username']}'.")
con.close()
