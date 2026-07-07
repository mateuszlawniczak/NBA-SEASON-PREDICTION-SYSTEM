"""Final null normalization on the three NEW seasons only.

Counting stats (deflections, opp_fg3_contests_attempts, opp_fg_at_rim_contested):
    remaining NULL -> 0 where the player has gp > 0 in player_stats_basic
    (played but recorded none = zero).

Percentage stats (ts_pct, fg_pct_mid, fg3_pct_corner, fg3_pct_above_break,
    opp_fg_pct_at_rim, opp_fg3_pct_contested, contested_shot_pct, open_shot_pct):
    remaining NULL stays NULL. Never write 0 into a percentage.

Scope: NEW_SEASONS only.
"""
import sqlite3
import sys

from config import DB_PATH, NEW_SEASONS

COUNTING = ["deflections", "opp_fg3_contests_attempts", "opp_fg_at_rim_contested"]
PERCENT = ["ts_pct", "fg_pct_mid", "fg3_pct_corner", "fg3_pct_above_break",
           "opp_fg_pct_at_rim", "opp_fg3_pct_contested", "contested_shot_pct", "open_shot_pct"]


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    ph = ",".join("?" for _ in NEW_SEASONS)

    print("=== Counting stats: NULL -> 0 where gp > 0 ===")
    for col in COUNTING:
        for s in NEW_SEASONS:
            cur.execute(
                f"""
                UPDATE player_stats_advanced
                   SET {col} = 0
                 WHERE season = ?
                   AND {col} IS NULL
                   AND EXISTS (
                       SELECT 1 FROM player_stats_basic b
                        WHERE b.player_id = player_stats_advanced.player_id
                          AND b.season = player_stats_advanced.season
                          AND b.gp > 0
                   )
                """,
                (s,),
            )
            set_n = cur.rowcount
            cur.execute(f"SELECT COUNT(*) FROM player_stats_advanced WHERE season=? AND {col} IS NULL", (s,))
            rem = cur.fetchone()[0]
            print(f"  {col:<28} {s}: set0={set_n}  remaining_null(no gp>0)={rem}")
    con.commit()

    print("\n=== Percentage stats: left NULL (never zeroed). Remaining nulls per new season ===")
    for col in PERCENT:
        line = f"  {col:<28}"
        for s in NEW_SEASONS:
            cur.execute(f"SELECT COUNT(*) FROM player_stats_advanced WHERE season=? AND {col} IS NULL", (s,))
            line += f" {s}={cur.fetchone()[0]}"
        print(line)
    con.close()


if __name__ == "__main__":
    main()
