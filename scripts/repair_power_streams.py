#!/usr/bin/env python3
"""Re-fetch per-second streams for activities whose power/cadence/temp were lost.

WHY
    Three code paths in api.py used to fetch Strava streams with their own
    hand-written key list, and the PR-check path omitted watts, cadence, temp
    and moving. store_points() sets points_saved=1, so whichever path touched an
    activity first won permanently: rides backfilled by the PR check kept a
    stream with power, cadence and temperature zeroed out, and nothing ever
    re-fetched them. The activity's own avg/max power stayed correct, so the
    damage only showed up in anything computed from the points table — most
    visibly the AI Coach's power-zone distribution.

    The key list is now a single shared constant (strava_importer.STRAVA_STREAM_KEYS),
    so no new activity can be damaged. This script repairs the ones already stored.

WHAT IT DOES
    Finds activities that have a summary power value but no point carrying
    power, then re-fetches their streams from Strava with the full key set.
    store_points() replaces the activity's points, so this is idempotent.

USAGE
    python3 scripts/repair_power_streams.py                  # dry run, all users
    python3 scripts/repair_power_streams.py --user 1         # dry run, one user
    python3 scripts/repair_power_streams.py --user 1 --apply # actually re-fetch

    Strava allows 100 requests per 15 minutes / 1000 per day. The script paces
    itself and stops cleanly on a rate-limit response so it can be re-run.
"""
import argparse
import asyncio
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import AscentDB                                    # noqa: E402
from app.strava_importer import build_points_rows, STRAVA_STREAM_KEYS  # noqa: E402
from app.routers import strava as strava_router                # noqa: E402
from app.routers.strava import refresh_tokens, tokens_are_fresh        # noqa: E402

FIND_SQL = """
    SELECT a.id, a.user_id, a.strava_activity_id,
           date(datetime(COALESCE(a.creation_time_override_s, a.creation_time_s),
                         'unixepoch')) AS d,
           a.src_avg_power
    FROM activities a
    WHERE a.src_avg_power > 0
      AND a.strava_activity_id IS NOT NULL
      AND a.points_saved = 1
      AND NOT EXISTS (SELECT 1 FROM points p
                      WHERE p.track_id = a.id AND p.power_w > 0)
      {user_filter}
    ORDER BY COALESCE(a.creation_time_override_s, a.creation_time_s) DESC
"""


def find_damaged(db, user_id=None):
    con = sqlite3.connect(f"file:{db.path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        sql = FIND_SQL.format(user_filter="AND a.user_id = ?" if user_id else "")
        return con.execute(sql, (user_id,) if user_id else ()).fetchall()
    finally:
        con.close()


async def repair(db, rows, limit):
    import httpx

    by_user = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)

    fixed = failed = 0
    for uid, acts in by_user.items():
        tokens = db.get_user_strava_tokens(uid)
        if not tokens:
            print(f"  user {uid}: no Strava tokens — skipping {len(acts)} activities")
            continue
        if not tokens_are_fresh(tokens):
            tokens = await refresh_tokens(tokens, user_id=uid)

        for r in acts:
            if fixed + failed >= limit:
                print(f"\nReached --limit {limit}; re-run to continue.")
                return fixed, failed
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(
                        f"https://www.strava.com/api/v3/activities/"
                        f"{r['strava_activity_id']}/streams",
                        headers={"Authorization": f"Bearer {tokens['access_token']}"},
                        params={"keys": STRAVA_STREAM_KEYS, "key_by_type": "true"},
                    )
                if resp.status_code == 429:
                    print("\nStrava rate limit hit — stopping. Re-run later to continue.")
                    return fixed, failed
                if resp.status_code != 200:
                    print(f"  ✗ {r['d']} id={r['id']}: HTTP {resp.status_code}")
                    failed += 1
                    continue

                points = build_points_rows(resp.json(), r["id"])
                if not points:
                    print(f"  ✗ {r['d']} id={r['id']}: no stream data returned")
                    failed += 1
                    continue

                db.store_points(r["id"], points)
                n_pw = sum(1 for p in db.get_track_points(r["id"]) if (p["power"] or 0) > 0)
                if n_pw:
                    print(f"  ✓ {r['d']} id={r['id']}: {n_pw} points now carry power")
                    fixed += 1
                else:
                    # Strava genuinely has no power for this ride.
                    print(f"  – {r['d']} id={r['id']}: Strava returned no watts stream")
                    failed += 1
            except Exception as e:
                print(f"  ✗ {r['d']} id={r['id']}: {e}")
                failed += 1
            time.sleep(0.4)          # stay well inside 100 req / 15 min
    return fixed, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", type=int, help="Only repair this user's activities")
    ap.add_argument("--apply", action="store_true",
                    help="Actually re-fetch (default is a dry run)")
    ap.add_argument("--limit", type=int, default=200,
                    help="Max activities to re-fetch in one run (default 200)")
    args = ap.parse_args()

    db_path = os.environ.get("ASCENT_DB_PATH", "")
    if not db_path:
        sys.exit("ASCENT_DB_PATH is not set (add it to .env)")
    db = AscentDB(db_path)

    # main.py normally wires this; running standalone we must do it ourselves or
    # the Strava router cannot read per-user client credentials and token refresh
    # fails with a 400 from /oauth/token.
    strava_router.db_getter = lambda: db

    rows = find_damaged(db, args.user)
    if not rows:
        print("No damaged activities found — nothing to repair.")
        return

    per_user = {}
    for r in rows:
        per_user[r["user_id"]] = per_user.get(r["user_id"], 0) + 1
    print(f"{len(rows)} activities have summary power but no power in their stream:")
    for uid, n in sorted(per_user.items(), key=lambda kv: -kv[1]):
        print(f"   user {uid}: {n}")
    print(f"\nOldest: {rows[-1]['d']}   Newest: {rows[0]['d']}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to re-fetch these streams from Strava.")
        return

    print(f"\nRe-fetching (limit {args.limit})…")
    fixed, failed = asyncio.run(repair(db, rows, args.limit))
    print(f"\nRepaired {fixed}, failed/skipped {failed}.")
    if fixed:
        print("Zone distributions and the AI Coach will pick this up immediately.")


if __name__ == "__main__":
    main()
