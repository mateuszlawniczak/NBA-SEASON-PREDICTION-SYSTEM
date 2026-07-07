import sqlite3
from config import DB_PATH, EXISTING_SEASONS

con = sqlite3.connect(DB_PATH)
cur = con.cursor()

# Reproduce the EXACT archive scalar subquery (NOT team-matched) and compare
# to the stored off_reb for existing seasons.
ph = ",".join("?" for _ in EXISTING_SEASONS)
cur.execute(
    f"""
    SELECT psa.season,
           COUNT(*) AS n,
           SUM(CASE WHEN psa.off_reb IS NOT NULL AND rq.reb IS NOT NULL
                     AND ABS(psa.off_reb - ROUND(rq.reb - psa.def_reb, 1)) <= 0.1001
                    THEN 1 ELSE 0 END) AS match_pt1,
           SUM(CASE WHEN psa.off_reb IS NOT NULL AND rq.reb IS NOT NULL
                     AND ABS(psa.off_reb - ROUND(rq.reb - psa.def_reb, 1)) <= 0.2001
                    THEN 1 ELSE 0 END) AS match_pt2,
           ROUND(AVG(CASE WHEN psa.off_reb IS NOT NULL AND rq.reb IS NOT NULL
                    THEN ABS(psa.off_reb - ROUND(rq.reb - psa.def_reb, 1)) END), 4) AS mean_abs_diff,
           MAX(CASE WHEN psa.off_reb IS NOT NULL AND rq.reb IS NOT NULL
                    THEN ABS(psa.off_reb - ROUND(rq.reb - psa.def_reb, 1)) END) AS max_abs_diff
      FROM player_stats_advanced psa
      LEFT JOIN (
           SELECT player_id, season, reb FROM player_stats_basic
      ) rq ON rq.player_id = psa.player_id AND rq.season = psa.season
     WHERE psa.season IN ({ph})
     GROUP BY psa.season
     ORDER BY psa.season
    """,
    EXISTING_SEASONS,
)
print("Existing off_reb vs archive scalar-subquery reb-def_reb (season-level, arbitrary team like archive):")
print(f"  {'season':<10}{'n':>6}{'m<=.1':>8}{'m<=.2':>8}{'meanAbs':>10}{'maxAbs':>8}")
for row in cur.fetchall():
    s, n, m1, m2, mean, mx = row
    print(f"  {s:<10}{n:>6}{m1:>8}{m2:>8}{str(mean):>10}{str(mx):>8}")

# How many traded players (multiple team rows) per existing season?
cur.execute(
    f"""
    SELECT season, COUNT(*) FROM (
        SELECT season, player_id, COUNT(*) c FROM player_stats_advanced
        WHERE season IN ({ph}) GROUP BY season, player_id HAVING c > 1
    ) GROUP BY season ORDER BY season
    """,
    EXISTING_SEASONS,
)
print("\nTraded players (multi-team rows) per existing season:")
for s, c in cur.fetchall():
    print(f"  {s}: {c}")

con.close()
