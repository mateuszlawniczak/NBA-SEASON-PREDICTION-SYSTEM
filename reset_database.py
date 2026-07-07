"""
reset_database.py (temporary)
-----------------------------
Hard reset: remove all rows from the live simulation/projection tables for a
given target season; schema is preserved. Tables missing from the DB are skipped.
"""

from __future__ import annotations

import os
import sqlite3
import sys

from season_utils import season_arg_parser, parse_season_pair

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main() -> None:
    parser = season_arg_parser("Clear live projection/simulation rows for one season.")
    args = parser.parse_args()
    pair = parse_season_pair(args.season)

    season_deletes = (
        ("player_simulation_pr", "season = ?", (pair.source,)),
        ("player_projected_pr", "season = ?", (pair.source,)),
        ("player_experience_pr", "season = ?", (pair.source,)),
        ("final_simulation_pr", "season = ?", (pair.source,)),
        ("ULTIMATE_PR", "season = ?", (pair.target,)),
        ("ultimate_playoff_pr", "season = ?", (pair.target,)),
        ("player_durability_profiles", "season = ?", (pair.target,)),
        ("team_projection", "season = ?", (pair.target,)),
        ("team_playoff_projection", "season = ?", (pair.target,)),
        ("rookie_projection", "season = ?", (pair.target,)),
        ("team_coaches", "season = ?", (pair.target,)),
        ("simulation_results", "season = ?", (pair.target,)),
    )

    con = sqlite3.connect(DB_PATH)
    try:
        for table, clause, params in season_deletes:
            if not _table_exists(con, table):
                print(f"{table}: not found, skipped", flush=True)
                continue
            con.execute(f'DELETE FROM "{table}" WHERE {clause};', params)
            con.commit()
            n = con.execute(f'SELECT COUNT(*) FROM "{table}";').fetchone()[0]
            print(f"{table} row count: {n}", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
