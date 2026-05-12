"""
init_progression_curves.py
---------------------------
Creates isolated reference table player_progression_curves (NBA aging curve
multipliers). Does not alter any other tables. Safe re-run: DROP + CREATE.
"""

import os
import sqlite3
import sys
from typing import Iterable

DB_PATH = os.path.join(os.path.dirname(__file__), "nba_data.db")

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POSITIONS = ("PG", "SG", "SF", "PF", "C")
BIGS: set[str] = {"PF", "C"}
DRAFT_AGE_TIERS = ("Under 21", "21+")
YEARS = range(2, 16)  # Years 2 through 15 inclusive

DROP_SQL = "DROP TABLE IF EXISTS player_progression_curves;"

CREATE_SQL = """
CREATE TABLE player_progression_curves (
    position          TEXT    NOT NULL,
    draft_age_tier    TEXT    NOT NULL,
    year_in_league    INTEGER NOT NULL,
    growth_multiplier REAL    NOT NULL,
    PRIMARY KEY (position, draft_age_tier, year_in_league)
);
"""

INSERT_SQL = """
INSERT INTO player_progression_curves (
    position, draft_age_tier, year_in_league, growth_multiplier
) VALUES (?, ?, ?, ?);
"""


def growth_multiplier(draft_age_tier: str, year_in_league: int, position: str) -> float:
    is_big = position in BIGS

    if draft_age_tier == "Under 21":
        if 2 <= year_in_league <= 3:
            return 1.08 if is_big else 1.12
        if 4 <= year_in_league <= 5:
            return 1.06
        if 6 <= year_in_league <= 9:
            return 1.00
        if 10 <= year_in_league <= 12:
            return 0.92 if is_big else 0.95
        if 13 <= year_in_league <= 15:
            return 0.85

    elif draft_age_tier == "21+":
        if 2 <= year_in_league <= 3:
            return 1.05
        if 4 <= year_in_league <= 6:
            return 1.00
        if 7 <= year_in_league <= 9:
            return 0.94
        if 10 <= year_in_league <= 15:
            return 0.80

    raise ValueError(
        f"Unsupported combo: tier={draft_age_tier!r}, year={year_in_league}, position={position!r}"
    )


def all_rows() -> Iterable[tuple[str, str, int, float]]:
    for tier in DRAFT_AGE_TIERS:
        for pos in POSITIONS:
            for y in YEARS:
                yield pos, tier, y, growth_multiplier(tier, y, pos)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(DROP_SQL + CREATE_SQL)
        conn.executemany(INSERT_SQL, list(all_rows()))
        conn.commit()
    finally:
        conn.close()

    print("Progression curves table created successfully.")
    print()
    print("Sample: PG | Under 21 | Year 2–15")
    print("-" * 44)
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """
            SELECT year_in_league, growth_multiplier
            FROM player_progression_curves
            WHERE position = 'PG' AND draft_age_tier = 'Under 21'
            ORDER BY year_in_league;
            """
        )
        for y, m in cur.fetchall():
            pct = (m - 1.0) * 100
            sign = "+" if pct >= 0 else ""
            print(f"  Year {y:2d}  |  multiplier {m:.2f}  ({sign}{pct:.0f}%)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
