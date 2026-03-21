#!/usr/bin/env python3
"""
Run this against your Ascent .db file to print the full schema.
Usage: python inspect_db.py /path/to/your.db
"""
import sqlite3, sys, json

def inspect(path):
    con = sqlite3.connect(path)
    cur = con.cursor()

    # All tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    print(f"\n=== Tables ({len(tables)}) ===")
    for t in tables:
        print(f"  {t}")

    # Schema + row count for each
    schema = {}
    for t in tables:
        cur.execute(f"PRAGMA table_info('{t}')")
        cols = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) FROM '{t}'")
        count = cur.fetchone()[0]
        schema[t] = {"columns": cols, "row_count": count}
        print(f"\n--- {t} ({count} rows) ---")
        for col in cols:
            print(f"  {col[1]:30s} {col[2]}")

    # Sample rows from key tables
    key_tables = [t for t in tables if any(k in t.lower() for k in
                  ['track','activity','lap','point','meta','pref','config'])]
    print(f"\n=== Sample rows from key tables ===")
    for t in key_tables[:8]:
        try:
            cur.execute(f"SELECT * FROM '{t}' LIMIT 2")
            rows = cur.fetchall()
            if rows:
                print(f"\n-- {t} --")
                col_names = [d[0] for d in cur.description]
                print("  Columns:", col_names)
                for r in rows:
                    print("  Row:", dict(zip(col_names, r)))
        except Exception as e:
            print(f"  (error reading {t}: {e})")

    con.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inspect_db.py /path/to/ascent.db")
        sys.exit(1)
    inspect(sys.argv[1])
