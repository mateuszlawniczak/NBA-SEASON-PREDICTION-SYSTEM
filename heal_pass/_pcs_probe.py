import sqlite3
from nba_api.stats.endpoints import PlayerCareerStats
from config import DB_PATH, NEW_SEASONS

con = sqlite3.connect(DB_PATH)
cur = con.cursor()
ph = ",".join("?" for _ in NEW_SEASONS)
cur.execute(f"SELECT DISTINCT player_id, player_name FROM player_stats_basic WHERE season IN ({ph}) AND gs IS NULL", NEW_SEASONS)
players = cur.fetchall()
con.close()
print("players with NULL gs:", [(p[0], p[1]) for p in players])

# probe PCS on the first one
if players:
    pid, name = players[0]
    df = PlayerCareerStats(player_id=pid, timeout=60).get_data_frames()[0]
    print(f"PCS probe {name} ({pid}): rows={len(df)}")
