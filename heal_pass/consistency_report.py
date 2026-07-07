"""Report 3: consistency proof across all nine seasons (2017-18 -> 2025-26).

Per-season league averages for open_shot_pct, contested_shot_pct, off_reb (from
player_stats_advanced) and gs (from player_stats_basic). A smooth trend across
the 2019-20 / 2020-21 boundary means the new seasons match the old methodology;
a visible step exactly at that boundary means they do not.

Also prints a whole-DB sanity check that every contested_shot_pct and
open_shot_pct is between 0 and 1.
"""
import sqlite3
from config import DB_PATH, ALL_SEASONS


def col_avg(cur, table, col, season):
    cur.execute(
        f"SELECT ROUND(AVG({col}),4), COUNT({col}), COUNT(*) FROM {table} WHERE season=?",
        (season,),
    )
    return cur.fetchone()


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    print("=" * 78)
    print("  CONSISTENCY PROOF - per-season league averages (all nine seasons)")
    print("  Boundary of interest: 2019-20 | 2020-21")
    print("=" * 78)
    header = f"  {'season':<10}{'open_shot':>12}{'contested':>12}{'off_reb':>12}{'gs':>12}"
    print(header)
    print("  " + "-" * 58)
    for s in ALL_SEASONS:
        o = col_avg(cur, "player_stats_advanced", "open_shot_pct", s)
        c = col_avg(cur, "player_stats_advanced", "contested_shot_pct", s)
        r = col_avg(cur, "player_stats_advanced", "off_reb", s)
        g = col_avg(cur, "player_stats_basic", "gs", s)
        marker = "  <== boundary" if s == "2020-21" else ""
        print(f"  {s:<10}{str(o[0]):>12}{str(c[0]):>12}{str(r[0]):>12}{str(g[0]):>12}{marker}")

    print("\n  (coverage: non-null / total per season)")
    for s in ALL_SEASONS:
        o = col_avg(cur, "player_stats_advanced", "open_shot_pct", s)
        c = col_avg(cur, "player_stats_advanced", "contested_shot_pct", s)
        r = col_avg(cur, "player_stats_advanced", "off_reb", s)
        g = col_avg(cur, "player_stats_basic", "gs", s)
        print(f"  {s:<10} open={o[1]}/{o[2]}  contested={c[1]}/{c[2]}  off_reb={r[1]}/{r[2]}  gs={g[1]}/{g[2]}")

    print("\n" + "=" * 78)
    print("  SANITY: contested_shot_pct / open_shot_pct in [0,1] across WHOLE DB")
    print("=" * 78)
    for col in ("contested_shot_pct", "open_shot_pct"):
        cur.execute(
            f"SELECT COUNT(*), ROUND(MIN({col}),4), ROUND(MAX({col}),4), "
            f"SUM(CASE WHEN {col} < 0 OR {col} > 1 THEN 1 ELSE 0 END) "
            f"FROM player_stats_advanced WHERE {col} IS NOT NULL"
        )
        n, mn, mx, oob = cur.fetchone()
        status = "OK" if oob == 0 else f"!!! {oob} OUT OF RANGE !!!"
        print(f"  {col:<22} n={n} min={mn} max={mx}  out_of_[0,1]={oob}  {status}")

    con.close()


if __name__ == "__main__":
    main()
