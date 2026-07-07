"""
reset_database.py (temporary)
-----------------------------
Hard reset: remove all rows from the live simulation/projection tables; schema
is preserved. Tables missing from the DB are skipped so the reset never fails.
"""

from __future__ import annotations

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

# Live tables the pipeline regenerates each run (see docs/ARCHITECTURE.md).
RESET_TABLES = (
    "player_simulation_pr",
    "projected_team_pr_25_26",
    "team_playoff_pr_25_26",
)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        for table in RESET_TABLES:
            if not _table_exists(con, table):
                print(f"{table}: not found, skipped", flush=True)
                continue
            con.execute(f'DELETE FROM "{table}";')
            con.commit()
            n = con.execute(f'SELECT COUNT(*) FROM "{table}";').fetchone()[0]
            print(f"{table} row count: {n}", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
