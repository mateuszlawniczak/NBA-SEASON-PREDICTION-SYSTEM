"""
Create and populate the playstyle_multipliers reference table in nba_data.db.
Does not alter or drop any other tables.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "nba_data.db"

ROWS = [
    ("Perfect", 1.15),
    ("Heliocentric", 1.05),
    ("Motion", 1.03),
    ("Pace & Space", 1.03),
    ("Elite Balanced", 1.03),
    ("Paint & Pound", 1.02),
    ("Undefined / No Identity", 0.90),
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS playstyle_multipliers (
                playstyle TEXT PRIMARY KEY,
                multiplier REAL NOT NULL
            )
            """
        )
        cur.executemany(
            """
            INSERT OR REPLACE INTO playstyle_multipliers (playstyle, multiplier)
            VALUES (?, ?)
            """,
            ROWS,
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
