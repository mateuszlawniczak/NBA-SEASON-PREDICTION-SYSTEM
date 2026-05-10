import sqlite3

DB_PATH = "nba_data.db"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("DELETE FROM player_stats_advanced WHERE season = '2019-20'")
conn.commit()

print(f"Deleted {cursor.rowcount} rows from player_stats_advanced where season = '2019-20'.")

conn.close()
