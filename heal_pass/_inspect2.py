import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "nba_data.db")
con = sqlite3.connect(DB_PATH)
cur = con.cursor()

for t in ["player_stats_basic_playoffs", "player_stats_advanced_playoffs"]:
    print(f"\n=== SCHEMA {t} ===")
    for r in cur.execute(f"PRAGMA table_info({t})"):
        print(f"  {r[1]:<32} {r[2]}")

con.close()
