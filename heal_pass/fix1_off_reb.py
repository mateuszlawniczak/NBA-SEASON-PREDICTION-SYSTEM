"""Fix 1: off_reb in player_stats_advanced for the three NEW seasons.

Method (archive/migrate_player_stats_advanced.py): off_reb = round(reb - def_reb, 1)
where reb comes from player_stats_basic joined on (player_id, season) and
def_reb is the advanced row's own value. Local compute, no API.

Scope: NEW_SEASONS only. --verify first prints a consistency check that the
EXISTING six seasons already satisfy off_reb == round(reb - def_reb, 1)
(so applying this recipe to the new seasons matches the existing methodology).
"""
import sqlite3
import sys

from config import DB_PATH, NEW_SEASONS, EXISTING_SEASONS


def verify_existing(cur):
    ph = ",".join("?" for _ in EXISTING_SEASONS)
    cur.execute(
        f"""
        SELECT psa.season,
               COUNT(*) AS n,
               SUM(CASE WHEN psa.off_reb IS NOT NULL
                         AND ABS(psa.off_reb - ROUND(psb.reb - psa.def_reb, 1)) <= 0.1
                        THEN 1 ELSE 0 END) AS matches,
               SUM(CASE WHEN psa.off_reb IS NULL THEN 1 ELSE 0 END) AS off_reb_null
          FROM player_stats_advanced psa
          JOIN player_stats_basic psb
            ON psb.player_id = psa.player_id
           AND psb.season    = psa.season
           AND psb.team_id   = psa.team_id
         WHERE psa.season IN ({ph})
           AND psa.def_reb IS NOT NULL
           AND psb.reb IS NOT NULL
         GROUP BY psa.season
         ORDER BY psa.season
        """,
        EXISTING_SEASONS,
    )
    print("  [verify] EXISTING seasons: off_reb vs round(reb-def_reb,1) (team-matched join)")
    print(f"    {'season':<10}{'rows':>8}{'match<=0.1':>12}{'off_reb_null':>14}")
    for s, n, m, nulls in cur.fetchall():
        print(f"    {s:<10}{n:>8}{m:>12}{nulls:>14}")


def apply_new(con):
    cur = con.cursor()
    per_season = {}
    for s in NEW_SEASONS:
        cur.execute("SELECT COUNT(*) FROM player_stats_advanced WHERE season=? AND off_reb IS NOT NULL", (s,))
        before = cur.fetchone()[0]
        cur.execute(
            """
            UPDATE player_stats_advanced
               SET off_reb = ROUND(
                   (
                       SELECT psb.reb
                         FROM player_stats_basic psb
                        WHERE psb.player_id = player_stats_advanced.player_id
                          AND psb.season    = player_stats_advanced.season
                          AND psb.reb IS NOT NULL
                   ) - player_stats_advanced.def_reb,
                   1
               )
             WHERE season = ?
               AND player_stats_advanced.def_reb IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM player_stats_basic psb
                    WHERE psb.player_id = player_stats_advanced.player_id
                      AND psb.season    = player_stats_advanced.season
                      AND psb.reb IS NOT NULL
               )
            """,
            (s,),
        )
        updated = cur.rowcount
        cur.execute("SELECT COUNT(*) FROM player_stats_advanced WHERE season=? AND off_reb IS NOT NULL", (s,))
        after = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM player_stats_advanced WHERE season=? AND off_reb IS NULL", (s,))
        remaining = cur.fetchone()[0]
        per_season[s] = (updated, before, after, remaining)
    con.commit()
    return per_season


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    verify_existing(cur)
    if "--verify-only" in sys.argv:
        con.close()
        return
    print("\n  [apply] NEW seasons off_reb = round(reb - def_reb, 1)")
    per = apply_new(con)
    print(f"    {'season':<10}{'updated':>10}{'nonnull_before':>16}{'nonnull_after':>15}{'still_null':>12}")
    for s in NEW_SEASONS:
        u, b, a, r = per[s]
        print(f"    {s:<10}{u:>10}{b:>16}{a:>15}{r:>12}")
    con.close()


if __name__ == "__main__":
    main()
