"""
fix_advanced_positions.py
-------------------------
Copy position from player_stats_basic into player_stats_advanced for rows
where advanced position is missing. Local SQLite only — no NBA API.

Reads: player_stats_basic
Writes: player_stats_advanced.position only
"""

import os
import sqlite3
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

UPDATE_SQL = """
UPDATE player_stats_advanced
SET position = (
    SELECT position
    FROM player_stats_basic
    WHERE player_stats_basic.player_id = player_stats_advanced.player_id
      AND player_stats_basic.season = player_stats_advanced.season
      AND player_stats_basic.team_id = player_stats_advanced.team_id
)
WHERE position IS NULL OR position = ''
"""


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute(UPDATE_SQL)
        con.commit()
        print(
            "[COSMETIC PATCH] Successfully copied player positions from Basic to Advanced table.",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
