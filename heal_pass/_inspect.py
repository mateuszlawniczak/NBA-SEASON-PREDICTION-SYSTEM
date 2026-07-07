import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "nba_data.db")
con = sqlite3.connect(DB_PATH)
cur = con.cursor()

print("=== TABLES ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
for t in tables:
    print(" ", t)

for t in ["player_stats_basic", "player_stats_advanced"]:
    print(f"\n=== SCHEMA {t} ===")
    for r in cur.execute(f"PRAGMA table_info({t})"):
        print(f"  {r[1]:<32} {r[2]}")

print("\n=== SEASONS per table (that have a 'season' col) ===")
for t in tables:
    cols = {r[1] for r in cur.execute(f"PRAGMA table_info({t})")}
    if "season" in cols:
        cur.execute(f"SELECT season, COUNT(*) FROM {t} GROUP BY season ORDER BY season")
        rows = cur.fetchall()
        print(f"\n  {t}:")
        for s, c in rows:
            print(f"    {s}: {c}")

con.close()
