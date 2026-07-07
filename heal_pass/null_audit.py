"""Null audit: per-column NULL counts by season.

Prints a column x season matrix of NULL counts for player_stats_advanced
(healed columns) and player_stats_basic (position/gs/years_in_league),
so the three new seasons can be compared against the 2024-25 reference.
"""
import sqlite3
import sys

from config import DB_PATH, NEW_SEASONS, ADV_HEAL_COLS, BASIC_HEAL_COLS

REPORT_SEASONS = NEW_SEASONS + ["2024-25"]


def audit_table(cur, table, cols, seasons):
    # row counts
    counts = {}
    for s in seasons:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE season = ?", (s,))
        counts[s] = cur.fetchone()[0]

    header = f"{'column':<28}" + "".join(f"{s:>12}" for s in seasons)
    print(f"\n=== {table}  (NULL counts by season) ===")
    print(f"{'[row count]':<28}" + "".join(f"{counts[s]:>12}" for s in seasons))
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for col in cols:
        line = f"{col:<28}"
        for s in seasons:
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE season = ? AND {col} IS NULL",
                (s,),
            )
            n = cur.fetchone()[0]
            line += f"{n:>12}"
        print(line)


def main():
    seasons = REPORT_SEASONS
    if "--all" in sys.argv:
        from config import ALL_SEASONS
        seasons = ALL_SEASONS
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    audit_table(cur, "player_stats_advanced", ADV_HEAL_COLS, seasons)
    audit_table(cur, "player_stats_basic", BASIC_HEAL_COLS, seasons)
    audit_table(cur, "player_stats_basic_playoffs", ["position"], seasons)
    audit_table(cur, "player_stats_advanced_playoffs", ["position"], seasons)
    con.close()


if __name__ == "__main__":
    main()
