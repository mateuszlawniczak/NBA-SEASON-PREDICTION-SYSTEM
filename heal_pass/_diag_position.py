import sqlite3
from config import DB_PATH, EXISTING_SEASONS

con = sqlite3.connect(DB_PATH)
cur = con.cursor()
ph = ",".join("?" for _ in EXISTING_SEASONS)

# Distinct position values in existing basic
cur.execute(f"SELECT DISTINCT position FROM player_stats_basic WHERE season IN ({ph}) ORDER BY position", EXISTING_SEASONS)
print("Distinct basic.position (existing):", [r[0] for r in cur.fetchall()])

# Does a player's position stay constant across seasons in existing basic?
cur.execute(
    f"""
    SELECT COUNT(*) FROM (
      SELECT player_id, COUNT(DISTINCT position) d
      FROM player_stats_basic WHERE season IN ({ph}) AND position IS NOT NULL
      GROUP BY player_id HAVING d > 1
    )
    """,
    EXISTING_SEASONS,
)
print("Players with >1 distinct position across existing seasons:", cur.fetchone()[0])

# Advanced position == basic position for same (player_id, season)?
cur.execute(
    f"""
    SELECT COUNT(*) tot,
           SUM(CASE WHEN a.position = b.position THEN 1 ELSE 0 END) same
    FROM player_stats_advanced a
    JOIN player_stats_basic b ON a.player_id=b.player_id AND a.season=b.season
    WHERE a.season IN ({ph})
    """,
    EXISTING_SEASONS,
)
print("Advanced vs basic position match (existing):", cur.fetchone())

# Playoff basic position vs regular basic position
cur.execute(
    f"""
    SELECT COUNT(*) tot,
           SUM(CASE WHEN pb.position = b.position THEN 1 ELSE 0 END) same,
           SUM(CASE WHEN b.player_id IS NULL THEN 1 ELSE 0 END) no_regular_row
    FROM player_stats_basic_playoffs pb
    LEFT JOIN player_stats_basic b ON pb.player_id=b.player_id AND pb.season=b.season
    WHERE pb.season IN ({ph})
    """,
    EXISTING_SEASONS,
)
print("Playoff-basic vs regular-basic position match (existing) [tot, same, no_regular_row]:", cur.fetchone())

con.close()
