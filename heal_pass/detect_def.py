"""Detection for Fix 5: which open_shot_pct definition do the EXISTING seasons hold?

  * fix_offensive_tracking recipe: open = 1 - contested  => contested + open == 1.0
  * patch_open_shots recipe:       open = wide_open FGA / total season FGA
                                   => contested + open does NOT sum to 1.

Reports the distribution of (contested_shot_pct + open_shot_pct) for existing
seasons, plus the same for the new seasons' current (pre-fix) values.
"""
import sqlite3
from config import DB_PATH, EXISTING_SEASONS, NEW_SEASONS


def report(cur, seasons, label):
    ph = ",".join("?" for _ in seasons)
    print(f"\n=== {label}: distribution of (contested_shot_pct + open_shot_pct) ===")
    print("  (rows where BOTH are non-null)")
    cur.execute(
        f"""
        SELECT
          COUNT(*) AS both_nonnull,
          SUM(CASE WHEN ABS((contested_shot_pct+open_shot_pct)-1.0) <= 0.005 THEN 1 ELSE 0 END) AS near_1,
          SUM(CASE WHEN ABS((contested_shot_pct+open_shot_pct)-1.0) <= 0.02  THEN 1 ELSE 0 END) AS near_1_loose,
          ROUND(AVG(contested_shot_pct+open_shot_pct),4) AS avg_sum,
          ROUND(MIN(contested_shot_pct+open_shot_pct),4) AS min_sum,
          ROUND(MAX(contested_shot_pct+open_shot_pct),4) AS max_sum
        FROM player_stats_advanced
        WHERE season IN ({ph})
          AND contested_shot_pct IS NOT NULL AND open_shot_pct IS NOT NULL
        """,
        seasons,
    )
    r = cur.fetchone()
    print(f"  both_nonnull={r[0]}  sum~=1.0(+/-0.005)={r[1]}  sum~=1.0(+/-0.02)={r[2]}")
    print(f"  avg_sum={r[3]}  min_sum={r[4]}  max_sum={r[5]}")

    # contested-only stats to see if contested itself is corrupted (~1.0)
    cur.execute(
        f"""
        SELECT COUNT(contested_shot_pct),
               ROUND(AVG(contested_shot_pct),4),
               SUM(CASE WHEN ABS(contested_shot_pct-1.0)<=0.005 THEN 1 ELSE 0 END),
               ROUND(AVG(open_shot_pct),4),
               COUNT(open_shot_pct)
        FROM player_stats_advanced WHERE season IN ({ph})
        """,
        seasons,
    )
    r = cur.fetchone()
    print(f"  contested: n={r[0]} avg={r[1]} count(~1.0)={r[2]}   open: n={r[4]} avg={r[3]}")

    # per-season sum histogram
    for s in seasons:
        cur.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN ABS((contested_shot_pct+open_shot_pct)-1.0)<=0.005 THEN 1 ELSE 0 END),
                   ROUND(AVG(contested_shot_pct+open_shot_pct),4)
            FROM player_stats_advanced
            WHERE season=? AND contested_shot_pct IS NOT NULL AND open_shot_pct IS NOT NULL
            """,
            (s,),
        )
        n, near, avg = cur.fetchone()
        print(f"    {s}: both_nonnull={n}  sum~1.0={near}  avg_sum={avg}")


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    report(cur, EXISTING_SEASONS, "EXISTING seasons (ground truth)")
    report(cur, NEW_SEASONS, "NEW seasons (current / pre-fix)")

    # Sample of existing rows
    ph = ",".join("?" for _ in EXISTING_SEASONS)
    print("\n=== sample existing rows (contested, open, sum) ===")
    cur.execute(
        f"""SELECT season, player_name, contested_shot_pct, open_shot_pct,
                   ROUND(contested_shot_pct+open_shot_pct,4)
            FROM player_stats_advanced
            WHERE season IN ({ph}) AND contested_shot_pct IS NOT NULL AND open_shot_pct IS NOT NULL
            ORDER BY RANDOM() LIMIT 12""",
        EXISTING_SEASONS,
    )
    for row in cur.fetchall():
        print(f"    {row[0]} {row[1]:<26} c={row[2]}  o={row[3]}  sum={row[4]}")
    con.close()


if __name__ == "__main__":
    main()
