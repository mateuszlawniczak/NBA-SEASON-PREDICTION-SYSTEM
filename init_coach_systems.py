"""
Populate coach_system_data with distinct coach_name values from coach_data.
"""

import os
import sqlite3

from init_db import CREATE_COACH_SYSTEM_DATA

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

_EXPECTED_COLS = frozenset({"coach_name", "PR", "system"})


def _ensure_coach_system_schema(cur: sqlite3.Cursor) -> None:
    cur.execute("PRAGMA table_info(coach_system_data)")
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        cur.execute(CREATE_COACH_SYSTEM_DATA)
    elif cols != _EXPECTED_COLS:
        cur.execute("DROP TABLE coach_system_data")
        cur.execute(CREATE_COACH_SYSTEM_DATA)


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    _ensure_coach_system_schema(cur)
    cur.execute(
        """
        INSERT OR IGNORE INTO coach_system_data (coach_name, PR, system)
        SELECT DISTINCT coach_name, NULL, NULL
        FROM coach_data
        WHERE coach_name IS NOT NULL
        """
    )
    inserted = cur.execute("SELECT changes()").fetchone()[0]
    con.commit()
    con.close()
    print(
        f"[COACH SYSTEMS] Successfully initialized {inserted} unique coaches "
        "for manual analysis."
    )


if __name__ == "__main__":
    main()
