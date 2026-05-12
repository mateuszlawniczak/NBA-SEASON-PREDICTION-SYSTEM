"""
reset_database.py (temporary)
-----------------------------
Hard reset: remove all rows from simulation tables; schema is preserved.
"""

from __future__ import annotations

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("DELETE FROM player_simulation_pr;")
        con.execute("DELETE FROM team_simulation_pr;")
        con.commit()

        n_player = con.execute("SELECT COUNT(*) FROM player_simulation_pr;").fetchone()[0]
        n_team = con.execute("SELECT COUNT(*) FROM team_simulation_pr;").fetchone()[0]

        print(f"player_simulation_pr row count: {n_player}", flush=True)
        print(f"team_simulation_pr row count: {n_team}", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
